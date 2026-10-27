"""Turn a free-text Whop bounty description into a structured clipping brief."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
MENTION_RE = re.compile(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})")
VIEWS_RE = re.compile(r"(\d[\d,.]*)\s*(k|K)?\+?\s*(?:public\s+)?(?:organic\s+)?views?", re.I)
RANGE_RE = re.compile(r"(\d{1,3})\s*(?:–|-|to)\s*(\d{1,3})\s*(?:second|sec|s\b)", re.I)
MINUTES_RE = re.compile(r"(\d{1,2})\s*(?:–|-|to)\s*(\d{1,2})\s*minutes?\s+long", re.I)

SOURCE_HOSTS = ("drive.google.com", "youtube.com", "youtu.be", "dropbox.com", "vimeo.com",
                "twitch.tv", "kick.com", "rumble.com", "x.com/", "twitter.com/")
PLATFORM_WORDS = {
    "tiktok": ("tiktok",),
    "instagram": ("instagram", "reels", " ig "),
    "youtube": ("youtube shorts", "yt shorts", "shorts"),
}


@dataclass
class Brief:
    source_links: list[str] = field(default_factory=list)
    reference_links: list[str] = field(default_factory=list)
    required_mentions: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    min_views: int = 0
    clip_seconds: tuple[int, int] | None = None
    long_form: bool = False
    livestream_required: bool = False
    needs_physical_presence: bool = False
    clip_count: int = 1
    vertical: bool = True
    repeatable: bool = False       # campaign accepts several posts from the same person
    needs_real_person: bool = False  # face/voice of a real person, or AI content forbidden
    product_links: list[str] = field(default_factory=list)  # links the post must carry (UGC)
    screenshot_proof: bool = False  # proof is screenshots of joining/inviting, not a posted video


def _num(text: str, k: str | None) -> int:
    n = float(text.replace(",", ""))
    return int(n * 1000) if k else int(n)


def parse(description: str, title: str = "") -> Brief:
    text = f"{title}\n{description or ''}"
    low = f" {text.lower()} "
    b = Brief()

    for url in URL_RE.findall(text):
        url = url.rstrip(".,;")
        if any(h in url for h in SOURCE_HOSTS) and "docs.google.com" not in url:
            b.source_links.append(url)
        else:
            b.reference_links.append(url)

    b.required_mentions = sorted({m.rstrip(".") for m in MENTION_RE.findall(text)})
    b.platforms = [p for p, words in PLATFORM_WORDS.items() if any(w in low for w in words)]

    views = [_num(n, k) for n, k in VIEWS_RE.findall(text)]
    b.min_views = max(views) if views else 0

    for m in RANGE_RE.finditer(text):
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        near = text[max(0, m.start() - 40):m.end() + 25].lower()
        # only ranges that describe the clip itself, not e.g. "charts 8 to 15 seconds on screen"
        if hi >= 10 and re.search(r"clip|moment|video|short|reel|long|length", near):
            b.clip_seconds = (lo, hi)
            break
    if MINUTES_RE.search(text) or "long-form" in low or "long form" in low:
        b.long_form = True

    # e.g. "submit a screenshot of you inside the main chat" / "invite at least 1 other clipper"
    b.screenshot_proof = bool(re.search(r"screenshot of (you|the friend|your)|invite at least \d", low))

    b.livestream_required = "livestream" in low or "live stream" in low or "go live" in low
    b.needs_physical_presence = any(w in low for w in (
        "head mount", "military base", "on air", "in person", "your location", "record yourself"))

    b.repeatable = bool(re.search(
        r"as many (clips|posts|videos)|multiple (clips|posts|videos)|unlimited (clips|posts|submissions)", low))
    b.needs_real_person = bool(re.search(
        r"no ai|not ai|ai[- ]generated (content )?(is )?(not allowed|prohibited|will be rejected)|"
        r"show (your|ur) face|face on camera|on[- ]camera|film yourself|your (own )?voice|real person\b|"
        r"selfie|talking head of you|unboxing|in your hands|wear(ing)? (it|the)", low))
    b.product_links = [u for u in dict.fromkeys(b.reference_links)
                       if not any(h in u for h in ("whop.com", "discord.gg", "docs.google.com", "drive.google.com"))]

    m = re.search(r"(?:clip|cut|attach)\s+(\d{1,2})\s+(?:\w+\s+){0,3}(?:clips|moments|videos)", low)
    if m:
        b.clip_count = int(m.group(1))
    b.vertical = "16:9" not in low or "9:16" in low
    return b
