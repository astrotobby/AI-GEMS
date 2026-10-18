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

    # clip style
    min_clip_seconds: int = 20
    max_clip_seconds: int = 60
    whisper_model: str = "small"
    caption_font: str = "DejaVu Sans"
    claude_model: str = "claude-opus-5-5"

    # publishing targets: list of {"platform": "youtube"|"instagram", "account_id": "...", "ig_user_id": "..."}
    targets: list[dict] = field(default_factory=list)
    youtube_privacy: str = "public"
    youtube_category_id: str = "24"  # Entertainment

    # paths
    work_dir: Path = ROOT / "work"
    ledger_path: Path = ROOT / "state" / "ledger.json"
    campaigns_path: Path = ROOT / "state" / "campaigns.json"
    # your personal Telegram chat id (not a secret); used when TELEGRAM_CHAT_ID is unset or wrong
    telegram_chat: str = ""

    # secrets (env only)
    whop_token: str = ""
    composio_api_key: str = ""
    composio_user_id: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


SECRET_FIELDS = {
    "whop_token", "composio_api_key", "composio_user_id",
    "anthropic_api_key", "telegram_bot_token", "telegram_chat_id",
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
    s.telegram_bot_token = secret("TELEGRAM_BOT_TOKEN")
    s.telegram_chat_id = secret("TELEGRAM_CHAT_ID")
    bot_id = s.telegram_bot_token.split(":", 1)[0]
    if s.telegram_chat and (not s.telegram_chat_id or s.telegram_chat_id == bot_id):
        s.telegram_chat_id = str(s.telegram_chat)
    if os.environ.get("CLIPPER_MODE"):
        s.mode = os.environ["CLIPPER_MODE"]
    return s
