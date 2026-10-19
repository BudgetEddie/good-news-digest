#!/usr/bin/env python3
"""Good News Website: the digest's feeds and Gemini filter, published as a static site."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import digest
import site_pdf
from digest import TZ, Item

SITE_NAME = "Good News Website"
TAGLINE = "Only the good news, updated every few hours."
# Public address, e.g. https://budgeteddie.github.io/good-news-digest. Only used for share
# previews and the RSS feed; the pages themselves use relative links and work without it.
SITE_URL = (os.environ.get("SITE_URL") or "").rstrip("/")

ARCHIVE_PATH = digest.ROOT / "state" / "site.json"
ASSETS_DIR = digest.ROOT / "site"
PUBLIC_DIR = digest.ROOT / "public"

SITE_MIN_SCORE = 4  # stricter than the Kindle digest's 3: strangers judge the site by its worst story
KEEP_DAYS = 10
MAX_STORIES = 300
JUDGED_KEEP_DAYS = 14  # feeds only offer stories up to 72 hours old, so this never forgets too soon
FEED_ITEMS = 50

CATEGORIES = {  # display order
    "local": ("Local", "📍"),
    "science": ("Science", "🔬"),
    "health": ("Health", "💚"),
    "environment": ("Environment", "🌱"),
    "technology": ("Technology", "💡"),
    "culture": ("Culture", "🎨"),
    "business": ("Business", "🤝"),
}

SITE_RULES = """This digest is a public website, so also reject:
- politics: elections, parties, politicians, and government or policy fights, even when the news is good;
- business news about earnings, funding rounds, deals, stock moves or product launches, unless it clearly improves people's lives;
- sports results, celebrity news, product reviews, shopping deals and sponsored content."""

# Backstop for SITE_RULES, and the only politics filter when there is no Gemini. Errs towards
# dropping: a missing good story costs nothing, a political one on the front page costs trust.
_POLITICAL = re.compile(
    r"\b(elections?|electoral|ballots?|referendum|parliament\w*|senat(?:e|ors?)|congress\w*|legislat\w*|"
    r"premier|prime minister|ministers?|mpp?s?|governor|white house|liberals?|conservatives?|ndp|"
    r"tor(?:y|ies)|republicans?|democrats?|gop|trump|biden|harris|vance|carney|poilievre|ford government)\b",
    re.I,
)

log = logging.getLogger("site")


# ----------------------------------------------------------------------- archive


def load_archive() -> dict:
    try:
        data = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    judged = data.get("judged") if isinstance(data.get("judged"), dict) else {}
    stories = data.get("stories") if isinstance(data.get("stories"), list) else []
    return {"judged": judged, "stories": [s for s in stories if isinstance(s, dict) and s.get("url")]}


def prune(archive: dict, now: datetime) -> None:
    story_cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    judged_cutoff = (now - timedelta(days=JUDGED_KEEP_DAYS)).strftime("%Y-%m-%d")
    archive["stories"] = [s for s in archive["stories"] if s.get("added", "") >= story_cutoff][:MAX_STORIES]
    archive["judged"] = {url: day for url, day in archive["judged"].items() if day >= judged_cutoff}


def save_archive(archive: dict) -> None:
    ARCHIVE_PATH.parent.mkdir(exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(archive, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def is_political(item: Item) -> bool:
    return bool(_POLITICAL.search(f"{item.title} {item.snippet} {item.summary}"))


def to_story(item: Item, now: datetime) -> dict:
    return {
        "id": hashlib.sha1(item.url.encode()).hexdigest()[:12],
        "title": item.title,
        "url": item.url,
        "source": item.source,
        "category": item.category if item.category in CATEGORIES else digest.FALLBACK_CATEGORY,
        "score": item.score,
        "summary": item.summary,
        "published": (item.published or now).astimezone(TZ).isoformat(timespec="seconds"),
        "added": now.isoformat(timespec="seconds"),
    }


def update(archive: dict, feeds: dict, client, now: datetime) -> tuple[int, list[str]]:
    """Fetch, judge the stories not judged before, and add the good ones. Returns (added, failed feeds)."""
    known_urls = set(archive["judged"]) | {s["url"] for s in archive["stories"]}
    known_titles = {digest.norm_title(s["title"]) for s in archive["stories"]}
    candidates, failed = digest.collect(feeds, now.astimezone(timezone.utc), known_urls)
    candidates = [c for c in candidates if digest.norm_title(c.title) not in known_titles]
    log.info("%d new candidates (%d feeds failed)", len(candidates), len(failed))
    if not candidates:
        return 0, failed

    kept, rejected = digest.judge_all(candidates, client, SITE_RULES, fallback=False)
    day = f"{now:%Y-%m-%d}"
    archive["judged"].update({i.url: day for i in kept + rejected})  # unjudged batches are retried next run

    fresh = []
    for item in kept:
        if item.score < SITE_MIN_SCORE:
            continue
        if is_political(item):
            log.info("Dropped as political: %s", item.title)
            continue
        fresh.append(to_story(item, now))
    fresh.sort(key=lambda s: (s["score"], s["published"]), reverse=True)
    archive["stories"] = fresh + archive["stories"]
    log.info("Published %d new stories", len(fresh))
    return len(fresh), failed


# ------------------------------------------------------------------------ render

esc = html.escape


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(TZ)


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt:%M} {'a.m.' if dt.hour < 12 else 'p.m.'}"


def _day_label(day: datetime, now: datetime) -> str:
    if day.date() == now.date():
        return "Today"
    if day.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    return f"{day:%A}, {day:%B} {day.day}"


def _abs(path: str) -> str:
    return f"{SITE_URL}/{path}" if SITE_URL else path


def page(title: str, description: str, path: str, body: str, now: datetime) -> str:
    full_title = SITE_NAME if title == SITE_NAME else f"{title} · {SITE_NAME}"
    canonical = f'<link rel="canonical" href="{esc(_abs(path))}">' if SITE_URL else ""
    og_url = f'<meta property="og:url" content="{esc(_abs(path))}">' if SITE_URL else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(full_title)}</title>
<meta name="description" content="{esc(description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{esc(SITE_NAME)}">
<meta property="og:title" content="{esc(full_title)}">
<meta property="og:description" content="{esc(description)}">
{og_url}
{canonical}
<meta name="theme-color" content="#fff8ec" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#16201d" media="(prefers-color-scheme: dark)">
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="alternate" type="application/rss+xml" title="{esc(SITE_NAME)}" href="feed.xml">
<link rel="stylesheet" href="style.css">
</head>
<body>
<a class="skip" href="#main">Skip to stories</a>
<header class="masthead">
  <div class="wrap masthead-row">
    <a class="brand" href="./"><span class="sun" aria-hidden="true"></span>{esc(SITE_NAME)}</a>
    <nav aria-label="Site">
      <a href="./">Stories</a>
      <a href="about.html">How we pick stories</a>
    </nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="wrap footer">
  <p>Headlines and articles belong to their publishers; every card links to the original.
  Summaries are written by AI from the publisher's own snippet and can contain mistakes.</p>
  <p><a href="about.html">How we pick stories</a> · <a href="feed.xml">RSS feed</a> · Built {now:%B} {now.day}, {now:%Y}</p>
</footer>
<script src="app.js" defer></script>
</body>
</html>
"""


def card(story: dict) -> str:
    label, icon = CATEGORIES.get(story["category"], CATEGORIES[digest.FALLBACK_CATEGORY])
    published = _dt(story["published"])
    url = esc(story["url"], quote=True)
    summary = f'<p class="summary">{esc(story["summary"])}</p>' if story.get("summary") else ""
    return f"""<article class="card" data-category="{esc(story['category'])}">
  <div class="card-meta"><span class="tag tag-{esc(story['category'])}"><span aria-hidden="true">{icon}</span> {esc(label)}</span>
  <time datetime="{esc(story['published'])}">{published:%b} {published.day}</time></div>
  <h3><a href="{url}" target="_blank" rel="noopener">{esc(story['title'])}</a></h3>
  {summary}
  <p class="source">{esc(story['source'])} · <a href="{url}" target="_blank" rel="noopener" aria-label="Read the full story at {esc(story['source'])}">Read the full story</a></p>
</article>"""


def group_by_day(stories: list[dict]) -> dict[str, list[dict]]:
    """Stories by Toronto publish date ("2026-10-18"), newest day first."""
    days: dict[str, list[dict]] = {}
    for story in sorted(stories, key=lambda s: s["published"], reverse=True):
        days.setdefault(_dt(story["published"]).strftime("%Y-%m-%d"), []).append(story)
    return days


def _day(day_key: str) -> datetime:
    return datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=TZ)


def pdf_name(day_key: str) -> str:
    return f"good-news-{day_key}.pdf"


def render_index(stories: list[dict], now: datetime, source_count: int, pdf_day: str | None = None) -> str:
    """pdf_day: the day whose PDF was built as today.pdf, if any; adds the download button."""
    days = group_by_day(stories)

    present = {s["category"] for s in stories}
    chips = ['<button type="button" class="chip" data-filter="all" aria-pressed="true">All</button>']
    for key, (label, icon) in CATEGORIES.items():
        if key in present:
            chips.append(
                f'<button type="button" class="chip" data-filter="{key}" aria-pressed="false">'
                f'<span aria-hidden="true">{icon}</span> {esc(label)}</button>'
            )

    sections = []
    for day_key, day_stories in days.items():
        day_stories.sort(key=lambda s: (s["score"], s["published"]), reverse=True)
        label = _day_label(_day(day_key), now)
        cards = "\n".join(card(s) for s in day_stories)
        sections.append(f'<section class="day"><h2>{esc(label)}</h2>\n<div class="grid">\n{cards}\n</div></section>')

    if sections:
        stories_html = "\n".join(sections)
        stories_html += '\n<p class="empty" hidden>No stories in this category right now. Check back soon.</p>'
    else:
        stories_html = '<p class="empty">The first good news is on its way. Check back in a few hours.</p>'

    updated = f'<time class="updated" datetime="{now.isoformat(timespec="seconds")}">{now:%B} {now.day} at {_clock(now)}</time>'
    download = ""
    if pdf_day:
        day = _day(pdf_day)
        whose = "today's" if day.date() == now.date() else f"{day:%B} {day.day}'s"
        count = len(days.get(pdf_day, []))
        download = (
            f'\n  <p class="download-row"><a class="download" href="today.pdf" download="{pdf_name(pdf_day)}">'
            f'<span aria-hidden="true">⬇</span> Download {whose} news as a PDF</a>'
            f' <span class="download-note">{count} {"story" if count == 1 else "stories"}, ready to print or share</span></p>'
        )
    body = f"""<section class="hero">
  <h1>Good things are happening.</h1>
  <p class="lede">{len(stories)} uplifting stories from the last {KEEP_DAYS} days, picked from {source_count} trusted news sources.
  No politics, no doom, no outrage.</p>
  <p class="stamp">Updated {updated}</p>{download}
</section>
<div class="filters" role="group" aria-label="Filter by topic">
  {''.join(chips)}
</div>
{stories_html}"""
    return page(SITE_NAME, f"{TAGLINE} Uplifting stories from trusted sources, with no politics.", "", body, now)


def render_about(feeds: dict, now: datetime) -> str:
    names = sorted({f["name"] for group in feeds.values() for f in group}, key=str.lower)
    sources = "".join(f"<li>{esc(n)}</li>" for n in names)
    body = (ASSETS_DIR / "about.html").read_text(encoding="utf-8")
    body = body.replace("<!-- SOURCES -->", f'<ul class="sources">{sources}</ul>')
    body = body.replace("<!-- MIN_SCORE -->", str(SITE_MIN_SCORE))
    return page("How we pick stories", "How the Good News Website finds, filters and summarises stories.", "about.html", body, now)


def render_feed(stories: list[dict], now: datetime) -> str:
    items = []
    for s in sorted(stories, key=lambda s: s["published"], reverse=True)[:FEED_ITEMS]:
        label = CATEGORIES.get(s["category"], ("Culture", ""))[0]
        description = f"{s.get('summary', '')} (Source: {s['source']})".strip()
        items.append(
            f"""  <item>
    <title>{esc(s['title'])}</title>
    <link>{esc(s['url'])}</link>
    <guid isPermaLink="true">{esc(s['url'])}</guid>
    <description>{esc(description)}</description>
    <category>{esc(label)}</category>
    <pubDate>{format_datetime(_dt(s['published']))}</pubDate>
  </item>"""
        )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
<channel>
  <title>{esc(SITE_NAME)}</title>
  <link>{esc(SITE_URL or "")}</link>
  <description>{esc(TAGLINE)}</description>
  <language>en</language>
  <lastBuildDate>{format_datetime(now)}</lastBuildDate>
{chr(10).join(items)}
</channel>
</rss>
"""


def render_site(archive: dict, feeds: dict, now: datetime, out: Path = PUBLIC_DIR) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ASSETS_DIR, out, ignore=shutil.ignore_patterns("about.html", ".DS_Store"))
    stories = archive["stories"]
    source_count = len({f["name"] for group in feeds.values() for f in group})
    days = group_by_day(stories)
    pdf_day = next(iter(days), None)  # the newest day with stories: today, or yesterday just after midnight
    if pdf_day:
        site_pdf.build_pdf(out / "today.pdf", days[pdf_day], _day(pdf_day), CATEGORIES, SITE_NAME, SITE_URL)
    (out / "index.html").write_text(render_index(stories, now, source_count, pdf_day), encoding="utf-8")
    (out / "about.html").write_text(render_about(feeds, now), encoding="utf-8")
    (out / "feed.xml").write_text(render_feed(stories, now), encoding="utf-8")
    public = [{k: v for k, v in s.items() if k != "score"} for s in stories]
    (out / "stories.json").write_text(
        json.dumps({"updated": now.isoformat(timespec="seconds"), "stories": public}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log.info("Rendered %d stories into %s", len(stories), out)


# -------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-ai", action="store_true", help="local preview with the keyword filter; does not save the archive")
    p.add_argument("--render-only", action="store_true", help="rebuild public/ from the saved archive without fetching")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    now = datetime.now(TZ)
    feeds = digest.load_feeds()
    archive = load_archive()

    if args.render_only:
        render_site(archive, feeds, now)
        return 0

    client = None
    if not args.no_ai:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            log.error("GEMINI_API_KEY is not set. The public site only publishes AI-checked stories; "
                      "use --no-ai for a local preview.")
            return 1
        models = [m.strip() for m in (os.environ.get("GEMINI_MODELS") or "").split(",") if m.strip()]
        client = digest.GeminiClient(key, models or digest.DEFAULT_MODELS, float(os.environ.get("GEMINI_MIN_INTERVAL") or 6))

    try:
        _, failed = update(archive, feeds, client, now)
    except digest.GeminiAuthError as exc:
        log.error("%s", exc)
        return 1
    for line in failed:
        log.warning("Feed failed: %s", line)
    prune(archive, now)
    if args.no_ai:
        log.info("Preview: archive not saved. Open %s", PUBLIC_DIR / "index.html")
    else:
        save_archive(archive)
    render_site(archive, feeds, now)

    if len(failed) == sum(map(len, feeds.values())):
        log.error("Every feed failed; the site was rebuilt from the archive only.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
