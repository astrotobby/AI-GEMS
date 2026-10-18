"""Minimal Whop REST client for the bounty (content rewards) endpoints."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import requests

BASE = "https://api.whop.com/api/v1"


class WhopError(RuntimeError):
    pass


class Whop:
    def __init__(self, token: str, base: str = BASE):
        if not token:
            raise WhopError("WHOP_TOKEN is not set (it must be a user token: account API keys cannot submit)")
        self.base = base
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(4):
            r = self.s.request(method, f"{self.base}{path}", timeout=60, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            if not r.ok:
                raise WhopError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
            return r.json() if r.content else {}
        raise WhopError(f"{method} {path} kept failing ({r.status_code})")

    # ---- discovery ---------------------------------------------------------
    def open_bounties(self, country: str, limit: int = 300) -> list[dict]:
        out, after = [], None
        while len(out) < limit:
            params = {"status": "open", "country": country, "first": 100}
            if after:
                params["after"] = after
            page = self._req("GET", "/bounties", params=params)
            out.extend(page.get("data", []))
            info = page.get("page_info") or {}
            after = info.get("end_cursor")
            if not info.get("has_next_page") or not after:
                break
        return out

    def bounty(self, bounty_id: str) -> dict:
        return self._req("GET", f"/bounties/{bounty_id}")

    def my_submissions(self, bounty_id: str) -> list[dict]:
        return self._req("GET", "/bounty_submissions", params={"bounty_id": bounty_id}).get("data", [])

    # ---- submission --------------------------------------------------------
    def upload_file(self, path: Path) -> str:
        meta = self._req("POST", "/files", json={
            "filename": path.name, "idempotency_key": f"clip-{path.stem}"})
        with path.open("rb") as fh:
            put = requests.put(meta["upload_url"], data=fh, timeout=600)
        if not put.ok:
            raise WhopError(f"file upload failed: {put.status_code}")
        for _ in range(30):
            f = self._req("GET", f"/files/{meta['id']}")
            if f.get("upload_status", f.get("status")) == "ready":
                return meta["id"]
            time.sleep(5)
        raise WhopError(f"file {meta['id']} never became ready")

    def submit(self, bounty_id: str, urls: list[str], caption: str,
               file_ids: list[str] | None = None, key: str | None = None) -> dict:
        body = {
            "bounty_id": bounty_id,
            "deliverable": {"urls": urls[:10], "caption": caption, "file_ids": (file_ids or [])[:10]},
            "idempotency_key": key or str(uuid.uuid4()),
        }
        return self._req("POST", "/bounty_submissions", json=body)
