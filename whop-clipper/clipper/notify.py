"""Telegram updates through your existing bot (Bot API, no extra dependency beyond requests)."""
from __future__ import annotations

import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def send(self, text: str) -> None:
        log.info("notify: %s", text.replace("\n", " | ")[:300])
        if not (self.token and self.chat_id):
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": text[:4000],
                                "disable_web_page_preview": True}, timeout=30)
        except requests.RequestException as e:  # notifications must never break the pipeline
            log.warning("telegram failed: %s", e)

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
