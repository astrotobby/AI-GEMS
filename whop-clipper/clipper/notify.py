"""Telegram updates through your existing bot (Bot API, no extra dependency beyond requests)."""
from __future__ import annotations

import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id
        self.last_error = ""

    def send(self, text: str) -> bool:
        log.info("notify: %s", text.replace("\n", " | ")[:300])
        if not (self.token and self.chat_id):
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text[:4000],
                                    "disable_web_page_preview": True}, timeout=30)
            if not r.ok:
                log.warning("telegram rejected message: %s %s", r.status_code, r.text[:200])
                # 401/404 = the bot token is wrong; 400/403 = the token works but the chat id doesn't
                self.last_error = ("TELEGRAM_BOT_TOKEN is invalid (copy it again from @BotFather)"
                                   if r.status_code in (401, 404) else
                                   f"TELEGRAM_CHAT_ID rejected: {r.text[:120]} "
                                   "(send your bot a message first, then use chat.id from getUpdates)")
            return r.ok
        except requests.RequestException as e:  # notifications must never break the pipeline
            log.warning("telegram failed: %s", e)
            return False

    def recent_chats(self) -> list[tuple[int, str]]:
        """Chats that recently messaged the bot, so the right TELEGRAM_CHAT_ID can be picked."""
        try:
            r = requests.get(f"https://api.telegram.org/bot{self.token}/getUpdates", timeout=30)
            updates = r.json().get("result", []) if r.ok else []
        except (requests.RequestException, ValueError):
            return []
        seen: dict[int, str] = {}
        for u in updates:
            msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
            chat = msg.get("chat") or {}
            if "id" in chat:
                seen[chat["id"]] = "@" + chat["username"] if chat.get("username") else (
                    chat.get("title") or chat.get("first_name") or chat.get("type", ""))
        return list(seen.items())

    def send_video(self, path: Path, caption: str) -> None:
        """Send the clip itself so it can be reviewed on the phone (Bot API limit: 50 MB)."""
        if not (self.token and self.chat_id) or not path.exists() or path.stat().st_size > 49_000_000:
            self.send(caption)
            return
        try:
            with path.open("rb") as fh:
                r = requests.post(f"https://api.telegram.org/bot{self.token}/sendVideo",
                                  data={"chat_id": self.chat_id, "caption": caption[:1000],
                                        "supports_streaming": "true"},
                                  files={"video": (path.name, fh, "video/mp4")}, timeout=300)
            if not r.ok:
                self.send(caption)
        except requests.RequestException as e:
            log.warning("telegram video failed: %s", e)
            self.send(caption)
