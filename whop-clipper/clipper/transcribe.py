"""Word-level transcription with faster-whisper (free, runs on CPU)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


def transcribe(video: Path, model_size: str = "small") -> list[Word]:
    cache = video.with_suffix(".words.json")
    if cache.exists():
        return [Word(**w) for w in json.loads(cache.read_text())]
    import numpy as np
    from faster_whisper import WhisperModel

    # decode with ffmpeg ourselves: avoids PyAV version mismatches and handles any container
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True)
    words = [Word(w.start, w.end, w.word.strip())
             for seg in segments for w in (seg.words or []) if w.word.strip()]
    cache.write_text(json.dumps([asdict(w) for w in words]))
    return words
