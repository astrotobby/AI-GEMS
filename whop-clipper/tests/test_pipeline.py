import json
from pathlib import Path

import pytest

from clipper import brief, highlights, render, scoring
from clipper.config import Settings, load
from clipper.ledger import Ledger
from clipper.pipeline import Pipeline
from clipper.transcribe import Word

FIX = json.loads((Path(__file__).parent / "fixtures" / "bounties.json").read_text())["data"]
BY_ID = {b["id"]: b for b in FIX}
ANON = BY_ID["bnty_4nQb37p7bvjna"]


# ---- brief parsing -----------------------------------------------------------
def test_brief_reads_anon_campaign():
    br = brief.parse(ANON["description"], ANON["title"])
    assert br.source_links == ["https://drive.google.com/drive/folders/19FuOdOulQL9e_TQgJwzEY7sagsr8U7Wz"]
    assert br.min_views == 1000
    assert br.clip_seconds == (15, 60)
    assert br.livestream_required
    assert {"ANON_BETTING", "AnonPicks"} <= set(br.required_mentions)
    assert set(br.platforms) == {"tiktok", "instagram", "youtube"}
    assert not br.long_form and br.clip_count == 1


def test_brief_flags_long_form_and_multi_clip():
    rude = brief.parse(BY_ID["bnty_3p84Y0yFYaAoc"]["description"])
    assert rude.long_form
    assert rude.clip_seconds is None  # "3 to 5 seconds" chart timing is not a clip length
    shane = brief.parse(BY_ID["bnty_iJpOl5HbBoDQu"]["description"], BY_ID["bnty_iJpOl5HbBoDQu"]["title"])
    assert shane.clip_count == 5 and not shane.source_links


# ---- scoring ---------------------------------------------------------------
def test_ranking_puts_automatable_clipping_first():
    ranked = scoring.rank(FIX, country="KE", goal_types=["clipping", "ugc_content"])
    assert ranked[0].id == "bnty_4nQb37p7bvjna"
    assert ranked[0].eligible and ranked[0].automatable
    engage = next(s for s in ranked if s.id == "bnty_jSxsKloO3ZiGq")
    assert not engage.eligible and engage.score == 0


def test_hard_filters():
    b = dict(ANON, spots_remaining=0)
    assert "no spots remaining" in scoring.score(b, country="KE", goal_types=["clipping"]).blockers
    b = dict(ANON, allowed_country_codes=["US"])
    assert not scoring.score(b, country="KE", goal_types=["clipping"]).eligible
    b = dict(ANON, gross_paid_out_amount=ANON["budget_amount"])
    assert "budget exhausted" in scoring.score(b, country="KE", goal_types=["clipping"]).blockers


# ---- highlight picking -------------------------------------------------------
def words_from(script: list[tuple[float, str]]) -> list[Word]:
    out = []
    for start, sentence in script:
        t = start
        for w in sentence.split():
            out.append(Word(t, t + 0.3, w))
            t += 0.35
    return out


SCRIPT = [
    (0.0, "Welcome back to the show everybody."),
    (3.0, "Today we cover a few things."),
    (6.0, "Here is the thing nobody tells you about betting on football."),
    (10.0, "Ninety percent of people lose money because they chase the parlay."),
    (14.0, "The odds stack against you on every single leg of it."),
    (18.0, "The sharp move is to bet singles and track every pick."),
    (22.0, "Anyway, let us take a short break now."),
    (26.0, "Here is a question from Mike about the stadium."),
]


def test_heuristic_picks_the_hook_and_stops_before_topic_change():
    p = highlights.pick(words_from(SCRIPT), ANON, ["ANON_BETTING", "Anon_Betting"], 10, 20)
    assert 5.5 <= p.start <= 6.1
    assert p.end < 22.0
    assert p.caption.count("@ANON_BETTING") + p.caption.count("@Anon_Betting") == 1


def test_exclude_avoids_used_moment():
    p = highlights.pick(words_from(SCRIPT), ANON, [], 6, 20, exclude=[(5.0, 21.0)])
    assert p.end <= 6.0 or p.start >= 21.0
    with pytest.raises(ValueError):  # nothing unused is long enough: never silently reuse
        highlights.pick(words_from(SCRIPT), ANON, [], 10, 20, exclude=[(5.0, 21.0)])


# ---- captions ------------------------------------------------------------------
def test_ass_is_relative_to_clip_and_highlights_words():
    words = words_from(SCRIPT)
    ass = render.build_ass(words, 6.0, 12.0, hook="Nobody tells you this")
    assert "NOBODY TELLS YOU THIS" in ass
    first = next(line for line in ass.splitlines() if line.startswith("Dialogue: 0"))
    assert first.split(",")[1] == "0:00:00.00"
    assert r"{\c&H00E5FF&}HERE{\c&HFFFFFF&}" in first


def test_filter_graph_handles_both_orientations():
    assert "boxblur" in render.filter_graph(1920, 1080, "s.ass")
    assert "pad=1080:1920" in render.filter_graph(1080, 1920, "s.ass")


# ---- config ---------------------------------------------------------------------
def test_config_file_never_overrides_secrets(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[pipeline]\nmode = "auto"\nwhop_token = "leak"\n[[targets]]\nplatform = "youtube"\n')
    monkeypatch.setenv("WHOP_TOKEN", "real")
    s = load(cfg)
    assert s.mode == "auto" and s.whop_token == "real" and s.targets == [{"platform": "youtube"}]


# ---- pipeline flow with fakes ----------------------------------------------------
class FakeWhop:
    def __init__(self, bounty):
        self.b, self.submissions, self.uploads = bounty, [], []

    def bounty(self, _id):
        return self.b

    def open_bounties(self, country):
        return [self.b]

    def upload_file(self, path):
        self.uploads.append(path)
        return "file_1"

    def submit(self, bounty_id, urls, caption, file_ids=None, key=None):
        self.submissions.append((bounty_id, urls, file_ids))
        return {"id": "btys_1"}


@pytest.fixture
def pipe(tmp_path, monkeypatch):
    def make(bounty, mode="auto", views=5000, whop_via="api", slots=("00:00", "00:01")):
        slots = list(slots)
        cfg = Settings(mode=mode, work_dir=tmp_path / "work", ledger_path=tmp_path / "ledger.json",
                       whop_via=whop_via, campaigns_path=tmp_path / "campaigns.json",
                       slots=slots, timezone="UTC",
                       targets=[{"platform": "youtube", "channel": "Astro Tobby", "kinds": ["clipping"]},
                                {"platform": "youtube", "channel": "DIJITOL NOMAD", "kinds": ["ugc"]},
                                {"platform": "instagram", "username": "astr_osignal", "kinds": ["clipping", "ugc"]}])
        p = Pipeline(cfg)
        p._whop = FakeWhop(bounty)
        p._composio = object()
        posted = []

        def fake_post(c, target, video, title, caption, tags, privacy, category):
            posted.append(target.get("channel") or target["platform"])
            return f"https://{target['platform']}.example/{len(posted)}"

        monkeypatch.setattr("clipper.publish.post", fake_post)
        monkeypatch.setattr("clipper.publish.views", lambda c, t, url: views)
        p.posted = posted
        return p
    return make


def clipped_job(p, bounty):
    p.plan(p.scan([bounty]))
    job = sorted(p.ledger.jobs.values(), key=lambda j: j["slot"])[0]
    clip = p.cfg.work_dir / "clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"x")
    p.ledger.move(job, "clipped", source="src/a.mp4", clip={
        "path": str(clip), "start": 1.0, "end": 21.0, "title": "t", "caption": "c",
        "hashtags": ["a"], "hook": "h", "reason": "r", "text": "x"})
    return job


def test_auto_mode_runs_to_submission(pipe):
    b = dict(ANON, description=ANON["description"].replace("livestream", "video"))
    p = pipe(b)
    job = clipped_job(p, b)
    p.advance(job)
    assert job["status"] == "submitted"
    assert sorted(p.posted) == ["Astro Tobby", "instagram"]  # clipping -> Astro Tobby + IG
    bounty_id, urls, files = p.whop.submissions[0]
    assert bounty_id == ANON["id"] and len(urls) == 2 and files == ["file_1"]


def test_livestream_campaign_hands_off_to_human(pipe):
    p = pipe(ANON)
    job = clipped_job(p, ANON)
    p.advance(job)
    assert job["status"] == "needs_livestream"
    assert p.whop.submissions == []


def test_review_mode_waits_then_never_double_posts(pipe):
    p = pipe(ANON, mode="review", views=10)
    job = clipped_job(p, ANON)
    p.advance(job)
    assert job["status"] == "awaiting_approval" and p.posted == []
    p.ledger.move(job, "approved")
    p.advance(job)
    assert job["status"] == "published"  # 10 views < 1000 needed: keeps waiting
    p.advance(job)
    assert len(p.posted) == 2


def test_closed_campaign_is_skipped_before_posting(pipe):
    p = pipe(ANON)
    job = clipped_job(p, ANON)
    p.whop.b = dict(ANON, status="closed")
    p.advance(job)
    assert job["status"] == "skipped" and p.posted == []


def test_plan_fills_daily_quota_once_across_runs(pipe):
    p = pipe(ANON)  # Anon allows "as many clips as you like" -> may fill both clipping slots
    p.plan(p.scan([ANON]))
    p.plan(p.scan([ANON]))
    jobs = sorted(p.ledger.jobs.values(), key=lambda j: j["slot"])
    assert [(j["kind"], j["slot"]) for j in jobs] == [("clipping", 0), ("clipping", 1)]
    assert "ugc" in p.shortfall  # no UGC campaigns offered -> reported, not an error
    assert Ledger(p.cfg.ledger_path).jobs  # persisted


def test_non_repeatable_campaign_is_used_once(pipe):
    b = dict(ANON, id="bnty_once", description=ANON["description"].replace("as many clips as you like", "one clip"))
    p = pipe(b)
    p.plan(p.scan([b]))
    assert len(p.ledger.jobs) == 1 and "clipping" in p.shortfall


def test_real_unpunctuated_transcript_starts_on_hook_and_ends_on_full_thought():
    # Whisper output from the Anon Betting campaign footage: almost no full stops
    raw = json.loads((Path(__file__).parent / "fixtures" / "anon_words.json").read_text())
    p = highlights.pick([Word(**w) for w in raw], ANON, ["ANON_BETTING"], 20, 60)
    assert p.start < 2.0 and p.source_text.startswith("Here's three signs")
    assert p.source_text.rstrip().endswith("action.")
    assert 20 <= p.end - p.start <= 60
    assert "being" not in p.hashtags and "gambler" in p.hashtags


def test_check_fails_cleanly_without_keys(capsys):
    from clipper import check
    assert check.run(Settings(whop_via="api")) is False
    out = capsys.readouterr().out
    assert "❌ Whop" in out and "❌ Telegram" in out


def test_instagram_me_is_resolved_to_numeric_id():
    from clipper import publish

    class C:
        calls = []

        def run(self, slug, args, account_id=None):
            self.calls.append(slug)
            return {"data": {"id": "17841400000000000", "username": "astr_osignal"}}

    c, target = C(), {"platform": "instagram"}
    assert publish.ig_user_id(c, target) == "17841400000000000"
    publish.ig_user_id(c, target)
    assert c.calls == ["INSTAGRAM_GET_USER_INFO"]  # resolved once, then cached on the target


@pytest.mark.parametrize("raw", [
    "\nak_abc123\n", "  ak_abc123  ", "COMPOSIO_API_KEY=ak_abc123", "API_KEY: ak_abc123",
    '"ak_abc123"', "Bearer ak_abc123",
])
def test_secrets_survive_copy_paste_damage(raw, monkeypatch):
    from clipper.config import secret
    monkeypatch.setenv("X_KEY", raw)
    assert secret("X_KEY") == "ak_abc123"


def test_telegram_token_with_colon_is_untouched(monkeypatch):
    from clipper.config import secret
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", " 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw\n")
    assert secret("TELEGRAM_BOT_TOKEN") == "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


def write_snapshot(p, bounties, hours_old=0):
    from datetime import datetime, timedelta, timezone
    at = datetime.now(timezone.utc) - timedelta(hours=hours_old)
    p.cfg.campaigns_path.write_text(json.dumps({"fetched_at": at.isoformat(), "data": bounties}))


def test_connector_mode_queues_submission_instead_of_calling_whop(pipe):
    b = dict(ANON, description=ANON["description"].replace("livestream", "video"))
    p = pipe(b, whop_via="connector")
    write_snapshot(p, [b])
    job = clipped_job(p, b)
    p.advance(job)
    assert job["status"] == "awaiting_submission"
    assert job["submission"]["bounty_id"] == b["id"] and len(job["submission"]["urls"]) == 2
    assert p.whop.submissions == []  # the connector task does the actual submit


def test_connector_mode_skips_campaign_gone_from_snapshot(pipe):
    p = pipe(ANON, whop_via="connector")
    write_snapshot(p, [ANON])
    job = clipped_job(p, ANON)
    write_snapshot(p, [])  # campaign closed / no longer listed
    p.advance(job)
    assert job["status"] == "skipped" and p.posted == []


def test_connector_mode_refuses_stale_snapshot(pipe):
    p = pipe(ANON, whop_via="connector")
    write_snapshot(p, [ANON], hours_old=30)
    with pytest.raises(RuntimeError, match="old"):
        p.campaigns()


def test_config_chat_id_overrides_secret(tmp_path, monkeypatch):
    cfg = tmp_path / "c.toml"
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "8135417299")
    cfg.write_text("")
    assert load(cfg).telegram_chat_id == "8135417299"
    cfg.write_text('telegram_chat = "555000111"\n')
    assert load(cfg).telegram_chat_id == "555000111"


def test_ledger_merge_keeps_newest_copy_of_each_job(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.data["jobs"] = {
        "a": {"id": "a", "status": "published", "updated": "2026-10-18T10:00:00+00:00"},
        "b": {"id": "b", "status": "clipped", "updated": "2026-10-18T12:00:00+00:00"},
    }
    other = {"jobs": {
        "a": {"id": "a", "status": "submitted", "updated": "2026-10-18T11:00:00+00:00"},  # newer -> taken
        "b": {"id": "b", "status": "planned", "updated": "2026-10-18T09:00:00+00:00"},    # older -> ignored
        "c": {"id": "c", "status": "planned", "created": "2026-10-18T09:00:00+00:00"},    # new job -> taken
    }, "seen": {}}
    assert led.merge(other) == 2
    assert [led.jobs[k]["status"] for k in "abc"] == ["submitted", "clipped", "planned"]


def test_publish_waits_for_its_slot(pipe):
    from datetime import datetime, timedelta, timezone as tz
    later = (datetime.now(tz.utc) + timedelta(hours=2)).strftime("%H:%M")
    p = pipe(ANON, slots=(later, later))
    job = clipped_job(p, ANON)
    if datetime.now(tz.utc).strftime("%H:%M") > later:  # crossed midnight; slot is tomorrow's time
        return
    p.advance(job)
    assert job["status"] == "approved" and p.posted == []  # made and approved, not yet live


def test_ugc_job_is_made_from_brief_and_routed_to_dijitol(pipe, monkeypatch):
    ugc_b = dict(BY_ID["bnty_lyJ5DBAjGJ6gF"], id="bnty_ugc", business_goal_type="ugc_content",
                 description="Make a short promo for the FocusPad app. Post it on Instagram Reels or YouTube Shorts.")
    made = []

    def fake_make(b, out, *a, **k):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        made.append(b["id"])
        return {"path": str(out), "start": 0.0, "end": 0.0, "title": "t", "caption": "c",
                "hashtags": ["a"], "hook": "h", "reason": "template script", "text": "x"}

    monkeypatch.setattr("clipper.ugc.make", fake_make)
    p = pipe(ugc_b)
    p.plan(p.scan([ugc_b]))
    job = next(j for j in p.ledger.jobs.values() if j["kind"] == "ugc")
    p.advance(job)
    assert made == ["bnty_ugc"]
    assert sorted(p.posted) == ["DIJITOL NOMAD", "instagram"]


def test_old_review_job_is_migrated_into_todays_first_slot(pipe):
    p = pipe(ANON)
    job = p.ledger.add(ANON, 0, 50)
    p.ledger.move(job, "awaiting_approval")
    p._migrate()
    assert job["status"] == "approved" and job["slot"] == 0 and job["kind"] == "clipping"


def test_daily_summary_reports_shortfall_once(pipe):
    p = pipe(ANON)
    p.plan(p.scan([ANON]))
    from datetime import datetime, time, timezone as tz
    evening = datetime.combine(p.today(), time(23, 0), tz.utc)
    assert p.daily_summary(now=evening.replace(hour=0, minute=10)) is None  # before last slot + 30m
    text = p.daily_summary(now=evening)
    assert text and "ugc: 0/2 live" in text and "no more eligible ugc campaigns" in text
    assert p.daily_summary(now=evening) is None  # once per day


def test_ugc_captions_use_script_words_with_heard_timing():
    from clipper.ugc import align
    heard = [Word(0.0, 0.4, "Okay,"), Word(0.4, 0.8, "so"), Word(1.0, 1.4, "urnex"), Word(1.5, 1.9, "today.")]
    out = align("Okay, so earn XP today.", heard, 2.5)
    assert [w.text for w in out] == ["Okay,", "so", "earn", "XP", "today."]
    assert out[0].start == 0.0 and out[-1].start == 1.5
    assert 0.8 <= out[2].start < out[3].start < 1.5  # unmatched words spread between neighbours


def test_claude_plan_used_when_api_has_no_credit(monkeypatch):
    from clipper import llm

    def no_credit(*a):
        raise RuntimeError("Your credit balance is too low to access the Anthropic API")
    seen = {}

    def fake_run(cmd, input, env, **kw):
        seen["env"] = env
        return type("P", (), {"stdout": json.dumps({"is_error": False, "structured_output": {"n": 3}}),
                              "stderr": ""})()
    monkeypatch.setattr(llm, "_api", no_credit)
    monkeypatch.setattr(llm.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-no-credit")
    assert llm.structured("pick", {"type": "object"}, "sk-no-credit", "claude-opus-5-5", "oat-token") == {"n": 3}
    assert "ANTHROPIC_API_KEY" not in seen["env"] and seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-token"
