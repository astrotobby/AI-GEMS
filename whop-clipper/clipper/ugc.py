"""Make an original UGC-style promo video for a campaign.

    product page  ->  real facts + screenshots      (product.py)
    script        ->  Claude, or a template built from those facts
    voiceover     ->  ElevenLabs via Composio (natural), Piper as offline fallback
    visuals       ->  a new shot per sentence: product screenshots with slow zoom, and stock
                      b-roll when PEXELS_API_KEY is set; gradient only if there is nothing else
    captions      ->  word-by-word, timed from the actual voiceover

Only used for campaigns whose brief allows non-human content (scoring blocks briefs that need
a real person on camera or forbid AI). The script may only state what the brief or the
product's own page says - no invented results, prices or testimonials.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import requests

from . import product as product_mod
from .highlights import llm_error, sentences
from .render import H, W, build_ass
from .transcribe import transcribe

PIPER_REPO = "rhasspy/piper-voices"
PIPER_VOICE = "en/en_US/lessac/medium/en_US-lessac-medium"

SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "string", "description": "short product name, e.g. LifeMaxx"},
        "voiceover": {"type": "array", "items": {"type": "string"},
                      "description": "5-7 spoken sentences; the first is a scroll-stopping hook, the last a soft CTA"},
        "broll": {"type": "array", "items": {"type": "string"},
                  "description": "one 2-4 word stock-footage search per sentence, e.g. 'person journaling desk'"},
        "title": {"type": "string"},
        "caption": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product", "voiceover", "broll", "title", "caption", "hashtags"],
    "additionalProperties": False,
}


# ---- script -------------------------------------------------------------------------
def script_with_claude(campaign: dict, prod: product_mod.Product, api_key: str, model: str) -> dict:
    import anthropic

    prompt = (
        "Write the voiceover for a 25-40 second vertical promo video (TikTok/Reels/Shorts style) for this "
        "content-rewards campaign. Make it sound like a real creator talking: punchy hook in the first "
        "sentence, casual rhythm, short sentences, no corporate tone.\n"
        "Hard rules: use ONLY facts from the campaign brief or the product's own description below - no "
        "invented results, numbers, prices, reviews or personal experiences, and don't claim to have used it; "
        "include every link, @mention and CTA the brief requires in the caption; 3-6 hashtags without '#'.\n\n"
        f"<campaign_title>{campaign.get('title')}</campaign_title>\n"
        f"<campaign_brief>\n{campaign.get('description')}\n</campaign_brief>\n"
        f"<product_name>{prod.name}</product_name>\n"
        f"<product_description>\n{prod.description[:3000]}\n</product_description>"
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
    if not 3 <= len(data["voiceover"]) <= 9:
        raise ValueError("script shape off")
    return data


def _short_name(campaign: dict, prod: product_mod.Product) -> str:
    if prod.name:
        return re.split(r"[:\-–|]", prod.name)[0].strip()
    title = campaign.get("title") or ""
    m = re.search(r"(?:for|promoting|promote)\s+([A-Z][\w.\- ]{2,40}?)(?:[,.!:]|\s+-|\s+reach|$)", title)
    return re.sub(r"\.(app|com|io)$", "", (m.group(1) if m else title.split(":")[0]).strip(), flags=re.I)


def _soft(sentence: str) -> str:
    """Lower-case the first letter after a lead-in ("Okay, so ..."), but never a name or acronym."""
    first = sentence.split(" ", 1)[0]
    bare = first.rstrip(",.;:!?")
    if bare[:1].isupper() and (len(bare) == 1 or bare[1:].islower()) and bare.lower() in {
            "a", "an", "the", "your", "this", "it", "its", "every", "daily", "one", "most", "gamified",
            "proof", "real", "turn", "build", "track", "get", "make", "keep", "earn", "snap"}:
        return first.lower() + sentence[len(first):]
    return sentence


def script_template(campaign: dict, prod: product_mod.Product) -> dict:
    """No-LLM script built from the product's own description, so every claim is the product's."""
    name = _short_name(campaign, prod)
    first = re.split(r"(?<=[.!?])\s+", prod.description.strip())[0].strip() if prod.description else ""
    facts = [f for f in prod.facts() if f.rstrip(".") != first.rstrip(".")][:3]
    lines = [f"Okay, so {_soft(first)}" if first else f"Okay, have you seen {name} yet?"]
    for lead, fact in zip(("First,", "Plus,", "And"), facts):
        lines.append(f"{lead} {_soft(fact.rstrip('.'))}.")
    lines.append(f"It's called {name}. Link's in the caption if you want to check it out.")
    words = re.findall(r"[a-z]{5,}", " ".join(lines).lower())
    tags = [w for w in dict.fromkeys(words) if w not in {"caption", "check", "called", "first", "actually"}][:3]
    return {
        "product": name,
        "voiceover": [ln if ln.endswith((".", "!", "?")) else ln + "." for ln in lines],
        "broll": [name] * len(lines),
        "title": (first if name.lower() in first.lower() else f"{name}: {first}")[:90] if first
                 else f"Have you seen {name}?",
        "caption": first or f"Have you seen {name}?",
        "hashtags": list(dict.fromkeys([re.sub(r"\W", "", name.lower())] + tags + ["shorts"])),
    }


# ---- voice ----------------------------------------------------------------------------
VOICE_SETTINGS = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.2, "use_speaker_boost": True}


def voice_elevenlabs_direct(api_key: str, text: str, out: Path, voice_id: str) -> Path:
    """ElevenLabs REST API with your own key (ELEVENLABS_API_KEY)."""
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                      params={"output_format": "mp3_44100_128"},
                      headers={"xi-api-key": api_key, "accept": "audio/mpeg"},
                      json={"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": VOICE_SETTINGS},
                      timeout=180)
    if not r.ok:
        raise RuntimeError(f"ElevenLabs {r.status_code}: {r.text[:150]}")
    out.write_bytes(r.content)
    return out


def voice_elevenlabs(composio, text: str, out: Path, voice_id: str) -> Path:
    from .publish import find

    account = composio.account_for("elevenlabs")
    if not account:
        raise RuntimeError("no ElevenLabs connection in Composio")
    data = composio.run("ELEVENLABS_TEXT_TO_SPEECH", {
        "voice_id": voice_id, "text": text, "model_id": "eleven_multilingual_v2", "voice_settings": VOICE_SETTINGS,
    }, account)
    url = find(data, "s3url", "url")
    local = find(data, "file_path", "path", "file")
    if isinstance(url, str) and url.startswith("http"):
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        out.write_bytes(r.content)
    elif isinstance(local, str) and Path(local).exists():  # SDK auto-downloaded the file
        shutil.copy(local, out)
    else:
        raise RuntimeError(f"ElevenLabs returned no audio: {str(data)[:200]}")
    return out


def voice_piper(text: str, out: Path) -> Path:
    from huggingface_hub import hf_hub_download

    hf_hub_download(PIPER_REPO, PIPER_VOICE + ".onnx.json")
    model = hf_hub_download(PIPER_REPO, PIPER_VOICE + ".onnx")
    wav = out.with_suffix(".wav")
    subprocess.run(["python", "-m", "piper", "-m", model, "-f", str(wav)],
                   input=text.encode(), check=True, capture_output=True)
    return wav


def duration(path: Path) -> float:
    if path.suffix == ".wav":
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# ---- visuals -----------------------------------------------------------------------------
def stock_clip(query: str, dest: Path, api_key: str) -> Path | None:
    """One portrait stock clip from Pexels (free API key), or None."""
    if not api_key:
        return None
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": api_key},
                         params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 6},
                         timeout=30)
        r.raise_for_status()
        for v in r.json().get("videos", []):
            files = sorted((f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                            and (f.get("height") or 0) >= 1280 and (f.get("height") or 0) > (f.get("width") or 0)),
                           key=lambda f: f["height"])
            if files:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(requests.get(files[0]["link"], timeout=120).content)
                return dest
    except (requests.RequestException, ValueError):
        return None
    return None


def _segment(src: Path | None, d: float, out: Path) -> Path:
    """Render one d-second 1080x1920 shot: stock video, a still with slow zoom, or a gradient."""
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an"]
    if src is None:
        cmd = ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0=0x1b0b3a:c1=0x0b5a7a:speed=0.02:d={d:.2f}:r=30"]
        vf = "setsar=1"
    elif src.suffix.lower() in (".mp4", ".mov", ".webm"):
        cmd = ["-stream_loop", "-1", "-i", str(src)]
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},eq=brightness=-0.06,setsar=1"
    else:
        frames = int(d * 30) + 1
        cmd = ["-loop", "1", "-i", str(src)]
        vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=30:5,eq=brightness=-0.18[bg];[b]scale={W - 80}:{H - 160}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,zoompan=z='min(1+0.0012*on,1.12)':x='iw/2-(iw/zoom/2)':"
              f"y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps=30,setsar=1")
        subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd, "-filter_complex", vf, "-frames:v", str(frames),
                        *enc, str(out)], check=True)
        return out
    subprocess.run(["ffmpeg", "-y", "-v", "error", *cmd, "-t", f"{d:.3f}", "-vf", vf, *enc, str(out)], check=True)
    return out


def align(script: str, heard: list, total: float) -> list:
    """Caption words = the script's exact words (no mis-hearings like "URNEX" for "earn XP"),
    timed from what speech recognition heard; unmatched words are spread between matched neighbours."""
    from difflib import SequenceMatcher

    from .transcribe import Word

    norm = lambda w: re.sub(r"[^a-z0-9]", "", w.lower())
    toks = script.split()
    times: list[tuple[float, float] | None] = [None] * len(toks)
    sm = SequenceMatcher(None, [norm(t) for t in toks], [norm(w.text) for w in heard], autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = (heard[b + k].start, heard[b + k].end)
    # fill gaps by interpolating between the surrounding known times
    i = 0
    while i < len(toks):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(toks) and times[j] is None:
            j += 1
        t0 = times[i - 1][1] if i > 0 else 0.0
        t1 = times[j][0] if j < len(toks) else total
        step = max(0.05, (t1 - t0) / (j - i))
        for k in range(i, j):
            times[k] = (t0 + (k - i) * step, t0 + (k - i + 1) * step)
        i = j
    return [Word(st, en, t) for t, (st, en) in zip(toks, times)]


# ---- assemble ------------------------------------------------------------------------------
def make(campaign: dict, out: Path, api_key: str = "", model: str = "claude-opus-5-5",
         product_links: list[str] | None = None, mentions: list[str] | None = None,
         font: str = "DejaVu Sans", composio=None, voice_id: str = "EXAVITQu4vr4xnSDxMaL",
         pexels_key: str = "", whisper_model: str = "base", elevenlabs_key: str = "") -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.with_suffix("")
    work.mkdir(exist_ok=True)
    notes = []

    prod = product_mod.Product()
    for link in product_links or []:
        prod = product_mod.fetch(link, work / "product")
        if prod.description or prod.images:
            break
    notes.append(f"{len(prod.images)} product images")

    script = None
    if api_key:
        try:
            script = script_with_claude(campaign, prod, api_key, model)
            notes.append("Claude script")
        except Exception as e:
            notes.append(llm_error(e))
    if script is None:
        script = script_template(campaign, prod)
        notes.append("template script from the product page")
    text = " ".join(script["voiceover"])

    audio = None
    if elevenlabs_key:
        try:
            audio = voice_elevenlabs_direct(elevenlabs_key, text, work / "voice.mp3", voice_id)
            notes.append("ElevenLabs voice")
        except Exception as e:
            notes.append(f"ElevenLabs key failed ({str(e)[:80]})")
    if audio is None and composio is not None:
        try:
            audio = voice_elevenlabs(composio, text, work / "voice.mp3", voice_id)
            notes.append("ElevenLabs voice")
        except Exception as e:
            notes.append(f"ElevenLabs failed ({str(e)[:80]})")
    if audio is None:
        audio = voice_piper(text, work / "voice")
        notes.append("Piper voice")
    total = duration(audio)

    words = align(text, transcribe(audio, whisper_model), total)
    spans = [(s.start, s.end) for s in sentences(words)] or [(0.0, total)]
    # cut points between sentences, so each shot covers one spoken sentence
    cuts = [0.0] + [b[0] for b in spans[1:]] + [total]
    shots = []
    for i in range(len(cuts) - 1):
        query = script["broll"][min(i, len(script["broll"]) - 1)] if script.get("broll") else ""
        stock = stock_clip(query, work / f"stock{i:02d}.mp4", pexels_key) if i % 2 == 0 else None
        img = prod.images[(i // 2 if pexels_key else i) % len(prod.images)] if prod.images else None
        shots.append(_segment(stock or img, max(0.5, cuts[i + 1] - cuts[i]), work / f"shot{i:02d}.mp4"))
    if pexels_key:
        notes.append("stock b-roll")

    (work / "shots.txt").write_text("".join(f"file '{s.name}'\n" for s in shots))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "shots.txt",
                    "-c", "copy", "visuals.mp4"], cwd=work, check=True)
    hook = " ".join(script["voiceover"][0].split()[:7])
    # no top banner: product screenshots carry their own headline; boxed captions stay readable on them
    (work / "captions.ass").write_text(build_ass(words, 0.0, total, font=font, boxed=True))
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-i", "visuals.mp4", "-i", str(audio.resolve()),
        "-filter_complex", "[0:v]ass=captions.ass,format=yuv420p[v]", "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-shortest",
        "-movflags", "+faststart", str(out.resolve()),
    ], cwd=work, check=True)

    caption = script["caption"]
    for extra in (product_links or []) + [f"@{m}" for m in (mentions or [])]:
        if extra.lower() not in caption.lower():
            caption += f"\n{extra}"
    return {"path": str(out), "start": 0.0, "end": round(total, 2), "title": script["title"][:100],
            "caption": caption, "hashtags": [h.lstrip("#") for h in script["hashtags"]][:8],
            "hook": hook, "reason": "; ".join(notes), "text": text[:500]}
