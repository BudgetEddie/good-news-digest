# Good News Digest

Collects news from free RSS feeds, has Gemini keep only the positive stories, bundles them into an EPUB and emails it to your Kindle at 6:00 a.m. and 8:00 p.m. Toronto time. GitHub Actions runs it, so nothing needs to be on your computer.

```
feeds.json ─► digest.py ─► Gemini (positive? score, category, summary) ─► EPUB ─► SMTP ─► @kindle.com
```

## One-time setup

1. **Kindle.** Amazon → Manage Your Content and Devices → Preferences → Personal Document Settings.
   Copy your Send-to-Kindle address (`…@kindle.com`) and add your sending address to *Approved Personal Document E-mail List*.
2. **Sending account.** Use a dedicated Gmail address. Turn on two-step verification and create an app password.
3. **Gemini key.** Create a free key in [Google AI Studio](https://aistudio.google.com/).
4. **GitHub.** Create a **private** repository, push this folder, then add four secrets under Settings → Secrets and variables → Actions:

   | Secret | Value |
   | --- | --- |
   | `GEMINI_API_KEY` | the AI Studio key |
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

GitHub cron is UTC and ignores daylight saving, so the workflow fires at 10:00, 11:00, 00:00 and 01:00 UTC. `digest.py` only sends when Toronto time is 6:00–7:59 (morning) or 20:00–21:59 (evening), and only once per edition. That gives three properties:

- Clock change (Nov 1): the right cron entry takes over by itself.
- Late GitHub start: a run delayed past 6:00 still sends.
- Failed run: the next cron entry an hour later retries it (during EDT).

Manual runs ignore the window, send immediately, and do not touch `state.json`.

## Tuning

- **Model.** Defaults to `gemini-3.5-flash`, then `gemini-3.5-flash-lite` if the first is missing or out of daily quota. Override with a repository *variable* (not secret) `GEMINI_MODELS`, e.g. `gemini-3.8-flash,gemini-3.5-flash-lite`. Check `--list-models` and Google's [deprecations page](https://ai.google.dev/gemini-api/docs/deprecations); names change.
- **Prompt.** `PROMPT` in `digest.py`. If grim stories slip through, add two or three example rejections there rather than raising `MIN_SCORE`.
- **Volume.** `MAX_PER_CATEGORY`, `MAX_LOCAL`, `GROUP_CAP`, `BATCH_SIZE`.
- **Free-tier pace.** Gemini calls are spaced `GEMINI_MIN_INTERVAL` seconds apart (default 6). A per-minute 429 is waited out; a daily-quota 429 moves to the next model. If every model fails for a batch, that batch falls back to the keyword filter and the log says so. A rejected API key fails the run instead, so you hear about it.

## Known gaps

- No usable RSS found for KitchenerToday, the University of Waterloo, Wilfrid Laurier or the University of Guelph. St. Catharines/Niagara is covered by Brock News and CBC Hamilton, because the Torstar Niagara papers answer automated requests with HTTP 429. Re-test these URLs occasionally; if one starts working, add it to `feeds.json`.
- GuelphToday and CityNews carry national wire stories; Gemini is told to file those under a topical section rather than Local, so the Local section may be short on quiet days.
- Feeds that only expose headlines give Gemini little to summarise; prefer sources with a snippet.
