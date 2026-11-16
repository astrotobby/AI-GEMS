"""UGC Africa (ugc.africa) as a second campaign source, next to Whop.

Creator flow on their API (https://developer.ugc.africa):
    discover active campaigns -> reserve a slot -> post -> submit the post link for that slot
UGC Africa verifies the views itself and pays per 1,000 verified views, so unlike Whop there is no
view threshold to wait for and no livestream step.

Campaigns are normalised into the same dict shape as a Whop bounty (`id` is `ugc_<campaign id>`,
`source` is "ugcafrica") so scoring, brief parsing, planning and the job ledger work unchanged.
Only CLIP_CAMPAIGN / RAW_FOOTAGE campaigns are taken: the pipeline cuts clips from footage the
advertiser provides. Ready-made assets that must be posted as they are are not clipping jobs.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)
BASE = "https://api.ugc.africa"

# our platform names <-> UGC Africa's platform enum
PLATFORM_ENUM = {"youtube": "YOUTUBE_SHORTS", "instagram": "INSTAGRAM_REELS", "tiktok": "TIKTOK"}
ENUM_PLATFORM = {v: k for k, v in PLATFORM_ENUM.items()}
# words the brief parser recognises; other platforms are written plainly so "Facebook Reels" is not read as Instagram
PLATFORM_WORDS = {"YOUTUBE_SHORTS": "YouTube Shorts", "INSTAGRAM_REELS": "Instagram Reels", "TIKTOK": "TikTok",
                  "X_TWITTER": "X", "WHATSAPP_STATUS": "WhatsApp", "FACEBOOK_REELS": "Facebook",
                  "LINKEDIN": "LinkedIn", "PINTEREST": "Pinterest", "TELEGRAM_CHANNEL": "Telegram",
                  "SNAPCHAT_SPOTLIGHT": "Snapchat"}


class UgcError(RuntimeError):
    def __init__(self, msg: str, status: int = 0, code: str = ""):
        super().__init__(msg)
        self.status, self.code = status, code


class NoCapacity(UgcError):
    """The campaign has no free slot left."""


def _find(obj: Any, *keys: str) -> Any:
    """First non-empty value under any of `keys`, searching nested dicts/lists breadth-first."""
    queue = [obj]
    while queue:
        cur = queue.pop(0)
        if isinstance(cur, dict):
            for k in keys:
                if cur.get(k) not in (None, "", [], {}):
                    return cur[k]
            queue.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            queue.extend(cur)
    return None


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class UgcAfrica:
    def __init__(self, key: str, base: str = BASE):
        if not key:
            raise UgcError("UGC_AFRICA_API_KEY is not set")
        self.base = base
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json",
                               "User-Agent": "whop-clipper/1.0"})

    def _req(self, method: str, path: str, params: dict | None = None, body: dict | None = None,
             idem: str | None = None) -> Any:
        headers = {"Idempotency-Key": idem} if idem else {}
        if body is not None and idem:
            body = dict(body, idempotencyKey=idem)
        last = ""
        for attempt in range(5):
            try:
                r = self.s.request(method, self.base + path, params=params, json=body, headers=headers, timeout=60)
            except requests.RequestException as e:  # the API sometimes drops the connection: retry
                last = str(e)[:200]
                time.sleep(2 + attempt * 2)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                time.sleep(2 + attempt * 3)
                continue
            try:
                data = r.json() if r.content else {}
            except ValueError:
                data = {"message": r.text[:200]}
            if r.ok:
                return data
            code = str((data or {}).get("code") or (data or {}).get("error") or "")
            msg = f"{method} {path} -> {r.status_code}: {str((data or {}).get('message') or data)[:300]}"
            if r.status_code == 409 and "NO_CAPACITY" in (code + msg).upper():
                raise NoCapacity(msg, r.status_code, code)
            raise UgcError(msg, r.status_code, code)
        raise UgcError(f"{method} {path} kept failing ({last})")

    # ---- discovery ---------------------------------------------------------
    def campaigns(self, country: str = "", types: list[str] | None = None, limit: int = 50) -> list[dict]:
        """Raw active campaigns with free slots, all pages."""
        out: list[dict] = []
        for t in types or [""]:
            for page in range(1, 11):
                params = {"page": page, "limit": limit, "sort": "NEW"}
                if country:
                    params["country"] = country
                if t:
                    params["campaignType"] = t
                res = self._req("GET", "/actions-gateway/campaigns/discover", params=params)
                items = res.get("items") if isinstance(res, dict) else res
                out += [c for c in (items or []) if isinstance(c, dict)]
                if not (isinstance(res, dict) and res.get("hasMore")):
                    break
        return list({str(c.get("id")): c for c in out}.values())

    def my_slots(self) -> list[dict]:
        res = self._req("GET", "/actions-gateway/slots/mine")
        items = res if isinstance(res, list) else (res.get("items") or res.get("data") or [])
        return [s for s in items if isinstance(s, dict)]

    def earnings(self) -> dict:
        return self._req("GET", "/actions-gateway/payout/earnings")

    def discover(self, country: str = "", types: list[str] | None = None,
                 clip_modes: tuple[str, ...] = ("RAW_FOOTAGE",)) -> list[dict]:
        """Open campaigns we could clip, as Whop-shaped bounty dicts."""
        mine = {str(_find(s, "campaignId") or "") for s in self.my_slots()}
        out = []
        for c in self.campaigns(country, types):
            b = normalize(c, clip_modes)
            if b:
                b["viewer_accepted_submissions_count"] = 1 if str(c.get("id")) in mine else 0
                out.append(b)
        return out

    # ---- the creator loop ----------------------------------------------------
    def reserve(self, campaign_id: str, key: str) -> str:
        """Reserve a slot on the campaign and return the slot id (idempotent per key)."""
        try:
            res = self._req("POST", f"/actions-gateway/campaigns/{campaign_id}/slots/reserve", body={}, idem=key)
        except NoCapacity:
            raise
        except UgcError as e:
            if e.status != 409:
                raise
            res = {}  # replayed or already reserved: fall back to our own slot list below
        slot = _find(res, "slotId") or (res.get("id") if isinstance(res, dict) else None)
        if not slot:
            for s in self.my_slots():
                if str(s.get("campaignId")) == str(campaign_id) and str(s.get("status", "")).upper() not in (
                        "EXPIRED", "REJECTED", "CANCELLED"):
                    slot = s.get("id")
                    break
        if not slot:
            raise UgcError(f"reserved a slot on {campaign_id} but could not read its id: {str(res)[:200]}")
        return str(slot)

    def submit(self, slot_id: str, platform: str, post_url: str, key: str, ad_disclosure: bool = False) -> dict:
        return self._req("POST", f"/actions-gateway/slots/{slot_id}/submit", idem=key, body={
            "platform": platform, "postUrl": post_url, "adDisclosureIncluded": ad_disclosure})


def normalize(c: dict, clip_modes: tuple[str, ...] = ("RAW_FOOTAGE",)) -> dict | None:
    """A UGC Africa campaign as a Whop-shaped bounty dict, or None if it is not a clip job we can do.

    The API documents its endpoints but not the discover payload, so each value is looked up under
    the likely field names. `raw` keeps the original so a mismatch can be diagnosed.
    """
    cid = str(c.get("id") or "")
    if not cid:
        return None
    ctype = str(c.get("campaignType") or "").upper()
    mode = str(c.get("clipSourceMode") or "").upper()
    if ctype != "CLIP_CAMPAIGN" or (mode and clip_modes and mode not in clip_modes):
        return None
    status = str(c.get("status") or "ACTIVE").upper()
    platforms = [str(p).upper() for p in (c.get("targetPlatforms") or c.get("platforms") or [])]
    tags = [str(t) for t in (c.get("requiredHashtags") or [])]
    source = c.get("sourceContentUrl") or _find(c, "sourceUrl", "sourceLink") or ""
    desc = "\n".join(x for x in (
        str(c.get("description") or ""),
        str(c.get("broadcastInstructions") or ""),
        f"Source footage: {source}" if source else "",
        ("Platforms: " + ", ".join(PLATFORM_WORDS.get(p, p.title()) for p in platforms)) if platforms else "",
        ("Required hashtags: " + " ".join(tags)) if tags else "",
        str(c.get("captionTemplate") or "")) if x)
    total = _num(_find(c, "totalBudget", "budget", "budgetAmount"))
    left = _num(_find(c, "remainingBudget", "budgetRemaining", "availableBudget"), total)
    cpm = _num(_find(c, "creatorCpmRate", "cpmRate", "advertiserCpmRate", "payoutRate", "rate"))
    slots = _find(c, "slotsRemaining", "remainingSlots", "availableSlots", "slotsAvailable", "openSlots")
    if slots is None:
        count, taken = _find(c, "slotCount", "totalSlots", "maxSlots"), _find(c, "slotsFilled", "filledSlots", "slotsTaken",
                                                                          "reservedSlots") or 0
        slots = (_num(count) - _num(taken)) if count is not None else 1
    est = round(cpm, 2)  # paid per 1,000 verified views: one thousand views is the nominal unit for ranking
    return {
        "id": f"ugc_{cid}", "source": "ugcafrica", "ugc_campaign_id": cid,
        "title": str(c.get("title") or f"UGC Africa campaign {cid}"), "description": desc,
        "status": "open" if status in ("ACTIVE", "LIVE", "OPEN") else status.lower(),
        "currency": "usd", "gross_reward_amount": est, "net_reward_amount": est,
        "budget_amount": left or max(est * 100, 1.0), "gross_paid_out_amount": 0,
        "spots_remaining": int(slots), "accepted_submissions_per_user_limit": 1,
        "viewer_accepted_submissions_count": 0, "unresolved_submissions_count": 0,
        "business_goal_type": "clipping",
        "allowed_country_codes": [str(x).upper() for x in (c.get("targetCountries") or c.get("countries") or [])],
        "required_hashtags": tags, "ad_disclosure": any(t.lower().lstrip("#") in ("ad", "sponsored") for t in tags),
        "ugc_platforms": [ENUM_PLATFORM[p] for p in platforms if p in ENUM_PLATFORM],
        "raw": c,
    }
