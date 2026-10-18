import email
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import digest  # noqa: E402
from digest import Item  # noqa: E402
from ebooklib import epub  # noqa: E402


def toronto(hour, minute=0):
    return datetime(2026, 10, 9, hour, minute, tzinfo=digest.TZ)


def item(i, title="A good thing happened", group="science", snippet="It went well. Everyone is happy.", url=None):
    return Item(i, title, url or f"https://example.com/{i}", "Example", group, snippet)


class FakeClient:
    """Stands in for GeminiClient: returns canned text from generate()."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class DueEdition(unittest.TestCase):
    def due(self, hour, minute=0):
        edition, start = digest.due_edition(toronto(hour, minute))
        return edition, f"{start:%Y-%m-%d %H:%M}"

    def test_slot_boundaries(self):
        self.assertEqual(self.due(6), ("am", "2026-10-09 06:00"))
        self.assertEqual(self.due(19, 59), ("am", "2026-10-09 06:00"))
        self.assertEqual(self.due(20), ("pm", "2026-10-09 20:00"))
        self.assertEqual(self.due(23, 59), ("pm", "2026-10-09 20:00"))

    def test_before_six_belongs_to_yesterdays_evening_edition(self):
        self.assertEqual(self.due(5, 59), ("pm", "2026-10-08 20:00"))
        self.assertEqual(self.due(0, 5), ("pm", "2026-10-08 20:00"))

    def test_hours_late_runs_still_map_to_an_edition(self):
        # Real GitHub start times seen for this repo (Toronto): none fell in a 6-8 / 20-22 window.
        self.assertEqual(self.due(10, 14), ("am", "2026-10-09 06:00"))
        self.assertEqual(self.due(22, 24), ("pm", "2026-10-09 20:00"))
        self.assertEqual(self.due(1, 41), ("pm", "2026-10-08 20:00"))
        self.assertEqual(self.due(16, 5), ("am", "2026-10-09 06:00"))


class ParseJudgements(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(digest.parse_judgements('[{"id": 1}]'), [{"id": 1}])

    def test_code_fence(self):
        self.assertEqual(digest.parse_judgements('```json\n[{"id": 2}]\n```'), [{"id": 2}])

    def test_wrapped_in_object(self):
        self.assertEqual(digest.parse_judgements('{"items": [{"id": 3}]}'), [{"id": 3}])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            digest.parse_judgements("sorry, I cannot")


class Categories(unittest.TestCase):
    def test_local_only_from_local_feeds(self):
        self.assertEqual(digest._category_for(item(1, group="local"), "local"), "local")
        self.assertEqual(digest._category_for(item(1, group="science"), "local"), "science")
        self.assertEqual(digest._category_for(item(1, group="general"), "local"), digest.FALLBACK_CATEGORY)

    def test_model_category_wins_over_feed_group(self):
        self.assertEqual(digest._category_for(item(1, group="local"), "science"), "science")

    def test_bad_category_falls_back_to_feed_group(self):
        self.assertEqual(digest._category_for(item(1, group="health"), "sports"), "health")
        self.assertEqual(digest._category_for(item(1, group="local"), ""), digest.FALLBACK_CATEGORY)


class KeywordFallback(unittest.TestCase):
    def test_grim_headline_rejected(self):
        self.assertFalse(digest.keyword_judge(item(1, title="Man charged after deadly crash"))["positive"])

    def test_preselected_feed_passes(self):
        verdict = digest.keyword_judge(item(1, group="general", title="Owl rescued by neighbours"))
        self.assertTrue(verdict["positive"])
        self.assertGreaterEqual(verdict["score"], digest.MIN_SCORE)

    def test_plain_item_without_positive_words_is_dropped(self):
        kept, rejected = digest.judge_all([item(1, title="Council meets on Tuesday")], None)
        self.assertEqual((kept, len(rejected)), ([], 1))

    def test_local_feed_story_must_name_a_nearby_place(self):
        wire = item(1, group="local", title="Poilievre unveils five-point steel plan", snippet="The plan helps workers grow.")
        local = item(2, group="local", title="Kitchener market reopens", snippet="A restored hall opens.")
        self.assertFalse(digest.keyword_judge(wire)["positive"])
        self.assertTrue(digest.keyword_judge(local)["positive"])

    def test_summary_is_two_sentences(self):
        verdict = digest.keyword_judge(item(1, group="general", snippet="One. Two. Three."))
        self.assertEqual(verdict["summary"], "One. Two.")


class JudgeAll(unittest.TestCase):
    def test_gemini_verdicts_are_applied(self):
        items = [item(0, group="local"), item(1), item(2), item(3)]
        reply = json.dumps(
            [
                {"id": 0, "positive": True, "score": 5, "category": "local", "summary": "Local win."},
                {"id": 1, "positive": False, "score": 1, "category": "science", "summary": "Grim."},
                {"id": 2, "positive": True, "score": 2, "category": "science", "summary": "Meh."},
                {"id": 99, "positive": True, "score": 5, "category": "science", "summary": "Not ours."},
            ]
        )
        kept, rejected = digest.judge_all(items, FakeClient(reply))
        self.assertEqual([i.id for i in kept], [0])
        self.assertEqual(kept[0].category, "local")
        self.assertEqual(kept[0].summary, "Local win.")
        self.assertEqual(sorted(i.id for i in rejected), [1, 2])
        # id 3 was never judged, so it is neither kept nor rejected (stays eligible next time)

    def test_failed_batch_falls_back_to_keywords(self):
        items = [item(0, group="general", title="Owl rescued by neighbours")]
        kept, _ = digest.judge_all(items, FakeClient(digest.GeminiError("boom")))
        self.assertEqual(len(kept), 1)

    def test_malformed_json_is_retried_once(self):
        items = [item(0)]
        good = json.dumps([{"id": 0, "positive": True, "score": 4, "category": "science", "summary": "Fine."}])
        client = FakeClient("not json", good)
        kept, _ = digest.judge_all(items, client)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(client.prompts), 2)

    def test_auth_error_is_not_swallowed(self):
        with self.assertRaises(digest.GeminiAuthError):
            digest.judge_all([item(0)], FakeClient(digest.GeminiAuthError("bad key")))

    def test_score_is_clamped_and_summary_cleaned(self):
        reply = json.dumps([{"id": 0, "positive": True, "score": 99, "category": "health", "summary": "<b>Hi</b>  there"}])
        kept, _ = digest.judge_all([item(0)], FakeClient(reply))
        self.assertEqual((kept[0].score, kept[0].summary), (5, "Hi there"))

    def test_prompt_contains_items_as_data(self):
        prompt = digest.build_prompt([item(7, title='Quote "marks" {braces}')])
        self.assertIn('"id": 7', prompt)
        self.assertIn("{braces}", prompt)


class GeminiHttp(unittest.TestCase):
    @staticmethod
    def response(status, body=None, text=""):
        r = mock.Mock()
        r.status_code = status
        r.text = text or json.dumps(body or {})
        r.json.return_value = body or {}
        return r

    def client(self):
        return digest.GeminiClient("key", ["model-a", "model-b"], min_interval=0)

    def test_success_extracts_text_and_skips_thoughts(self):
        body = {"candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "[]"}]}}]}
        with mock.patch("digest.requests.post", return_value=self.response(200, body)):
            self.assertEqual(self.client().generate("p"), "[]")

    def test_daily_quota_moves_to_next_model(self):
        quota = self.response(429, text="GenerateRequestsPerDayPerProjectPerModel-FreeTier exceeded")
        ok = self.response(200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
        client = self.client()
        with mock.patch("digest.requests.post", side_effect=[quota, ok]) as post:
            self.assertEqual(client.generate("p"), "[]")
        self.assertIn("model-b", post.call_args_list[1].args[0])
        self.assertEqual(client.models, ["model-b"])

    def test_missing_model_moves_to_next_model(self):
        ok = self.response(200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
        with mock.patch("digest.requests.post", side_effect=[self.response(404), ok]):
            self.assertEqual(self.client().generate("p"), "[]")

    def test_bad_key_is_fatal(self):
        bad = self.response(400, text="API key not valid. Please pass a valid API key.")
        with mock.patch("digest.requests.post", return_value=bad):
            with self.assertRaises(digest.GeminiAuthError):
                self.client().generate("p")

    def test_per_minute_429_waits_then_retries(self):
        slow = self.response(429, {"error": {"details": [{"retryDelay": "3s"}]}}, text="RESOURCE_EXHAUSTED PerMinute")
        ok = self.response(200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
        with mock.patch("digest.requests.post", side_effect=[slow, ok]), mock.patch("digest.time.sleep") as sleep:
            self.assertEqual(self.client().generate("p"), "[]")
        sleep.assert_any_call(3.0)

    def test_blocked_response_raises_batch_error(self):
        with mock.patch("digest.requests.post", return_value=self.response(200, {"promptFeedback": {}})):
            with self.assertRaises(digest.GeminiError):
                self.client().generate("p")


class Collect(unittest.TestCase):
    def fake_fetch(self, feeds):
        def fetch(group, feed, now_utc):
            if feed["name"] == "dead":
                raise ValueError("no items")
            return [Item(0, t, f"https://x/{feed['name']}/{t}", feed["name"], group) for t in feed["titles"]]

        return fetch

    def test_dedupe_seen_failure_and_interleave(self):
        feeds = {
            "local": [
                {"name": "a", "url": "", "titles": ["A1", "A2", "A3", "Shared story"]},
                {"name": "b", "url": "", "titles": ["B1", "shared story!", "B2"]},
                {"name": "dead", "url": ""},
            ],
            "science": [{"name": "c", "url": "", "titles": ["C1", "A1", "C2"]}],
        }
        seen = {"https://x/a/A2"}
        with mock.patch("digest.fetch_feed", self.fake_fetch(feeds)):
            items, failed = digest.collect(feeds, datetime.now(timezone.utc), seen)
        titles = [i.title for i in items]
        self.assertEqual(titles, ["A1", "B1", "A3", "B2", "Shared story", "C1", "C2"])
        self.assertEqual(len(failed), 1)
        self.assertEqual([i.id for i in items], list(range(len(items))))

    def test_group_cap(self):
        feeds = {"science": [{"name": "c", "url": "", "titles": [f"T{n}" for n in range(100)]}]}
        with mock.patch("digest.fetch_feed", self.fake_fetch(feeds)):
            items, _ = digest.collect(feeds, datetime.now(timezone.utc), set())
        self.assertEqual(len(items), digest.GROUP_CAP)


class Text(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(digest.clean_text("<p>Fish &amp; chips</p>\n  <b>now</b>"), "Fish & chips now")
        self.assertEqual(digest.clean_text("Story. The post Story appeared first on Site."), "Story.")
        self.assertTrue(digest.clean_text("word " * 200, 50).endswith("…"))
        self.assertEqual(digest.clean_text(None), "")


class Epub(unittest.TestCase):
    def test_book_is_valid_and_escaped(self):
        sections = {
            "local": [Item(0, "Kitchener <script>alert(1)</script> wins", "https://e.com/a?x=1&y=2", "CBC", "local", summary="Two & two.", score=5, category="local")],
            "science": [Item(1, "Telescope sees farther", "https://e.com/b", "NASA", "science", summary="Far.", score=4, category="science")],
        }
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(digest, "OUT_DIR", Path(tmp)):
            path = digest.build_epub(sections, toronto(6), "am")
            self.assertEqual(path.name, "good-news-2026-10-09-am.epub")
            book = epub.read_epub(str(path))
            self.assertEqual(book.get_metadata("DC", "title")[0][0], "Good News, Oct 9, Morning")
            docs = {d.get_name(): d.get_content().decode() for d in book.get_items_of_type(9)}
            self.assertIn("local.xhtml", docs)
            self.assertIn("science.xhtml", docs)
            self.assertNotIn("<script>", docs["local.xhtml"])
            self.assertIn("&lt;script&gt;", docs["local.xhtml"])
            self.assertIn("x=1&amp;y=2", docs["local.xhtml"])
            self.assertEqual([t.title for t in book.toc], ["Local: Golden Horseshoe", "Science"])
            # Reading order: contents page, then Local, then the rest.
            order = [book.get_item_with_id(i[0]).get_name() for i in book.spine]
            self.assertEqual(order, ["nav.xhtml", "local.xhtml", "science.xhtml"])

    def test_evening_title(self):
        self.assertEqual(digest.edition_title(toronto(20), "pm"), "Good News, Oct 9, Evening")


class SelectStories(unittest.TestCase):
    def test_ranked_limited_and_ordered(self):
        kept = []
        for n in range(10):
            i = item(n, group="science")
            i.category, i.score = "science", 3 + (n % 3)
            kept.append(i)
        local = item(50, group="local")
        local.category, local.score = "local", 3
        kept.append(local)
        sections = digest.select_stories(kept)
        self.assertEqual(list(sections), ["local", "science"])
        self.assertEqual(len(sections["science"]), digest.MAX_PER_CATEGORY)
        scores = [s.score for s in sections["science"]]
        self.assertEqual(scores, sorted(scores, reverse=True))


class State(unittest.TestCase):
    def test_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(digest, "STATE_PATH", Path(tmp) / "s" / "state.json"):
            state = digest.load_state()
            self.assertEqual(state, {"seen": {}, "sent": []})
            state["seen"] = {"https://old": "2026-09-01", "https://new": "2026-10-08"}
            state["sent"] = ["2026-10-08-pm"]
            digest.save_state(state, toronto(6))
            again = digest.load_state()
            self.assertEqual(again["seen"], {"https://new": "2026-10-08"})
            self.assertEqual(again["sent"], ["2026-10-08-pm"])


class SendEmail(unittest.TestCase):
    def test_message_shape(self):
        env = {"SMTP_USER": "sender@gmail.com", "SMTP_APP_PASSWORD": "abcd efgh ijkl mnop", "KINDLE_EMAIL": "me_123@kindle.com"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, env):
            book = Path(tmp) / "good-news-2026-10-09-am.epub"
            book.write_bytes(b"PK-fake-epub")
            with mock.patch("digest.smtplib.SMTP_SSL") as smtp_cls:
                digest.send_to_kindle(book, "Good News, Oct 9, Morning")
        smtp = smtp_cls.return_value.__enter__.return_value
        smtp_cls.assert_called_once_with("smtp.gmail.com", 465, timeout=60)
        smtp.login.assert_called_once_with("sender@gmail.com", "abcdefghijklmnop")  # spaces stripped
        msg = smtp.send_message.call_args.args[0]
        self.assertEqual((msg["From"], msg["To"], msg["Subject"]), ("sender@gmail.com", "me_123@kindle.com", "Good News, Oct 9, Morning"))
        attachment = next(msg.iter_attachments())
        self.assertEqual(attachment.get_filename(), "good-news-2026-10-09-am.epub")
        self.assertEqual(attachment.get_content_type(), "application/epub+zip")
        self.assertEqual(attachment.get_payload(decode=True), b"PK-fake-epub")


class MainFlow(unittest.TestCase):
    """Drives main() with the network, clock and mailbox replaced."""

    ENV = {"SMTP_USER": "s@gmail.com", "SMTP_APP_PASSWORD": "pw", "KINDLE_EMAIL": "k@kindle.com"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.items = [Item(n, f"Neighbours rescue owl {n}", f"https://e.com/{n}", "GNN", "general", "They saved it. It flew away.") for n in range(3)]
        for patcher in (
            mock.patch.object(digest, "STATE_PATH", self.tmp / "state.json"),
            mock.patch.object(digest, "OUT_DIR", self.tmp / "out"),
            mock.patch("digest.collect", side_effect=lambda *a, **k: ([Item(**i.__dict__) for i in self.items], [])),
            mock.patch("digest.load_feeds", return_value={"general": []}),
            mock.patch("digest.send_to_kindle"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.send = digest.send_to_kindle

    def run_main(self, hour, *argv, event=None, ai=False):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return toronto(hour, 10)

        env = dict(self.ENV, **({"GITHUB_EVENT_NAME": event} if event else {}))
        with mock.patch.object(digest, "datetime", Frozen), mock.patch.dict(os.environ, env):
            if not event:
                os.environ.pop("GITHUB_EVENT_NAME", None)
            os.environ.pop("GEMINI_API_KEY", None)
            return digest.main([*argv] if ai else ["--no-ai", *argv])

    def test_scheduled_run_sends_records_then_skips_the_retry(self):
        self.assertEqual(self.run_main(6, event="schedule"), 0)
        self.assertEqual(self.send.call_count, 1)
        state = json.loads(digest.STATE_PATH.read_text())
        self.assertEqual(state["sent"], ["2026-10-09-am"])
        self.assertEqual(sorted(state["seen"]), [f"https://e.com/{n}" for n in range(3)])
        # The 11:00 UTC cron in EDT lands at 7:10 Toronto: inside the window, already sent.
        self.assertEqual(self.run_main(7, event="schedule"), 0)
        self.assertEqual(self.send.call_count, 1)

    def test_late_scheduled_run_still_sends_once(self):
        # GitHub started the 6 a.m. job at 10:14 a.m. in practice.
        self.assertEqual(self.run_main(10, event="schedule"), 0)
        self.assertEqual(self.send.call_args.args[1], "Good News, Oct 9, Morning")
        self.run_main(13, event="schedule")
        self.assertEqual(self.send.call_count, 1)

    def test_overnight_run_delivers_the_previous_evening_edition(self):
        # The 8 p.m. job started at 2:10 a.m. the next day.
        self.run_main(2, event="schedule")
        self.assertEqual(self.send.call_args.args[1], "Good News, Oct 8, Evening")
        self.assertEqual(json.loads(digest.STATE_PATH.read_text())["sent"], ["2026-10-08-pm"])
        self.run_main(5, event="schedule")
        self.assertEqual(self.send.call_count, 1)

    def test_evening_edition_is_separate_from_morning(self):
        self.run_main(6, event="schedule")
        self.run_main(20, event="schedule")
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(json.loads(digest.STATE_PATH.read_text())["sent"], ["2026-10-09-am", "2026-10-09-pm"])

    def test_failed_send_is_not_recorded_so_the_retry_can_run(self):
        self.send.side_effect = OSError("smtp down")
        with self.assertRaises(OSError):
            self.run_main(6, event="schedule")
        self.assertFalse(digest.STATE_PATH.exists())
        self.send.side_effect = None
        self.run_main(7, event="schedule")
        self.assertEqual(self.send.call_count, 2)

    def test_manual_run_sends_but_does_not_touch_state(self):
        self.assertEqual(self.run_main(15), 0)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[1], "Good News, Oct 9, Morning")
        self.assertFalse(digest.STATE_PATH.exists())

    def test_no_gemini_key_falls_back_to_keywords_instead_of_failing(self):
        self.assertEqual(self.run_main(15, ai=True), 0)
        self.assertEqual(self.send.call_count, 1)

    def test_dry_run_never_sends(self):
        self.assertEqual(self.run_main(15, "--dry-run"), 0)
        self.send.assert_not_called()
        self.assertTrue(any((self.tmp / "out").glob("*.epub")))

    def test_missing_secrets_fail_loudly(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            class Frozen(datetime):
                @classmethod
                def now(cls, tz=None):
                    return toronto(15)

            with mock.patch.object(digest, "datetime", Frozen):
                self.assertEqual(digest.main(["--no-ai"]), 1)
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
