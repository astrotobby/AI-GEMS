"""`python -m clipper check`: prove every credential works without posting or submitting anything."""
from __future__ import annotations

import logging
import platform

from . import publish
from .config import Settings
from .notify import Telegram
from .whop import Whop

log = logging.getLogger(__name__)


def _line(ok: bool, name: str, detail: str) -> str:
    return f"{'✅' if ok else '❌'} {name}: {detail}"


def whop(cfg: Settings) -> str:
    w = Whop(cfg.whop_token)
    bounties = w.open_bounties(cfg.country, limit=100)
    if not bounties:
        # a user token sees the whole public bounty board (dozens open to KE); an account/company
        # API key only sees that account's own bounties, and Whop rejects submissions from it
        raise RuntimeError("token works but sees 0 open bounties - this looks like a business/account "
                           "API key. WHOP_TOKEN must be a *user* OAuth token (see README)")
    mine = w._req("GET", "/bounty_submissions", params={"first": 1}).get("data", [])
    return f"{len(bounties)}+ open bounties visible from {cfg.country}; submissions endpoint OK ({len(mine)} recent)"


def composio(cfg: Settings) -> list[str]:
    c = publish.Composio(cfg.composio_api_key, cfg.composio_user_id)
    out = []
    # list the connected accounts so their ca_... ids can go into config.toml
    try:
        accts = c.sdk.connected_accounts.list(
            user_ids=[cfg.composio_user_id] if cfg.composio_user_id else None,
            toolkit_slugs=["youtube", "instagram"], statuses=["ACTIVE"])
        for a in getattr(accts, "items", []) or []:
            tk = getattr(getattr(a, "toolkit", None), "slug", "?")
            out.append(f"   • {tk}: {a.id}")
    except Exception as e:
        out.append(f"   (could not list connected accounts: {e})")

    platforms = {t["platform"] for t in cfg.targets} or {"youtube", "instagram"}
    if "youtube" in platforms:
        yt = next((t for t in cfg.targets if t["platform"] == "youtube"), {})
        try:
            data = c.run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet"}, yt.get("account_id"))
            out.insert(0, _line(True, "YouTube", f"posting as {publish.find(data, 'title')}"))
        except Exception as e:
            out.insert(0, _line(False, "YouTube", str(e)[:200]))
    if "instagram" in platforms:
        ig = next((t for t in cfg.targets if t["platform"] == "instagram"), {})
        try:
            data = c.run("INSTAGRAM_GET_USER_INFO", {"ig_user_id": "me", "fields": "id,username,account_type"},
                         ig.get("account_id"))
            out.insert(1, _line(True, "Instagram", f"posting as @{publish.find(data, 'username')} "
                                                   f"({publish.find(data, 'account_type') or 'business/creator'})"))
        except Exception as e:
            out.insert(1, _line(False, "Instagram", str(e)[:200]))
    return out


def anthropic(cfg: Settings) -> str:
    import anthropic as sdk

    m = sdk.Anthropic(api_key=cfg.anthropic_api_key).models.retrieve(cfg.claude_model)
    return f"{m.id} reachable"


def run(cfg: Settings) -> bool:
    lines, ok = [], True

    def attempt(name, fn, required=True):
        nonlocal ok
        try:
            res = fn(cfg)
            if isinstance(res, list):
                lines.extend(res)
                ok &= not any(r.startswith("❌") for r in res)
            else:
                lines.append(_line(True, name, res))
        except Exception as e:
            lines.append(_line(False, name, str(e)[:300]))
            ok &= not required

    attempt("Whop", whop)
    attempt("Composio", composio)
    if cfg.anthropic_api_key:
        attempt("Claude", anthropic, required=False)
    else:
        lines.append("➖ Claude: no key, using the free moment picker")

    lines.append(f"mode: {cfg.mode} · country: {cfg.country} · python {platform.python_version()}")
    report = "🔌 Whop clipper connection check\n" + "\n".join(lines)
    print(report)
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(_line(False, "Telegram", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing"))
        return False
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    sent = tg.send(report)
    print(_line(sent, "Telegram", "report delivered to your chat" if sent else tg.last_error))
    return ok and sent
