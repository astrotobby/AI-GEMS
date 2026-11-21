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

## Daily schedule (Kenya time)

| When | What |
|---|---|
| 05:00 | Pick today's videos from the freshest campaign list: **2 clipping + 2 UGC**, best-scoring campaigns first (a campaign is reused only if its brief allows several posts). Make all four. |
| **19:00** | 1 clipping + 1 UGC go live (11:00 New York - the campaigns target US viewers) |
| **22:30** | the other clipping + UGC go live (14:30 New York) |
| every 4h | track views, submit what's ready, re-try anything that failed, catch up a missed slot |
| ~23:15 | Telegram summary: what went live, and why anything is missing |

Routing: **clipping → YouTube Astro Tobby (@astrotobyc)**, **UGC → YouTube DIJITOL NOMAD**, everything
→ **Instagram @astr_osignal** (only on platforms each campaign accepts). YouTube channels are matched
by title against your own Google sign-in, Instagram/TikTok by name among your Composio connections,
see `[[targets]]` in `config.toml`.

If a day comes up short, the only expected reason is **no eligible campaign**: none open to Kenya,
none accepting YouTube/Instagram, or every one needs a real person on camera, long-form editing or
footage that isn't provided. Temporary errors (YouTube quota, a slow Instagram upload) are retried on
the next runs; posting gets 8 attempts before it gives up.

**UGC videos** are made from the brief: a script (Claude when `ANTHROPIC_API_KEY` has credit or `CLAUDE_CODE_OAUTH_TOKEN` is set, a plain
template otherwise), a free Piper voiceover, and a 9:16 slideshow with big captions; the product
link and required @mentions go in the caption. Briefs that need a real face/voice or forbid AI are
skipped.

## What each stage does

| Stage | How |
|---|---|
| **Discover** | The GitHub workflow reads the campaign snapshot refreshed by your authenticated Whop MCP connector task. It fails visibly if the snapshot is stale; the connector must refresh and commit it before each scheduled run. |
| **Score** | Hard filters: open, spots left, budget left, open to your country, under your per-user limit, a clipping campaign, no physical/on-location work. Then points for net payout, budget health, spots vs. pending queue, provided source footage, short-form, low view threshold; minus for livestream proof or several clips per submission. `python -m clipper scan` shows the reasons. |
| **Source** | Google Drive files/folders via `gdown`; YouTube/Twitch/Kick/etc. via `yt-dlp`. Only footage the campaign links is used. |
| **Clip** | `faster-whisper` word timestamps → Claude picks the strongest standalone moment and writes title/caption/hashtags (falls back to a free transcript heuristic without an API key) → `ffmpeg` renders 1080×1920, blurred-fill for landscape sources, big word-by-word captions, a hook banner for the first 3 seconds, loudness-normalised audio. |
| **Review** | Current config is `mode = "auto"`: clips move straight to scheduled publishing; review mode is optional and disabled. |
| **Post** | YouTube Shorts: the YouTube Data API with **your own Google OAuth app** (own daily quota, never Composio). Before each upload the credentials are checked to belong to the configured channel. Instagram Reels / TikTok: Composio. Only to platforms the campaign accepts. Re-checks the campaign is still open first. |
| **Track** | Reads views (YouTube Data API; Composio for Instagram/TikTok) until the brief's threshold (e.g. "1,000 views") is met; gives up after 14 days. |
| **Submit** | The pipeline queues complete submission data (post URLs, source timestamps and public MP4 asset URL). The signed-in Whop MCP connector uploads/attaches the file and submits idempotently. For livestream-required jobs, it submits only after Whop records your genuine stream as ended. |

### The livestream rule (read this)

Some campaigns require a **genuine proof livestream** at submission. Whop does not let the
automation start or fake this stream. The clipper publishes the video, tracks views, then waits
at `needs_livestream`. You must complete the real proof stream in Whop; after Whop records it
as ended, the next scheduled run automatically finds your existing attempt, uploads/attaches the
MP4 and post URLs, and submits it. No manual `done` command or connector task is needed.
Campaigns without a livestream go fully automatically, including file upload and submission.

Campaigns whose livestream must show you *making* the clip from start to finish are skipped:
the bot made it, so that stream can't honestly be done.

Campaigns that are skipped on purpose: engagement farming (likes/follows/DMs), anything needing
you physically somewhere, long-form edits with scripts/charts, and campaigns asking for many
clips in a single submission.

## Setup

1. **Secrets** (GitHub repo → Settings → Secrets and variables → Actions, or a local `.env`):

   | Secret | Where to get it |
   |---|---|
   | `WHOP_TOKEN` | Not used in connector mode. The saved GitHub token currently returns 401 and must not be used for submissions. Whop MCP signs in as the worker and handles submit permissions. |
   | `COMPOSIO_API_KEY` | composio.dev → Settings → API keys |
   | `COMPOSIO_USER_ID` | The Composio user id your Instagram/TikTok are connected under |
   | `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` | Your Google Cloud OAuth client (type *Desktop app*, YouTube Data API v3 enabled) |
   | `YOUTUBE_REFRESH_TOKEN_ASTRO_TOBBY`, `YOUTUBE_REFRESH_TOKEN_DIJITOL_NOMAD` | One refresh token per channel: only the ~103-character value starting `1//`. See *YouTube sign-in* below. |
   | `TELEGRAM_BOT_TOKEN` | Your existing bot (BotFather). Your chat id is set as `telegram_chat` in `config.toml` (it isn't a secret). |
   | `ANTHROPIC_API_KEY` | Optional: better moment picking and captions |
   | `CLAUDE_CODE_OAUTH_TOKEN` | Optional: same, paid by your Claude Pro/Max plan instead of API credit. Run `claude setup-token` on your computer and paste the token |
   | `UGC_AFRICA_API_KEY` | Optional second campaign source: a `UGSK_live_...` key from developer.ugc.africa -> Console (see *UGC Africa* below) |
   | `YTDLP_COOKIES_TXT` | Optional: contents of a `cookies.txt` exported from a logged-in browser. YouTube blocks most cloud IPs; Google Drive sources don't need this. |

2. **Targets**: `[[targets]]` in `config.toml` name each YouTube channel (`channel = "Astro Tobby"`)
   and Instagram/TikTok account (`username = ...`). Instagram and TikTok must be connected in
   Composio (`python -m clipper connect instagram`); YouTube needs only the secrets above.

   **YouTube sign-in** (once per channel). Either run `python get_youtube_token.py` on your computer
   and save the token it prints as `YOUTUBE_REFRESH_TOKEN_<CHANNEL>`, or run the *YouTube auth* workflow
   (GitHub → Actions), which keeps the credentials in the run cache. Shortcut: a tokens file of the form
   `{"client_id", "client_secret", "accounts": [{"channel", "refresh_token"}, ...]}` can be pasted whole
   into the single secret `YOUTUBE_REFRESH_TOKEN`; each channel picks its own entry. Two things in
   Google Cloud decide whether this keeps working:

   - **OAuth consent screen → Publishing status must be "In production"**. While it says "Testing",
     every refresh token expires after 7 days and uploads stop with `invalid_grant`.
   - **API uploads from a project that has not passed YouTube's API audit are locked to private.**
     The clipper detects this, tells you on Telegram, and never uploads the same clip twice; request the
     audit ("YouTube API Services - Audit and Quota Extension Form") to get public uploads.
   - Quota: 10,000 units/day per Google project, 1,600 per upload - about 6 uploads a day shared by
     both channels when they use the same OAuth client.

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

## UGC Africa (second campaign source)

With `UGC_AFRICA_API_KEY` set, the same pipeline also takes **clip campaigns from [ugc.africa](https://ugc.africa)**
next to Whop (`python -m clipper ugc` shows what it sees; `check` tests the key). Their creator flow is
fully API-driven, so these jobs need no human step:

```
discover (CLIP_CAMPAIGN, raw footage, open to KE) -> score -> download -> clip -> post (YouTube + Instagram)
   -> reserve a slot just before posting -> submit the post link for the slot -> UGC Africa verifies views and pays per 1,000
```

- Only `CLIP_CAMPAIGN` + `RAW_FOOTAGE` campaigns are taken (advertiser footage we cut from). Ready-made assets that must be
  posted unchanged are not clipping jobs; change `ugc_campaign_types` in `config.toml` to widen it.
- Campaigns carry required hashtags (e.g. `#ad`); they are added to every post and the submission says the disclosure is included.
- No view threshold and no livestream: the job goes from posted straight to `submitted`. The slot is reserved right before
  posting and kept if posting fails, so a retry never reserves twice (idempotency keys `reserve-<job>` / `submit-<job>`).
- If either source is down, the other keeps working and you get one Telegram warning a day.
- Withdrawing earnings from UGC Africa needs KYC in their app (done by you, not by the pipeline).

## Ground rules baked in

- Uses only the footage a campaign provides, keeps the speaker's meaning, and adds the
  required @mentions. It never invents claims or stats.
- Posts only to your own real accounts and never buys or fakes views. Campaigns reject
  botted engagement and Whop can ban the account.
- Checks that a campaign is still open before posting and again before submitting.
