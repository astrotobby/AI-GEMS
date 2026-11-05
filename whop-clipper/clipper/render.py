"""Cut the picked moment into a 1080x1920 clip with burned-in word-by-word captions."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .transcribe import Word

W, H = 1080, 1920


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], start: float, end: float, hook: str = "",
              font: str = "DejaVu Sans", chunk: int = 3, boxed: bool = False) -> str:
    """ASS subtitles: 3-word chunks, active word highlighted, optional hook banner."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},92,&H00FFFFFF,&H00FFFFFF,{"&H59000000" if boxed else "&H00000000"},&H64000000,-1,0,0,0,100,100,0,0,{3 if boxed else 1},{14 if boxed else 7},{0 if boxed else 3},2,60,60,{420 if boxed else 560},1
Style: Hook,{font},70,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(2.8, end - start))},Hook,,0,0,0,,{_esc(hook.upper())}")
    clip_words = [w for w in words if w.end > start and w.start < end]
    for g in range(0, len(clip_words), chunk):
        group = clip_words[g:g + chunk]
        for k, w in enumerate(group):
            nxt = group[k + 1].start if k + 1 < len(group) else (
                clip_words[g + chunk].start if g + chunk < len(clip_words) else w.end + 0.4)
            parts = [
                (r"{\c&H00E5FF&}" + _esc(x.text.upper()) + r"{\c&HFFFFFF&}") if x is w else _esc(x.text.upper())
                for x in group
            ]
            lines.append(f"Dialogue: 0,{_ts(w.start - start)},{_ts(min(nxt, end) - start)},Cap,,0,0,0,,{' '.join(parts)}")
    return head + "\n".join(lines) + "\n"


def _rotation(stream: dict) -> int:
    rot = stream.get("tags", {}).get("rotate")
    for sd in stream.get("side_data_list", []):
        rot = sd.get("rotation", rot)
    return abs(int(float(rot or 0))) % 180


def probe(path: Path) -> tuple[int, int]:
    """Displayed size: phone .MOVs store portrait video as landscape plus a 90-degree rotation flag."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                          "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    st = json.loads(out.stdout)["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    return (h, w) if _rotation(st) == 90 else (w, h)


# Audio codecs ffmpeg can decode. iPhone .MOVs also carry an Apple spatial-audio track
# (apple_apac) it can't, and mapping that track makes the whole command fail.
DECODABLE_AUDIO = {"aac", "mp3", "opus", "vorbis", "flac", "alac", "ac3", "eac3", "pcm_s16le",
                   "pcm_s24le", "pcm_f32le", "pcm_s16be", "pcm_s24be", "mp2", "wmav2", "amr_nb"}


def audio_stream(path: Path) -> int | None:
    """Index of the first decodable audio stream, or None when there is no usable audio."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                          "stream=index,codec_name", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout).get("streams", [])
    pick = [s for s in streams if s.get("codec_name") in DECODABLE_AUDIO] or streams
    return int(pick[0]["index"]) if pick else None


def filter_graph(src_w: int, src_h: int, ass_name: str) -> str:
    if src_w / src_h <= 9 / 16 + 0.01:  # already vertical
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[base]"
    else:  # landscape/square: blurred fill behind the full-width original, nothing cropped away
        base = (f"[0:v]split[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:4,eq=brightness=-0.08[bg];"
                f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80,setsar=1[base]")
    return f"{base};[base]ass={ass_name}[v]"


def render(src: Path, words: list[Word], start: float, end: float, out: Path,
           hook: str = "", font: str = "DejaVu Sans") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    ass.write_text(build_ass(words, start, end, hook, font))
    sw, sh = probe(src)
    a = audio_stream(src)
    audio = (["-map", f"0:{a}", "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]
             if a is not None else [])
    cmd = [
        "ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", str(src.resolve()), "-t", f"{end - start:.2f}",
        "-filter_complex", filter_graph(sw, sh, ass.name), "-map", "[v]", *audio,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-r", "30",
        "-movflags", "+faststart", out.name,
    ]
    subprocess.run(cmd, cwd=out.parent, check=True, capture_output=True)
    return out
