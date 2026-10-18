"""Make an original UGC-style promo video for a campaign: script -> voiceover -> 9:16 slideshow.

Only used for campaigns whose brief allows non-human, non-camera content (scoring blocks
briefs that need a real person on camera or forbid AI). The script never invents results,
prices or claims: it may only use facts stated in the brief.
"""
from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

from .render import W, H, _esc

VOICE_REPO = "rhasspy/piper-voices"
VOICE = "en/en_US/lessac/medium/en_US-lessac-medium"

SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string"},
        "slides": {"type": "array", "items": {"type": "string"},
                   "description": "5-7 short on-screen lines, the first one is the hook, the last one the CTA"},
        "voiceover": {"type": "array", "items": {"type": "string"},
                      "description": "one spoken sentence per slide, same count as slides"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product", "slides", "voiceover", "title", "caption", "hashtags"],
    "additionalProperties": False,
}


def script_with_claude(campaign: dict, api_key: str, model: str) -> dict:
    import anthropic

    prompt = (
        "Write a short, authentic-sounding vertical promo video (slideshow with voiceover) for this "
        "content-rewards campaign. Rules: use ONLY facts stated in the brief - no invented results, "
        "numbers, prices, testimonials or personal experiences; don't pretend to be a customer; keep it "
        "conversational, not like an ad; 5-7 slides of at most 9 words each; include every link, "
        "@mention and CTA the brief requires in the caption; 3-6 hashtags without '#'.\n\n"
        f"<campaign_title>{campaign.get('title')}</campaign_title>\n"
        f"<campaign_brief>\n{campaign.get('description')}\n</campaign_brief>"
    )
    resp = anthropic.Anthropic(api_key=api_key).beta.messages.create(
        model=model, max_tokens=4000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    if not 3 <= len(data["slides"]) <= 8 or len(data["voiceover"]) != len(data["slides"]):
        raise ValueError("script shape off")
    return data


def script_template(campaign: dict) -> dict:
    """No-LLM fallback: build slides from the brief's own bullet points."""
    title = campaign.get("title") or ""
    desc = campaign.get("description") or ""
    m = re.search(r"(?:for|promoting|promote)\s+([A-Z][\w.\- ]{2,40}?)(?:[,.!:]|\s+-|\s+reach|$)", title)
    product = (m.group(1) if m else title.split(":")[0]).strip()
    product = re.sub(r"\.(app|com|io)$", "", product, flags=re.I)
    bullets = [re.sub(r"^[\s\-•*\d.)]+", "", ln).strip() for ln in desc.splitlines()]
    # briefs are mostly instructions to the creator; keep only lines that describe the product
    instruction = re.compile(
        r"https?://|^[A-Z ]{3,}:|\b(submit|livestream|reject|screen ?record|views|discord|whop|must|account|"
        r"create|include|post|tag|attach|upload|keep|use|make sure|let the|do not|don't|caption|slideshow|"
        r"video|clip|requirement)\b", re.I)
    facts = [b for b in bullets if 4 <= len(b.split()) <= 14 and not instruction.search(b)]
    # an app-store style slug ("lifemaxx-30-day-life-reset") usually carries the product's own tagline
    slug = next((m.group(1) for u in re.findall(r"https?://\S+", desc)
                 for m in [re.search(r"/app/([a-z0-9-]{6,})", u)] if m), "")
    tagline = " ".join(w for w in slug.split("-") if not w.isdigit() or len(w) < 4)
    body = facts[:3] or ([f"It's called {product}"] + ([tagline.capitalize()] if tagline else []))
    slides = [f"Have you seen {product}?"] + body + [f"Check out {product} - link below"]
    return {
        "product": product,
        "slides": slides,
        "voiceover": slides,
        "title": f"{product} in 30 seconds",
        "caption": f"Quick look at {product}.",
        "hashtags": [re.sub(r"\W", "", product.lower())[:20] or "app", "shorts"],
    }


def _voice_model() -> Path:
    from huggingface_hub import hf_hub_download

    hf_hub_download(VOICE_REPO, VOICE + ".onnx.json")
    return Path(hf_hub_download(VOICE_REPO, VOICE + ".onnx"))


def _tts(text: str, out: Path, model: Path) -> float:
    subprocess.run(["python", "-m", "piper", "-m", str(model), "-f", str(out)],
                   input=text.encode(), check=True, capture_output=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(slides: list[str], durations: list[float], font: str = "DejaVu Sans") -> str:
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Slide,{font},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,3,5,90,90,0,1
Style: Hook,{font},104,&H0000E5FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,5,90,90,0,1
Style: Count,{font},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,140,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines, t = [], 0.0
    for i, (text, d) in enumerate(zip(slides, durations)):
        style = "Hook" if i == 0 else "Slide"
        lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + d)},{style},,0,0,0,,{{\\fad(180,120)}}{_esc(text)}")
        lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + d)},Count,,0,0,0,,{i + 1}/{len(slides)}")
        t += d
    return head + "\n".join(lines) + "\n"


def render(script: dict, out: Path, font: str = "DejaVu Sans") -> Path:
    """Voice every slide, then draw the slides over a slowly shifting gradient, synced to the voice."""
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.with_suffix("")
    work.mkdir(exist_ok=True)
    model = _voice_model()
    parts, durations = [], []
    for i, line in enumerate(script["voiceover"]):
        wav = work / f"vo{i:02d}.wav"
        durations.append(max(1.8, _tts(line, wav, model) + 0.35))
        parts.append(wav)
    total = sum(durations)
    concat = work / "vo.txt"
    concat.write_text("".join(f"file '{p.name}'\nduration {d:.3f}\n" for p, d in zip(parts, durations)))
    (work / "slides.ass").write_text(build_ass(script["slides"], durations, font))
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x1b0b3a:c1=0x0b5a7a:c2=0x3a0b2e:x0=0:y0=0:x1={W}:y1={H}:"
                             f"nb_colors=3:speed=0.01:d={total:.2f}:r=30",
        "-f", "concat", "-safe", "0", "-i", concat.name,
        "-filter_complex", "[0:v]ass=slides.ass,format=yuv420p[v]", "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "6M", "-bufsize", "12M",
        "-c:a", "aac", "-b:a", "160k", "-af", "apad,loudnorm=I=-14:TP=-1.5:LRA=11", "-shortest",
        "-movflags", "+faststart", str(out.resolve()),
    ]
    subprocess.run(cmd, cwd=work, check=True, capture_output=True)
    return out


def make(campaign: dict, out: Path, api_key: str = "", model: str = "claude-opus-5-5",
         product_links: list[str] | None = None, mentions: list[str] | None = None,
         font: str = "DejaVu Sans") -> dict:
    reason = ""
    script = None
    if api_key:
        try:
            script = script_with_claude(campaign, api_key, model)
            reason = "claude script"
        except Exception as e:
            reason = f"claude failed ({e.__class__.__name__}: {str(e)[:120]}); "
    if script is None:
        script = script_template(campaign)
        reason += "template script"
    caption = script["caption"]
    for extra in (product_links or []) + [f"@{m}" for m in (mentions or [])]:
        if extra.lower() not in caption.lower():
            caption += f"\n{extra}"
    render(script, out, font)
    return {"path": str(out), "start": 0.0, "end": 0.0, "title": script["title"][:100],
            "caption": caption, "hashtags": [h.lstrip("#") for h in script["hashtags"]][:8],
            "hook": script["slides"][0], "reason": reason, "text": " | ".join(script["slides"])[:500]}
