"""The end-to-end loop: discover -> source -> clip -> (approve) -> publish -> track views -> submit."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import brief as brief_mod
from . import highlights, publish, render, scoring, sources, transcribe
from .config import Settings
from .ledger import Ledger
from .notify import Telegram
from .whop import Whop

log = logging.getLogger(__name__)
VIEW_DEADLINE_DAYS = 14


def bounty_link(b: dict) -> str:
    exp, post = b.get("discussion_experience_id") or b.get("experience_id"), b.get("discussion_post_id")
    return f"https://whop.com/townhall/{exp}/{post}/" if exp and post else f"bounty id {b['id']}"


class Pipeline:
    def __init__(self, cfg: Settings, dry_run: bool = False):
        self.cfg, self.dry_run = cfg, dry_run
        self.ledger = Ledger(cfg.ledger_path)
        self.tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
        self._whop: Whop | None = None
        self._composio: publish.Composio | None = None

    # ---- lazy clients (scan/clip work without social credentials) ----------
    @property
    def whop(self) -> Whop:
        if self._whop is None:
            self._whop = Whop(self.cfg.whop_token)
        return self._whop

    @property
    def composio(self) -> publish.Composio:
        if self._composio is None:
            self._composio = publish.Composio(self.cfg.composio_api_key, self.cfg.composio_user_id)
        return self._composio

    def job_dir(self, job: dict) -> Path:
        return self.cfg.work_dir / job["bounty_id"]

    # ---- Whop data: direct API, or the snapshot the Whop-connector task commits ----
    def campaigns(self) -> list[dict]:
        if self.cfg.whop_via == "api":
            return self.whop.open_bounties(self.cfg.country)
        path = self.cfg.campaigns_path
        if not path.exists():
            raise RuntimeError(f"{path.name} missing - the Whop connector task has not run yet")
        snap = json.loads(path.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(snap["fetched_at"])
        if age > timedelta(hours=self.cfg.campaigns_max_age_hours):
            raise RuntimeError(f"{path.name} is {age.total_seconds() / 3600:.0f}h old - the Whop connector "
                               "task has stopped updating it")
        return snap.get("data", [])

    def bounty(self, job: dict, refresh: bool = False) -> dict:
        path = self.job_dir(job) / "bounty.json"
        if refresh and self.cfg.whop_via != "api":
            # a campaign missing from a fresh snapshot is no longer open to us
            snap = {b["id"]: b for b in self.campaigns()}
            data = snap.get(job["bounty_id"]) or dict(json.loads(path.read_text()), status="closed")
            path.write_text(json.dumps(data, indent=1))
            return data
        if refresh or not path.exists():
            data = self.whop.bounty(job["bounty_id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=1))
            return data
        return json.loads(path.read_text())

    # ---- 1. discover --------------------------------------------------------
    def scan(self, bounties: list[dict] | None = None) -> list[scoring.Scored]:
        bounties = bounties if bounties is not None else self.campaigns()
        return scoring.rank(bounties, country=self.cfg.country, goal_types=self.cfg.goal_types,
                            min_clip=self.cfg.min_clip_seconds)

    def plan(self, ranked: list[scoring.Scored]) -> list[dict]:
        new = []
        slots = self.cfg.max_active_campaigns - len({j["bounty_id"] for j in self.ledger.active()})
        for s in ranked:
            if slots <= 0:
                break
            if not (s.eligible and s.automatable) or s.score < self.cfg.min_score:
                continue
            if s.brief.livestream_required and not self.cfg.allow_livestream_campaigns:
                continue
            if self.ledger.for_bounty(s.id):
                continue
            d = self.cfg.work_dir / s.id
            d.mkdir(parents=True, exist_ok=True)
            (d / "bounty.json").write_text(json.dumps(s.bounty, indent=1))
            for i in range(self.cfg.clips_per_campaign):
                new.append(self.ledger.add(s.bounty, i, s.score))
            self.ledger.save()
            slots -= 1
            self.tg.send(f"🎯 New campaign picked ({s.score}): {s.bounty['title']}\n"
                         f"${s.bounty.get('net_reward_amount')} net, {s.bounty.get('spots_remaining')} spots\n"
                         + "\n".join(f"• {r}" for r in s.reasons)
                         + ("\n⚠️ " + "; ".join(s.human_steps) if s.human_steps else ""))
        return new

    # ---- 2-6. advance one job as far as it can go this run -------------------
    def advance(self, job: dict) -> None:
        handlers = {
            "planned": self._source, "sourced": self._clip, "clipped": self._gate,
            "approved": self._publish, "published": self._track, "ready_to_submit": self._submit,
        }
        while job["status"] in handlers:
            before = job["status"]
            try:
                handlers[before](job)
            except Exception as e:
                log.exception("job %s failed at %s", job["id"], before)
                job.setdefault("errors", []).append(f"{before}: {e}")
                fails = sum(1 for x in job["errors"] if x.startswith(before))
                if fails >= 3:
                    self.ledger.move(job, "failed")
                    self.tg.send(f"❌ {job['title']}: gave up at '{before}' after 3 tries.\n{e}")
                else:
                    self.ledger.save()
                return
            if job["status"] == before:  # waiting on something external
                return

    def _source(self, job: dict) -> None:
        b = self.bounty(job)
        br = brief_mod.parse(b["description"], b["title"])
        src_dir = self.job_dir(job) / "src"
        # prefer footage no earlier clip of this campaign used
        used = {Path(j["source"]).name for j in self.ledger.for_bounty(job["bounty_id"]) if j.get("source")}
        files: list[Path] = []
        for url in br.source_links:
            try:
                files += sources.fetch(url, src_dir, skip=used)
            except Exception as e:  # one dead link shouldn't sink the campaign
                log.warning("source %s failed: %s", url, e)
        files = sorted(set(files)) or sources._videos(src_dir)
        if not files:
            raise RuntimeError("could not download any source video")
        fresh = [f for f in files if f.name not in used] or files
        self.ledger.move(job, "sourced", source=str(max(fresh, key=sources.duration)))

    def _clip(self, job: dict) -> None:
        b = self.bounty(job)
        br = brief_mod.parse(b["description"], b["title"])
        src = Path(job["source"])
        lo, hi = br.clip_seconds or (self.cfg.min_clip_seconds, self.cfg.max_clip_seconds)
        lo, hi = max(lo, self.cfg.min_clip_seconds), min(hi, self.cfg.max_clip_seconds)
        words = transcribe.transcribe(src, self.cfg.whisper_model)
        used = [(j["clip"]["start"], j["clip"]["end"]) for j in self.ledger.for_bounty(job["bounty_id"])
                if j.get("clip") and j.get("source") == job["source"] and j["id"] != job["id"]]
        p = highlights.pick(words, b, br.required_mentions, lo, hi,
                            api_key=self.cfg.anthropic_api_key, model=self.cfg.claude_model, exclude=used)
        out = self.job_dir(job) / "clips" / f"{job['id']}.mp4"
        render.render(src, words, p.start, p.end, out, hook=p.hook, font=self.cfg.caption_font)
        self.ledger.move(job, "clipped", clip={
            "path": str(out), "start": round(p.start, 2), "end": round(p.end, 2), "title": p.title,
            "caption": p.caption, "hashtags": p.hashtags, "hook": p.hook, "reason": p.reason,
            "text": p.source_text[:500],
        })

    def _gate(self, job: dict) -> None:
        c = job["clip"]
        if self.cfg.mode == "auto":
            self.ledger.move(job, "approved")
            return
        self.ledger.move(job, "awaiting_approval")
        self.tg.send_video(Path(c["path"]), (
            f"🎬 Review: {job['title']}\n"
            f"{Path(job['source']).name} {c['start']:.1f}s-{c['end']:.1f}s ({c['end'] - c['start']:.0f}s)\n"
            f"Title: {c['title']}\nCaption: {c['caption']}\nWhy: {c['reason']}\n\n"
            f"Approve: approve {job['id']}\nReject: skip {job['id']}\n"
            "(GitHub -> Actions -> Whop clipper -> Run workflow, or python -m clipper ...)"))

    def _targets(self, job: dict) -> list[dict]:
        b = self.bounty(job)
        wanted = brief_mod.parse(b["description"], b["title"]).platforms
        ts = [t for t in self.cfg.targets if not wanted or t["platform"] in wanted]
        return ts or self.cfg.targets

    def _publish(self, job: dict) -> None:
        if self.dry_run:
            log.info("dry run: would publish %s", job["id"])
            return
        b = self.bounty(job, refresh=True)
        if b.get("status") != "open" or (b.get("spots_remaining") or 0) <= 0:
            self.ledger.move(job, "skipped", reason="campaign closed before posting")
            self.tg.send(f"⏭️ {job['title']} closed before posting - skipped.")
            return
        c = job["clip"]
        targets = self._targets(job)
        if not targets:
            raise RuntimeError("no publish targets configured in config.toml")
        errors = []
        for t in targets:
            key = f"{t['platform']}:{t.get('account_id', '')}"
            if key in job["posts"]:
                continue  # already live - never double-post
            try:
                url = publish.post(self.composio, t, Path(c["path"]), c["title"], c["caption"],
                                   c["hashtags"], self.cfg.youtube_privacy, self.cfg.youtube_category_id)
                job["posts"][key] = {"url": url, "at": datetime.now(timezone.utc).isoformat(), "views": 0}
                self.ledger.save()
            except Exception as e:
                errors.append(f"{key}: {e}")
        if not job["posts"]:
            raise RuntimeError("; ".join(errors))
        if errors:
            job.setdefault("errors", []).extend(errors)
        self.ledger.move(job, "published", published_at=datetime.now(timezone.utc).isoformat())
        self.tg.send("📤 Posted: " + job["title"] + "\n" + "\n".join(p["url"] for p in job["posts"].values()))

    def _track(self, job: dict) -> None:
        b = self.bounty(job)
        need = brief_mod.parse(b["description"], b["title"]).min_views
        best = 0
        for key, post in job["posts"].items():
            platform, account = key.split(":", 1)
            target = next((t for t in self.cfg.targets if t["platform"] == platform
                           and t.get("account_id", "") == account), {"platform": platform})
            try:
                post["views"] = publish.views(self.composio, target, post["url"])
            except Exception as e:
                log.warning("views for %s failed: %s", post["url"], e)
            best = max(best, post.get("views", 0))
        self.ledger.save()
        if best >= need:
            self.ledger.move(job, "ready_to_submit")
            return
        age = datetime.now(timezone.utc) - datetime.fromisoformat(job["published_at"])
        if age > timedelta(days=VIEW_DEADLINE_DAYS):
            self.ledger.move(job, "failed", reason=f"only {best}/{need} views after {VIEW_DEADLINE_DAYS} days")
            self.tg.send(f"📉 {job['title']}: {best}/{need} views after {VIEW_DEADLINE_DAYS} days - stopped tracking.")

    def _submission_caption(self, job: dict) -> str:
        c = job["clip"]
        return (f"Source: {Path(job['source']).name} @ {c['start']:.1f}s-{c['end']:.1f}s. "
                f"Posts: {', '.join(p['url'] for p in job['posts'].values())}")

    def _submit(self, job: dict) -> None:
        if self.dry_run:
            return
        b = self.bounty(job, refresh=True)
        br = brief_mod.parse(b["description"], b["title"])
        urls = [p["url"] for p in job["posts"].values()]
        if br.livestream_required:
            self.ledger.move(job, "needs_livestream")
            self.tg.send(
                f"🔴 Your turn: {job['title']} needs a livestream to submit.\n"
                f"Bounty: {bounty_link(b)}\n"
                f"Live posts: " + " | ".join(f"{p['url']} ({p.get('views', 0)} views)" for p in job["posts"].values())
                + f"\nClip file: {job['clip']['path']}\nSubmission notes: {self._submission_caption(job)}\n"
                "Go live on Whop showing what the brief asks for, attach the links above, then run:\n"
                f"python -m clipper done {job['id']}")
            return
        if b.get("status") != "open" or (b.get("spots_remaining") or 0) <= 0:
            self.ledger.move(job, "skipped", reason="campaign closed before submission")
            return
        if self.cfg.whop_via != "api":
            # the scheduled Whop-connector task picks this up, submits as you, and marks it submitted
            self.ledger.move(job, "awaiting_submission", submission={
                "bounty_id": b["id"], "urls": urls, "caption": self._submission_caption(job)})
            self.tg.send(f"📨 Queued for Whop submission: {job['title']}\n" + "\n".join(urls))
            return
        file_ids = []
        if "attach" in b["description"].lower():
            file_ids.append(self.whop.upload_file(Path(job["clip"]["path"])))
        res = self.whop.submit(b["id"], urls, self._submission_caption(job), file_ids, key=f"submit-{job['id']}")
        self.ledger.move(job, "submitted", submission_id=res.get("id"))
        self.tg.send(f"✅ Submitted to Whop: {job['title']}\n" + "\n".join(urls))

    # ---- one scheduled run ---------------------------------------------------
    def run(self) -> None:
        try:
            self.plan(self.scan())
        except Exception as e:
            log.exception("discovery failed")
            self.tg.send(f"⚠️ Whop discovery failed: {e}")
        for job in list(self.ledger.active()):
            self.advance(job)
        self.ledger.save()
