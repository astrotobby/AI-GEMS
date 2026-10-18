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
    if cfg.whop_via != "api":
        from .pipeline import Pipeline
        n = len(Pipeline(cfg).campaigns())  # raises if the snapshot is missing or stale
        return f"connector mode: {n} open campaigns in the latest snapshot from your Whop connector"
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

    # what this API key can see, so missing connections (e.g. ElevenLabs) are obvious
    try:
        seen = {}
        for users in ([cfg.composio_user_id] if cfg.composio_user_id else [], None):
            for a in getattr(c.sdk.connected_accounts.list(user_ids=users or None, account_type="ALL",
                                                           limit=100), "items", []) or []:
                tk = getattr(getattr(a, "toolkit", None), "slug", "?")
                seen.setdefault(tk, set()).add(f"{getattr(a, 'status', '?')}{'' if users else '*'}")
        out.append("   connections visible to COMPOSIO_API_KEY (* = other user): "
                   + ", ".join(f"{k}[{'/'.join(sorted(v))}]" for k, v in sorted(seen.items())))
        voice = "ELEVENLABS_API_KEY" if cfg.elevenlabs_api_key else c.account_for("elevenlabs")
        out.append(("✅ ElevenLabs voice: " + str(voice)) if voice else
                   "⚠️ ElevenLabs voice: add an ELEVENLABS_API_KEY secret - UGC uses the offline voice until then")
    except Exception as e:
        out.append(f"   (could not list connections: {str(e)[:150]})")

    # every configured target must resolve to a real connected account
    for t in cfg.targets or [{"platform": "youtube"}, {"platform": "instagram"}]:
        label = f"{t['platform'].title()} ({'/'.join(t.get('kinds', ['all']))})"
        try:
            r = publish.resolve(c, dict(t))
            if t["platform"] == "youtube":
                data = c.run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet"}, r.get("account_id"))
                who = publish.find(data, "title")
            else:
                data = c.run("INSTAGRAM_GET_USER_INFO", {"ig_user_id": "me", "fields": "id,username"},
                             r.get("account_id"))
                who = "@" + str(publish.find(data, "username"))
            out.insert(0, _line(True, label, f"posting as {who} [{r.get('account_id') or 'default'}]"))
        except Exception as e:
            out.insert(0, _line(False, label, str(e)[:200]))
    return out


def anthropic(cfg: Settings) -> str:
    # exercise the exact structured-output request the clipper makes, on a tiny transcript
    from .highlights import Sentence, with_claude

    sents = [Sentence(0, 4, "Here's the one rule nobody tells you about betting."),
             Sentence(4, 12, "Most people lose because they chase parlays instead of tracking every single pick."),
             Sentence(12, 25, "Bet singles, keep a log, and walk away when the numbers say so.")]
    pick = with_claude(sents, {"title": "test", "description": "Clip the strongest moment."}, 10, 25,
                       cfg.anthropic_api_key, cfg.claude_model, oauth_token=cfg.claude_oauth_token)
    return f"{cfg.claude_model} picked sentences {pick['start_index']}-{pick['end_index']}: \"{pick['hook']}\""


def plan_token(tok: str) -> str:
    """Say what kind of token CLAUDE_CODE_OAUTH_TOKEN holds - by its public prefix only, never the value."""
    if not tok:
        return "➖ Claude plan: no CLAUDE_CODE_OAUTH_TOKEN"
    if tok.startswith("sk-ant-api"):
        return ("❌ Claude plan: CLAUDE_CODE_OAUTH_TOKEN holds an API key (sk-ant-api...), which bills API "
                "credit. Run `claude setup-token` and paste the sk-ant-oat... token it prints")
    if tok.startswith("sk-ant-oat"):
        return ("ℹ️ Claude plan: token is a setup-token (sk-ant-oat...). If Claude Code says 'credit balance', "
                "it was made while logged in with an Anthropic Console account - run `claude /logout`, log in "
                "with your Claude Pro account, then `claude setup-token` again")
    return "⚠️ Claude plan: CLAUDE_CODE_OAUTH_TOKEN doesn't look like a `claude setup-token` token"


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
    if cfg.anthropic_api_key or cfg.claude_oauth_token:
        try:
            lines.append(_line(True, "Claude", anthropic(cfg)))
        except Exception as e:
            from .highlights import llm_error
            lines.append("⚠️ " + llm_error(e) + " - using the free fallbacks")
    else:
        lines.append("➖ Claude: no key, using the free moment picker")
    lines.append(plan_token(cfg.claude_oauth_token))

    lines.append("✅ Stock b-roll: Pexels" if cfg.pexels_api_key else
                 "➖ Stock b-roll: off (optional PEXELS_API_KEY) - UGC uses product screenshots")
    lines.append(f"mode: {cfg.mode} · country: {cfg.country} · python {platform.python_version()}")
    report = "🔌 Whop clipper connection check\n" + "\n".join(lines)
    print(report)
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        print(_line(False, "Telegram", "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing"))
        return False
    tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
    sent = tg.send(report)
    print(_line(sent, "Telegram", "report delivered to your chat" if sent else tg.last_error))
    if not sent and tg.last_error.startswith("TELEGRAM_CHAT_ID"):
        # say which mistake it is without printing either secret
        bot_id = cfg.telegram_bot_token.split(":", 1)[0]
        if cfg.telegram_chat_id == bot_id:
            print("   TELEGRAM_CHAT_ID equals the number at the start of TELEGRAM_BOT_TOKEN - that is the "
                  "bot's own id, not yours. Get yours from @userinfobot.")
        elif "can't send messages to the bot" in tg.last_error:
            print("   TELEGRAM_CHAT_ID belongs to a bot (a different one from this bot). "
                  "Use your personal id from @userinfobot.")
        chats = tg.recent_chats()
        print("   Chats that have messaged this bot (put the right number in TELEGRAM_CHAT_ID):"
              if chats else "   No one has messaged this bot recently - open it in Telegram, send 'hi', re-run.")
        for cid, who in chats:
            print(f"   • {cid}  {who}")
    return ok and sent
