"""CLI: python -m clipper {check,connect,scan,run,status,approve,skip,done,clip}"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config
from .pipeline import Pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipper", description="Whop clipping automation")
    ap.add_argument("--config", type=Path)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="test every credential (read-only + one Telegram message)")

    cn = sub.add_parser("connect", help="send a Composio login link for a platform to your Telegram")
    cn.add_argument("toolkit", choices=["instagram", "youtube", "tiktok"])
    cn.add_argument("auth_config", nargs="?", default="", help="auth config id (ac_...), e.g. your own Google app")

    sc = sub.add_parser("scan", help="rank open Whop campaigns (no side effects)")
    sc.add_argument("--from-file", type=Path, help="score a saved bounties JSON instead of calling Whop")
    sc.add_argument("--all", action="store_true", help="also list ineligible campaigns")

    r = sub.add_parser("run", help="one full cycle: pick campaigns and advance every job")
    r.add_argument("--dry-run", action="store_true", help="clip but never publish or submit")

    sub.add_parser("status", help="show jobs in the ledger")
    m = sub.add_parser("merge-ledger", help="fold another copy of the ledger into ours (newest job wins)")
    m.add_argument("other", type=Path)
    for name, help_ in (("approve", "approve a clip for publishing (review mode)"),
                        ("skip", "drop a job"),
                        ("done", "mark a livestream job as submitted by you")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("job_id")

    u = sub.add_parser("make-ugc", help="render a UGC video for a campaign in the snapshot (no posting)")
    u.add_argument("bounty_id")

    c = sub.add_parser("clip", help="make a clip from any local video for a bounty (testing)")
    c.add_argument("video", type=Path)
    c.add_argument("--bounty-file", type=Path, help="bounty JSON for the brief/copy")
    c.add_argument("--out", type=Path, default=Path("work/test-clip.mp4"))

    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load(a.config)
    pipe = Pipeline(cfg, dry_run=getattr(a, "dry_run", False))

    if a.cmd == "check":
        from . import check
        return 0 if check.run(cfg) else 1

    if a.cmd == "connect":
        from .notify import Telegram
        from .publish import Composio
        url = Composio(cfg.composio_api_key, cfg.composio_user_id).link(a.toolkit, a.auth_config)
        tg = Telegram(cfg.telegram_bot_token, cfg.telegram_chat_id)
        sent = tg.send(f"🔗 Connect {a.toolkit} to the clipper (link expires soon, open it on your phone or PC):\n{url}",
                       private=True)
        print("connect link sent to Telegram" if sent else f"could not send to Telegram: {tg.last_error}")
        return 0 if sent else 1

    if a.cmd == "scan":
        data = json.loads(a.from_file.read_text())["data"] if a.from_file else None
        for s in pipe.scan(data):
            if not s.eligible and not a.all:
                continue
            print(s.summary())
            for line in s.reasons:
                print(f"           + {line}")
            for line in s.blockers:
                print(f"           x {line}")
            for line in s.human_steps:
                print(f"           ! {line}")
        return 0

    if a.cmd == "run":
        pipe.run()
        return 0

    if a.cmd == "merge-ledger":
        if a.other.exists() and a.other.stat().st_size:
            n = pipe.ledger.merge(json.loads(a.other.read_text()))
            pipe.ledger.save()
            print(f"merged: {n} job(s) taken from {a.other}")
        return 0

    if a.cmd == "status":
        for j in sorted(pipe.ledger.jobs.values(), key=lambda j: j["created"]):
            posts = " ".join(f"{p['url']} ({p.get('views', 0)}v)" for p in j["posts"].values())
            print(f"{j['status']:<18} {j['id']:<26} {j['title'][:45]:<45} {posts}")
        return 0

    if a.cmd in ("approve", "skip", "done"):
        job = pipe.ledger.jobs.get(a.job_id)
        if not job:
            print(f"no job {a.job_id}", file=sys.stderr)
            return 1
        expected = {"approve": "awaiting_approval", "done": "needs_livestream", "skip": None}[a.cmd]
        if expected and job["status"] != expected:
            print(f"job is '{job['status']}', expected '{expected}'", file=sys.stderr)
            return 1
        pipe.ledger.move(job, {"approve": "approved", "skip": "skipped", "done": "submitted"}[a.cmd])
        print(f"{a.job_id} -> {job['status']}")
        return 0

    if a.cmd == "make-ugc":
        from . import brief as brief_mod
        from . import ugc
        b = next(x for x in pipe.campaigns() if x["id"] == a.bounty_id)
        br = brief_mod.parse(b["description"], b["title"])
        out = cfg.work_dir / "ugc-test" / f"{a.bounty_id}.mp4"
        try:
            composio = pipe.composio
        except Exception:
            composio = None
        clip = ugc.make(b, out, cfg.anthropic_api_key, cfg.claude_model, br.product_links,
                        br.required_mentions, cfg.caption_font, composio=composio,
                        voice_id=cfg.ugc_voice_id, pexels_key=cfg.pexels_api_key,
                        elevenlabs_key=cfg.elevenlabs_api_key, oauth_token=cfg.claude_oauth_token)
        print(json.dumps(clip, indent=2))
        pipe.tg.send_video(out, f"🧪 UGC test render (not posted): {b['title']}\n{clip['caption']}\n"
                                f"Made with: {clip['reason']}")
        return 0

    if a.cmd == "clip":
        from . import brief as brief_mod
        from . import highlights, render, transcribe
        bounty = json.loads(a.bounty_file.read_text()) if a.bounty_file else {"title": "", "description": ""}
        br = brief_mod.parse(bounty.get("description", ""), bounty.get("title", ""))
        words = transcribe.transcribe(a.video, cfg.whisper_model)
        p = highlights.pick(words, bounty, br.required_mentions, cfg.min_clip_seconds, cfg.max_clip_seconds,
                            api_key=cfg.anthropic_api_key, model=cfg.claude_model,
                            oauth_token=cfg.claude_oauth_token)
        render.render(a.video, words, p.start, p.end, a.out, hook=p.hook, font=cfg.caption_font)
        print(json.dumps({"out": str(a.out), "start": p.start, "end": p.end, "title": p.title,
                          "caption": p.caption, "hashtags": p.hashtags, "reason": p.reason}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
