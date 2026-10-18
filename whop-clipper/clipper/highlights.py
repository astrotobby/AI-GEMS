"""Pick the strongest 20-60s moment and write the post copy.

Claude picks the moment when ANTHROPIC_API_KEY is set; otherwise a transcript
heuristic does (speech density, a hook in the first line, campaign keywords).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

from . import llm
from .transcribe import Word

HOOK_WORDS = {"you", "never", "secret", "why", "how", "stop", "best", "worst", "nobody",
              "money", "win", "lose", "crazy", "insane", "truth", "mistake", "signs", "reasons",
              "ways", "rules", "things", "here's"}
NUMBER_RE = re.compile(r"\d|\b(two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million)\b")
DANGLING_END = {"a", "an", "the", "and", "or", "but", "of", "to", "if", "because", "being", "that",
                "your", "my", "is", "are", "was", "with", "for", "in", "on", "at", "so"}
FILLER_START = {"um", "uh", "and", "but", "because", "like", "yeah", "anyway"}
STOPWORDS = {"about", "because", "there", "their", "every", "thing", "things", "people", "nobody",
             "single", "tells", "think", "really", "going", "would", "could", "should", "where",
             "which", "these", "those", "looks", "against", "honestly", "what's", "here's",
             "another", "trying", "night", "constantly", "you're", "they're", "that's", "first",
             "absolutely", "while", "never", "always", "something", "whoever", "action", "being"}
# phrases that signal the speaker is changing topic or going to a break - a clip shouldn't run through them
TRANSITIONS = ("anyway", "moving on", "short break", "take a break", "welcome back", "question from",
               "before we", "let's get into", "let us get into", "sponsor")


@dataclass
class Sentence:
    start: float
    end: float
    text: str


@dataclass
class Pick:
    start: float
    end: float
    title: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    hook: str = ""
    reason: str = ""
    source_text: str = ""


def sentences(words: list[Word]) -> list[Sentence]:
    out, buf = [], []
    for k, w in enumerate(words):
        buf.append(w)
        nxt = words[k + 1] if k + 1 < len(words) else None
        pause = nxt is not None and nxt.start - w.end > 0.8
        # Whisper often drops full stops on casual speech but keeps capitalising sentence starts
        cap_start = (nxt is not None and len(buf) >= 4 and nxt.text[:1].isupper()
                     and not re.match(r"^I('|$)", nxt.text))
        if re.search(r"[.?!]$", w.text) or len(buf) >= 30 or pause or cap_start:
            out.append(Sentence(buf[0].start, buf[-1].end, " ".join(x.text for x in buf)))
            buf = []
    if buf:
        out.append(Sentence(buf[0].start, buf[-1].end, " ".join(x.text for x in buf)))
    return out


def _windows(sents: list[Sentence], lo: int, hi: int):
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j].end - sents[i].start
            if dur > hi:
                break
            if dur >= lo:
                yield i, j


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())} - {
        "with", "your", "this", "that", "from", "must", "will", "clip", "clips", "video", "post"}


def _overlaps(a: float, b: float, used: list[tuple[float, float]]) -> bool:
    return any(a < u_end and b > u_start for u_start, u_end in used)


def heuristic(sents: list[Sentence], brief_text: str, lo: int, hi: int,
              exclude: list[tuple[float, float]] | None = None) -> tuple[int, int, str]:
    kws = _keywords(brief_text)
    best, best_score = None, -1e9
    for i, j in _windows(sents, lo, hi):
        if exclude and _overlaps(sents[i].start, sents[j].end, exclude):
            continue
        chunk = sents[i:j + 1]
        text = " ".join(s.text for s in chunk)
        tokens = re.findall(r"[a-z']+", text.lower())
        dur = chunk[-1].end - chunk[0].start
        first = chunk[0].text.lower()
        first_tokens = re.findall(r"[a-z']+", first)
        sc = len(tokens) / max(dur, 1) * 4                    # pace
        sc += 6 * any(t in HOOK_WORDS for t in first_tokens)  # hook up front
        sc += 3 * ("?" in first or "!" in first)
        sc += 3 * bool(NUMBER_RE.search(" ".join(s.text for s in chunk[:2]).lower()))
        last = re.findall(r"[a-z']+", chunk[-1].text.lower())
        sc -= 5 * bool(last and last[-1] in DANGLING_END)      # ends mid-thought
        sc += 2 * bool(re.search(r"[.?!]$", chunk[-1].text))
        sc -= 6 * bool(first_tokens[:1] and first_tokens[0] in FILLER_START)
        sc -= 8 * (len(first_tokens) < 4)                      # "Why?" alone needs context
        sc -= 10 * sum(any(p in s.text.lower() for p in TRANSITIONS) for s in chunk)  # crosses a topic change
        sc += 2 * len(kws & set(tokens)) / max(1, len(tokens)) * 20  # on-brief density
        sc -= abs(dur - (lo + hi) / 2) / 10                    # prefer mid-length
        if sc > best_score:
            best, best_score = (i, j), sc
    if best is None and exclude:
        raise ValueError("no unused moment of the right length left in this source")
    if best is None:  # source shorter than lo: take everything up to hi
        j = max((k for k, s in enumerate(sents) if s.end - sents[0].start <= hi), default=len(sents) - 1)
        best = (0, j)
    return best[0], best[1], f"heuristic score {best_score:.1f}"


SCHEMA = {
    "type": "object",
    "properties": {
        "start_index": {"type": "integer"},
        "end_index": {"type": "integer"},
        "hook": {"type": "string", "description": "<=7 word on-screen hook for the first 2 seconds"},
        "title": {"type": "string", "description": "YouTube Shorts title, <=90 chars"},
        "caption": {"type": "string", "description": "Instagram/TikTok caption, 1-3 short lines"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["start_index", "end_index", "hook", "title", "caption", "hashtags", "reason"],
    "additionalProperties": False,
}


def with_claude(sents: list[Sentence], campaign: dict, lo: int, hi: int, api_key: str, model: str,
                exclude: list[tuple[float, float]] | None = None, oauth_token: str = "") -> dict:
    lines = "\n".join(f"[{k}] {s.start:7.1f}-{s.end:7.1f}s  {s.text}" for k, s in enumerate(sents))
    avoid = ("Already used (do not overlap): " + ", ".join(f"{a:.0f}-{b:.0f}s" for a, b in exclude) + "\n"
             if exclude else "")
    prompt = (
        "You are a short-form video editor picking ONE clip for a paid clipping campaign.\n"
        f"The clip must run {lo}-{hi} seconds (end time of end_index minus start time of start_index), "
        "open directly on its strongest hook, stand alone without context, and keep the speaker's "
        "original meaning - never pick a moment that would misrepresent them.\n"
        f"{avoid}\n"
        f"<campaign title>{campaign.get('title')}</campaign>\n"
        f"<campaign_brief>\n{campaign.get('description')}\n</campaign_brief>\n\n"
        "Copy rules: include any @mentions, links or CTA the brief requires; no fake claims, "
        "results or statistics; 3-6 relevant hashtags without the # sign.\n\n"
        f"<transcript>\n{lines}\n</transcript>"
    )
    return llm.structured(prompt, SCHEMA, api_key, model, oauth_token)


def llm_error(e: Exception) -> str:
    """Short, human explanation of why the Claude step was skipped."""
    msg = str(e)
    if "credit balance" in msg and "Claude Code" not in msg:
        return ("Claude skipped: no Anthropic API credit - add a CLAUDE_CODE_OAUTH_TOKEN secret to use "
                "your Claude plan instead")
    if "credit balance" in msg:
        return f"Claude skipped: no API credit, and the Claude plan failed too ({msg.split('|')[-1].strip()[:300]})"
    if "authentication" in msg.lower() or "api key" in msg.lower():
        return "Claude skipped: ANTHROPIC_API_KEY is invalid"
    return f"Claude skipped ({e.__class__.__name__}: {msg[:120]})"


def fallback_copy(campaign: dict, text: str) -> dict:
    parts = [p for p in re.split(r"(?<=[.?!])\s", text.strip()) if len(p.split()) >= 5]
    first = re.sub(r"^(okay|ok|so|alright)[,]?\s+(so\s+)?", "", (parts or [text])[0], flags=re.I)
    if len(first) > 90:  # cut on a word boundary, never mid-word
        first = first[:90].rsplit(" ", 1)[0].rstrip(",;") + "…"
    first = first[:1].upper() + first[1:]
    counts = Counter(w for w in re.findall(r"[a-z]{5,}", text.lower()) if w not in STOPWORDS)
    topical = [w for w, _ in counts.most_common(3)]
    return {
        "hook": " ".join(first.rstrip("…").split()[:7]),
        "title": first,
        "caption": first,
        "hashtags": topical + ["shorts", "clips"],
    }


def dedupe_mentions(mentions: list[str]) -> list[str]:
    seen, out = set(), []
    for m in mentions:
        if m.lower() not in seen:
            seen.add(m.lower())
            out.append(m)
    return out


def pick(words: list[Word], campaign: dict, mentions: list[str], lo: int, hi: int,
         api_key: str = "", model: str = "claude-opus-5-5",
         exclude: list[tuple[float, float]] | None = None, oauth_token: str = "") -> Pick:
    sents = sentences(words)
    if not sents:
        raise ValueError("no speech found in source")
    data, i, j, reason = None, 0, 0, ""
    if api_key or oauth_token:
        try:
            data = with_claude(sents, campaign, lo, hi, api_key, model, exclude, oauth_token)
            i, j = data["start_index"], data["end_index"]
            ok = (0 <= i <= j < len(sents) and lo - 5 <= sents[j].end - sents[i].start <= hi + 5
                  and not _overlaps(sents[i].start, sents[j].end, exclude or []))
            if not ok:
                data = None
            else:
                reason = "claude: " + data.get("reason", "")
        except Exception as e:  # any API/parse problem -> heuristic
            reason = llm_error(e) + "; "
            data = None
    if data is None:
        i, j, why = heuristic(sents, f"{campaign.get('title')} {campaign.get('description')}", lo, hi, exclude)
        reason += why
        text = " ".join(s.text for s in sents[i:j + 1])
        data = fallback_copy(campaign, text)
    text = " ".join(s.text for s in sents[i:j + 1])
    caption = data["caption"]
    missing = [f"@{m}" for m in dedupe_mentions(mentions) if f"@{m}".lower() not in caption.lower()]
    if missing:
        caption = f"{caption}\n{' '.join(missing)}"
    return Pick(start=max(0.0, sents[i].start - 0.15), end=sents[j].end + 0.3,
                title=data["title"][:100], caption=caption,
                hashtags=[h.lstrip("#") for h in data.get("hashtags", [])][:8],
                hook=data.get("hook", ""), reason=reason, source_text=text)
