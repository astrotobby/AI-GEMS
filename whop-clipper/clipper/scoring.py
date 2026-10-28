"""Rank open Whop bounties by how worth it they are for an automated clipper."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import brief as brief_mod

CLIP_WORDS = ("clip", "shorts", "reel", "edit")
UGC_WORDS = ("ugc", "slideshow", "promo video", "promote", "review video", "testimonial")
OUR_PLATFORMS = {"youtube", "instagram"}


def kind_of(b: dict) -> str | None:
    """'clipping' (cut provided footage), 'ugc' (make original promo content) or None."""
    goal, title = b.get("business_goal_type"), (b.get("title") or "").lower()
    if goal == "ugc_content" or (goal is None and any(w in title for w in UGC_WORDS)):
        return "ugc"
    if goal == "clipping" or (goal is None and any(w in title for w in CLIP_WORDS)):
        return "clipping"
    return None


@dataclass
class Scored:
    bounty: dict
    brief: brief_mod.Brief
    score: float = 0.0
    eligible: bool = True
    automatable: bool = True
    kind: str | None = None
    blockers: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    human_steps: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.bounty["id"]

    def summary(self) -> str:
        b = self.bounty
        flag = "AUTO" if self.automatable else "HUMAN"
        return (f"{self.score:5.1f}  [{flag}] ${b.get('net_reward_amount') or 0:<6} "
                f"spots {b.get('spots_remaining')!s:<4} {b['id']}  {b['title'][:60]}")


def budget_left(b: dict) -> float:
    return float(b.get("budget_amount") or 0) - float(b.get("gross_paid_out_amount") or 0)


def score(b: dict, *, country: str, goal_types: list[str], min_clip: int = 20,
          platforms: set[str] | frozenset[str] = OUR_PLATFORMS) -> Scored:
    br = brief_mod.parse(b.get("description") or "", b.get("title") or "")
    s = Scored(bounty=b, brief=br, kind=kind_of(b))
    net = float(b.get("net_reward_amount") or 0)
    gross = float(b.get("gross_reward_amount") or 0)
    title = (b.get("title") or "").lower()

    # ---- hard eligibility ------------------------------------------------
    if b.get("status") != "open":
        s.blockers.append(f"status is {b.get('status')}")
    if (b.get("spots_remaining") or 0) <= 0:
        s.blockers.append("no spots remaining")
    if budget_left(b) < gross:
        s.blockers.append("budget exhausted")
    allowed = b.get("allowed_country_codes") or []
    if allowed and country not in allowed:
        s.blockers.append(f"not open to {country}")
    if (b.get("viewer_accepted_submissions_count") or 0) >= (b.get("accepted_submissions_per_user_limit") or 1):
        s.blockers.append("you already hit the per-user limit")
    goal = b.get("business_goal_type")
    if s.kind is None or (goal is not None and goal not in goal_types):
        s.blockers.append(f"not a clipping/UGC campaign (goal: {goal})")
    if br.platforms and not set(br.platforms) & set(platforms):
        s.blockers.append(f"only accepts {', '.join(br.platforms)} (we post to {', '.join(sorted(platforms))})")
    if br.screenshot_proof:
        s.blockers.append("wants screenshots of joining/inviting, not a posted video")
    if br.needs_physical_presence:
        s.blockers.append("needs you physically on camera/location")
    s.eligible = not s.blockers

    # ---- automation feasibility -----------------------------------------
    if s.kind == "ugc":
        if br.needs_real_person:
            s.automatable = False
            s.human_steps.append("UGC needs a real person on camera / forbids AI content")
    elif not br.source_links:
        s.automatable = False
        s.human_steps.append("no downloadable source footage linked - pick sources manually")
    if br.long_form:
        s.automatable = False
        s.human_steps.append("long-form edit (charts/b-roll/script) - not a short clip")
    if br.livestream_required:
        s.human_steps.append("livestream proof required at submission - you must go live")
    if br.clip_count > 1:
        s.automatable = False
        s.human_steps.append(f"wants {br.clip_count} clips in one submission")
    if br.clip_seconds and br.clip_seconds[1] < min_clip:
        s.automatable = False
        s.human_steps.append("requested clip length is shorter than the clipper supports")

    # ---- score -----------------------------------------------------------
    pts = 0.0
    pay = min(30.0, 10 * math.log10(1 + net * 10))  # $0.10->3, $1->10, $7->18, $70->28
    pts += pay
    s.reasons.append(f"payout ${net:.2f} net (+{pay:.0f})")

    budget = float(b.get("budget_amount") or 0)
    if budget > 0:
        health = 15 * max(0.0, budget_left(b) / budget)
        pts += health
        s.reasons.append(f"budget {budget_left(b):.0f}/{budget:.0f} left (+{health:.0f})")

    spots = b.get("spots_remaining") or 0
    queue = b.get("unresolved_submissions_count") or 0
    competition = 10 * (spots / (spots + queue)) if spots + queue else 0
    pts += competition
    s.reasons.append(f"{spots} spots vs {queue} pending (+{competition:.0f})")

    if br.source_links or s.kind == "ugc":
        pts += 15
        s.reasons.append("source footage provided (+15)" if br.source_links else "UGC: we make the video (+15)")
    if not br.long_form:
        pts += 15
    if br.min_views == 0:
        pts += 10
        s.reasons.append("paid on approval, no view threshold (+10)")
    elif br.min_views <= 1000:
        pts += 5
        s.reasons.append(f"needs {br.min_views} views (+5)")
    else:
        s.reasons.append(f"needs {br.min_views} views (+0)")
    if br.livestream_required:
        pts -= 10
        s.reasons.append("livestream proof (-10)")
    if br.clip_count > 1:
        pts -= 3 * (br.clip_count - 1)
        s.reasons.append(f"{br.clip_count} clips per submission (-{3 * (br.clip_count - 1)})")

    s.score = round(pts if s.eligible else 0.0, 1)
    return s


def rank(bounties: list[dict], *, country: str, goal_types: list[str], min_clip: int = 20,
         platforms: set[str] | frozenset[str] = OUR_PLATFORMS) -> list[Scored]:
    scored = [score(b, country=country, goal_types=goal_types, min_clip=min_clip, platforms=platforms)
              for b in bounties]
    return sorted(scored, key=lambda x: (x.eligible, x.automatable, x.score), reverse=True)
