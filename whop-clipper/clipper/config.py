"""Settings: config.toml for behaviour, environment variables for secrets."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # pipeline
    mode: str = "review"  # "review" = stop before publishing and ask on Telegram; "auto" = end to end
    country: str = "KE"
    max_active_campaigns: int = 2
    clips_per_campaign: int = 1
    min_score: float = 40.0
    allow_livestream_campaigns: bool = True  # still produced, but submission is handed to you
    goal_types: list[str] = field(default_factory=lambda: ["clipping", "ugc_content"])
    # "connector": campaigns come from / submissions go through the Whop connector in a scheduled
    # Claude task, via state/campaigns.json and the ledger. "api": call Whop directly with WHOP_TOKEN
    # (needs a *user* OAuth token).
    whop_via: str = "connector"
    campaigns_max_age_hours: int = 12

    # daily schedule: how many videos of each kind per day, and the local go-live times.
    # Each slot gets an even share (2 per kind over 2 slots = 1 clipping + 1 UGC per slot).
    daily_quota: dict = field(default_factory=lambda: {"clipping": 2, "ugc": 2})
    slots: list[str] = field(default_factory=lambda: ["08:20", "14:00"])
    timezone: str = "Africa/Nairobi"
    max_per_campaign_per_day: int = 2  # repeat posts only for campaigns that allow several

    # clip style
    min_clip_seconds: int = 20
    max_clip_seconds: int = 60
    whisper_model: str = "small"
    caption_font: str = "DejaVu Sans"
    claude_model: str = "claude-opus-5-5"
    ugc_voice_id: str = "EXAVITQu4vr4xnSDxMaL"  # ElevenLabs "Sarah" - natural, conversational

    # publishing targets: {"platform": "youtube"|"instagram"|"tiktok", "kinds": [...], and one of
    # "channel" (YouTube channel title), "username" (Instagram) or "account_id" (Composio ca_...)}
    targets: list[dict] = field(default_factory=list)
    youtube_privacy: str = "public"
    youtube_category_id: str = "24"  # Entertainment

    # paths
    work_dir: Path = ROOT / "work"
    ledger_path: Path = ROOT / "state" / "ledger.json"
    campaigns_path: Path = ROOT / "state" / "campaigns.json"
    # your personal Telegram chat id (not a secret); overrides TELEGRAM_CHAT_ID when set
    telegram_chat: str = ""

    # secrets (env only)
    whop_token: str = ""
    composio_api_key: str = ""
    composio_user_id: str = ""
    anthropic_api_key: str = ""
    claude_oauth_token: str = ""  # `claude setup-token`: use the Claude Pro/Max plan
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    pexels_api_key: str = ""
    tiktok_client_key: str = ""     # your approved TikTok developer app (for connect tiktok)
    tiktok_client_secret: str = ""
    elevenlabs_api_key: str = ""


SECRET_FIELDS = {
    "whop_token", "composio_api_key", "composio_user_id",
    "anthropic_api_key", "claude_oauth_token", "telegram_bot_token", "telegram_chat_id", "pexels_api_key", "elevenlabs_api_key",
    "tiktok_client_key", "tiktok_client_secret",
}


def secret(name: str) -> str:
    """Read a secret, forgiving the usual copy-paste damage: surrounding whitespace/newlines,
    quotes, a pasted `NAME=` or `Bearer ` prefix."""
    v = os.environ.get(name, "").strip().strip("'\"").strip()
    v = re.sub(r"^[A-Z][A-Z0-9_]*\s*[=:]\s*", "", v)  # e.g. "COMPOSIO_API_KEY=ak_..." or "API_KEY: ..."
    v = re.sub(r"^Bearer\s+", "", v, flags=re.I)
    return v.strip().strip("'\"")


def load(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("CLIPPER_CONFIG", ROOT / "config.toml"))
    s = Settings()
    if path.exists():
        data = tomllib.loads(path.read_text())
        # Sections ([pipeline], [clip], [publish]) are only for readability; keys are flat.
        flat: dict = {}
        for k, v in data.items():
            if isinstance(v, dict):
                flat.update(v)
            else:
                flat[k] = v
        for k, v in flat.items():
            if hasattr(s, k) and k not in SECRET_FIELDS:
                cur = getattr(s, k)
                setattr(s, k, (ROOT / v) if isinstance(cur, Path) else v)
    s.whop_token = secret("WHOP_TOKEN")
    s.composio_api_key = secret("COMPOSIO_API_KEY")
    s.composio_user_id = secret("COMPOSIO_USER_ID")
    s.anthropic_api_key = secret("ANTHROPIC_API_KEY")
    s.claude_oauth_token = secret("CLAUDE_CODE_OAUTH_TOKEN")
    s.telegram_bot_token = secret("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = secret("TELEGRAM_CHAT_ID")
    s.pexels_api_key = secret("PEXELS_API_KEY")
    s.tiktok_client_key = secret("TIKTOK_CLIENT_KEY")
    s.tiktok_client_secret = secret("TIKTOK_CLIENT_SECRET")
    s.elevenlabs_api_key = secret("ELEVENLABS_API_KEY")
    if s.telegram_chat:  # an explicit chat id in config.toml wins over the secret
        s.telegram_chat_id = str(s.telegram_chat)
    if os.environ.get("CLIPPER_MODE"):
        s.mode = os.environ["CLIPPER_MODE"]
    return s
