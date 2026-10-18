# Whop clipper

End-to-end automation for Whop content-reward / clipping campaigns:

```
Whop bounties ─► score & pick ─► download source ─► transcribe ─► pick best moment
     ▲                                                               │
     │                                                   render 9:16 captioned clip
     │                                                               │
 submit links ◄── wait for view threshold ◄── post (YouTube/IG) ◄── (review on Telegram)
```

Every step reports to your Telegram bot. A JSON ledger (`state/ledger.json`) records each
job, so runs resume where they stopped and nothing is ever posted or submitted twice.

## What each stage does

| Stage | How |
|---|---|
| **Discover** | Reads `state/campaigns.json`, which the scheduled **Whop connector task** refreshes every 4 hours (see `CONNECTOR_TASK.md`). With `whop_via = "api"` and a Whop *user* token it calls `GET /bounties` directly instead. |
| **Score** | Hard filters: open, spots left, budget left, open to your country, under your per-user limit, a clipping campaign, no physical/on-location work. Then points for net payout, budget health, spots vs. pending queue, provided source footage, short-form, low view threshold; minus for livestream proof or several clips per submission. `python -m clipper scan` shows the reasons. |
| **Source** | Google Drive files/folders via `gdown`; YouTube/Twitch/Kick/etc. via `yt-dlp`. Only footage the campaign links is used. |
| **Clip** | `faster-whisper` word timestamps → Claude picks the strongest standalone moment and writes title/caption/hashtags (falls back to a free transcript heuristic without an API key) → `ffmpeg` renders 1080×1920, blurred-fill for landscape sources, big word-by-word captions, a hook banner for the first 3 seconds, loudness-normalised audio. |
| **Review** | `mode = "review"` (default): the clip is sent to Telegram as a video and waits for `approve <job>`. `mode = "auto"`: no stop. |
| **Post** | Composio: `YOUTUBE_MULTIPART_UPLOAD_VIDEO` (Shorts) and `INSTAGRAM_POST_IG_USER_MEDIA` + `..._PUBLISH` (Reels). Only to platforms the campaign accepts. Re-checks the campaign is still open first. |
| **Track** | Reads views through Composio until the brief's threshold (e.g. "1,000 views") is met; gives up after 14 days. |
| **Submit** | The job moves to `awaiting_submission`; the Whop connector task submits it as you (links + source timestamps) and marks it `submitted`. In `api` mode it calls `POST /bounty_submissions` itself. |

### The livestream rule (read this)

Right now every clipping campaign open to Kenya asks for a **livestream proving you
made/posted the clip yourself**. That is the campaign's anti-bot check, and software cannot
honestly satisfy it. For those campaigns the pipeline does all the work (clip, post, wait for
views), then stops at `needs_livestream` and sends you a Telegram checklist (links, view
counts, clip file, timestamps). You go live on Whop, submit, then mark it `done <job>`.
Campaigns without that rule go all the way through by themselves.

Campaigns that are skipped on purpose: engagement farming (likes/follows/DMs), anything needing
you physically somewhere, long-form edits with scripts/charts, and campaigns asking for many
clips in a single submission.

## Setup

1. **Secrets** (GitHub repo → Settings → Secrets and variables → Actions, or a local `.env`):

   | Secret | Where to get it |
   |---|---|
   | `WHOP_TOKEN` | Not needed in the default connector mode. Only for `whop_via = "api"`: a Whop **user** OAuth token (business API keys can't submit). |
   | `COMPOSIO_API_KEY` | composio.dev → Settings → API keys |
   | `COMPOSIO_USER_ID` | The Composio user id your YouTube/Instagram are connected under |
   | `TELEGRAM_BOT_TOKEN` | Your existing bot (BotFather). Your chat id is set as `telegram_chat` in `config.toml` (it isn't a secret). |
   | `ANTHROPIC_API_KEY` | Optional: better moment picking and captions |
   | `YTDLP_COOKIES_TXT` | Optional: contents of a `cookies.txt` exported from a logged-in browser. YouTube blocks most cloud IPs; Google Drive sources don't need this. |

2. **Targets**: in `config.toml`, fill `account_id` for each `[[targets]]` with the Composio
   connected-account id (`ca_...`, Composio dashboard → Connected accounts). Connected now:
   YouTube *Astro Tobby* and *DIJITOL NOMAD*, Instagram *@astr_osignal* and *@astro_tobby*.
   TikTok and Facebook aren't connected in Composio yet; connect them there and add a target if
   you want them (the publisher currently supports YouTube and Instagram).

3. **Check it**: every push to the feature branch runs `python -m clipper check`, which tests each
   connection without posting (and sends the report to Telegram). Run it any time locally too.

4. **Run it**: the workflow `.github/workflows/whop-clipper.yml` runs every 4 hours. From
   GitHub → Actions → *Whop clipper* → *Run workflow* you can send `scan`, `status`,
   `approve <job>`, `skip <job>`, `done <job>`, or `run` with *dry run* ticked.

   Locally:

   ```bash
   cd whop-clipper
   pip install -r requirements.txt   # plus ffmpeg on PATH
   python -m clipper scan            # rank campaigns, no side effects
   python -m clipper run --dry-run   # pick + download + clip, never post or submit
   python -m clipper run             # the real thing
   python -m clipper status
   python -m clipper clip some.mp4 --bounty-file bounty.json   # test the clipper alone
   python -m pytest -q
   ```

Start in `review` mode for the first few clips, then set the repo variable
`CLIPPER_MODE=auto` (or `mode = "auto"` in `config.toml`) once you trust the output.

## Ground rules baked in

- Uses only the footage a campaign provides, keeps the speaker's meaning, and adds the
  required @mentions. It never invents claims or stats.
- Posts only to your own real accounts and never buys or fakes views. Campaigns reject
  botted engagement and Whop can ban the account.
- Checks that a campaign is still open before posting and again before submitting.
