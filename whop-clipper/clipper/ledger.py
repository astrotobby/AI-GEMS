"""JSON ledger: one job per campaign clip, so each run resumes where the last stopped
and nothing is ever posted or submitted twice."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# job lifecycle, in order
STAGES = [
    "planned",          # campaign chosen
    "sourced",          # footage downloaded
    "clipped",          # clip rendered
    "awaiting_approval",  # review mode: waiting for `approve`
    "approved",
    "published",        # live on socials
    "ready_to_submit",  # view threshold met (or none)
    "needs_livestream", # everything ready; you must livestream + submit
    "awaiting_submission",  # connector mode: the scheduled Whop-connector task submits it
    "submitted",
]
TERMINAL = {"submitted", "skipped", "failed", "needs_livestream"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"jobs": {}, "seen": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))

    @property
    def jobs(self) -> dict[str, dict]:
        return self.data["jobs"]

    def active(self) -> list[dict]:
        return [j for j in self.jobs.values() if j["status"] not in TERMINAL]

    def for_bounty(self, bounty_id: str) -> list[dict]:
        return [j for j in self.jobs.values() if j["bounty_id"] == bounty_id]

    def add(self, bounty: dict, index: int | str, score: float) -> dict:
        jid = index if isinstance(index, str) else f"{bounty['id']}-{index}"
        job = self.jobs.setdefault(jid, {
            "id": jid, "bounty_id": bounty["id"], "title": bounty["title"], "score": score,
            "status": "planned", "created": now(), "posts": {}, "history": [],
        })
        return job

    def merge(self, other: dict) -> int:
        """Fold in a ledger written concurrently elsewhere (the Whop connector task), keeping the
        most recently updated copy of each job. Returns how many jobs came from `other`."""
        taken = 0
        for jid, theirs in other.get("jobs", {}).items():
            mine = self.jobs.get(jid)
            stamp = lambda j: j.get("updated") or j.get("created") or ""
            if mine is None or stamp(theirs) > stamp(mine):
                self.jobs[jid] = theirs
                taken += 1
        for k, v in other.get("seen", {}).items():
            self.data["seen"].setdefault(k, v)
        return taken

    def move(self, job: dict, status: str, **fields) -> None:
        job.update(fields)
        job["history"].append({"at": now(), "from": job["status"], "to": status})
        job["status"] = status
        job["updated"] = now()
        self.save()
