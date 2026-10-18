# Good News Digest

Collects news from free RSS feeds, has Gemini keep only the positive stories, bundles them into an EPUB and emails it to your Kindle at 6:00 a.m. and 8:00 p.m. Toronto time. GitHub Actions runs it, so nothing needs to be on your computer.

```
feeds.json ─► digest.py ─► Gemini (positive? score, category, summary) ─► EPUB ─► SMTP ─► @kindle.com
```

## One-time setup

1. **Kindle.** Amazon → Manage Your Content and Devices → Preferences → Personal Document Settings.
   Copy your Send-to-Kindle address (`…@kindle.com`) and add your sending address to *Approved Personal Document E-mail List*.
2. **Sending account.** Use a dedicated Gmail address. Turn on two-step verification and create an app password.
3. **Gemini key (optional).** Create a free key in [Google AI Studio](https://aistudio.google.com/). Skip it to use the keyword filter instead (see below).
4. **GitHub.** Create a **private** repository, push this folder, then add four secrets under Settings → Secrets and variables → Actions:

   | Secret | Value |
   | --- | --- |
   | `GEMINI_API_KEY` | the AI Studio key (optional) |
   | `SMTP_USER` | the sending Gmail address |
   | `SMTP_APP_PASSWORD` | the Gmail app password |
   | `KINDLE_EMAIL` | your `…@kindle.com` address |

5. Actions → *good-news-digest* → **Run workflow** to send one edition now. After that the schedule takes over.

## Trying it locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python digest.py --headlines          # what the feeds returned, no AI
.venv/bin/python digest.py --no-ai --dry-run    # keyword filter, writes out/*.epub, sends nothing
```

With your Gemini key exported in your own terminal (`export GEMINI_API_KEY=…`):

```bash
.venv/bin/python digest.py --list-models        # models your key can use
.venv/bin/python digest.py --dry-run            # real filter, writes out/*.epub, sends nothing
```

Open the EPUB in Apple Books or the Kindle app before trusting a change to the prompt or feeds. Run the tests with `.venv/bin/python -m unittest discover -s tests`.

## Files

| File | Purpose |
| --- | --- |
| `feeds.json` | Every feed URL, grouped. Add or swap sources here, no code changes. |
| `digest.py` | Fetch → dedupe → Gemini → select → EPUB → email. Settings are constants at the top. |
| `.github/workflows/digest.yml` | The four cron entries and the job. |
| `state/state.json` | Stories already delivered and editions already sent. The job commits it back. |

## How scheduling works

GitHub cron is UTC-only, ignores daylight saving, and starts scheduled jobs late. In this repo's first nine days it started them 2.5 to 9.5 hours late, so no fixed send window can work. Instead, `digest.py` asks "which edition is due?": the 6:00 edition from 6:00 a.m., the 8:00 p.m. edition from 8:00 p.m. (until 6:00 the next morning). Any scheduled run sends the due edition unless `state.json` says it already went out.

- Clock change (Nov 1): nothing to adjust, the due edition is computed in Toronto time.
- Late GitHub start: the edition is late, never skipped.
- Failed run: the next of the four daily cron entries retries it.

Editions therefore arrive when GitHub gets round to it, not at 6:00 and 20:00 sharp. If that matters, have an external timer call the workflow's `workflow_dispatch` at the exact times, which GitHub starts promptly, and keep the cron entries as a backup.

Manual runs send immediately, whatever the time, and do not touch `state.json`.

## Running without Gemini

Leave `GEMINI_API_KEY` unset (or run with `--no-ai`) and the digest uses a keyword filter instead: headlines containing words like *killed*, *charged* or *crash* are dropped, and stories need positive words (or a pre-filtered good-news source) to score high enough. Summaries are the first two sentences of the publisher's own snippet. A story from a local feed must name a nearby place to appear under Local.

It costs nothing and needs no Google account, but it is blunter than Gemini: expect the odd grim or dull story, and a thinner Local section. The `general` feeds (Good News Network, Positive News, Reasons to Be Cheerful) are already positive, so those hold up best. A wrong or revoked key still fails the run rather than silently downgrading.

## Tuning

- **Model.** Defaults to `gemini-3.5-flash`, then `gemini-3.5-flash-lite` if the first is missing or out of daily quota. Override with a repository *variable* (not secret) `GEMINI_MODELS`, e.g. `gemini-3.8-flash,gemini-3.5-flash-lite`. Check `--list-models` and Google's [deprecations page](https://ai.google.dev/gemini-api/docs/deprecations); names change.
- **Prompt.** `PROMPT` in `digest.py`. If grim stories slip through, add two or three example rejections there rather than raising `MIN_SCORE`.
- **Volume.** `MAX_PER_CATEGORY`, `MAX_LOCAL`, `GROUP_CAP`, `BATCH_SIZE`.
- **Free-tier pace.** Gemini calls are spaced `GEMINI_MIN_INTERVAL` seconds apart (default 6). A per-minute 429 is waited out; a daily-quota 429 moves to the next model. If every model fails for a batch, that batch falls back to the keyword filter and the log says so.

## Known gaps

- No usable RSS found for KitchenerToday, the University of Waterloo, Wilfrid Laurier or the University of Guelph. St. Catharines/Niagara is covered by Brock News and CBC Hamilton, because the Torstar Niagara papers answer automated requests with HTTP 429. Re-test these URLs occasionally; if one starts working, add it to `feeds.json`.
- GuelphToday and CityNews carry national wire stories; Gemini is told to file those under a topical section rather than Local, so the Local section may be short on quiet days.
- Feeds that only expose headlines give Gemini little to summarise; prefer sources with a snippet.
