"""Synthesize demo audio from a scripted transcript (macOS TTS, multi-voice).

Produces a 16 kHz mono WAV where each line is spoken by a per-role voice and
placed at its transcript timestamp (optionally time-compressed). This gives a
legal, ground-truth-aligned "recording" to play into the live ambient mode —
no copyrighted TV audio needed.

Usage:
    python make_audio.py data/transcript.txt          # real-time (x1)
    python make_audio.py data/transcript.txt 3        # gaps compressed 3x
    python make_audio.py data/transcript_stroke.txt 6
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000

VOICES = {
    "CODE LEADER": "Daniel",
    "PHYSICIAN": "Daniel",
    "NURSE 1": "Samantha",
    "NURSE 2": "Karen",
    "RECORDER": "Moira",
    "RESP THERAPIST": "Rishi",
    "CT TECH": "Tessa",
    "CHARGE NURSE": "Tessa",
}
DEFAULT_VOICE = "Samantha"

_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2})\]\s+([A-Z0-9 ]+):\s+(.*)$")


def tts_line(voice: str, text: str, workdir: Path, idx: int) -> np.ndarray:
    aiff = workdir / f"line{idx}.aiff"
    wav = workdir / f"line{idx}.wav"
    subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", f"LEI16@{SAMPLE_RATE}", "-c", "1",
         str(aiff), str(wav)],
        check=True,
    )
    with wave.open(str(wav), "rb") as f:
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def main() -> None:
    transcript = Path(sys.argv[1] if len(sys.argv) > 1 else "data/transcript.txt")
    scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

    lines = []
    for raw in transcript.read_text().splitlines():
        m = _LINE_RE.match(raw.strip())
        if m:
            mm, ss, speaker, text = m.groups()
            lines.append((int(mm) * 60 + int(ss), speaker.strip(), text))

    out_dir = Path("data/audio")
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_x{scale:g}" if scale != 1 else ""
    out_path = out_dir / f"{transcript.stem}{suffix}.wav"

    chunks: list[np.ndarray] = []
    cursor = 0.0  # seconds of audio written so far
    max_drift = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for idx, (t_sim, speaker, text) in enumerate(lines):
            target = t_sim / scale
            if target > cursor:
                gap = int((target - cursor) * SAMPLE_RATE)
                chunks.append(np.zeros(gap, dtype=np.int16))
                cursor = target
            else:
                max_drift = max(max_drift, (cursor - target) * scale)
            speech = tts_line(VOICES.get(speaker, DEFAULT_VOICE), text, workdir, idx)
            chunks.append(speech)
            chunks.append(np.zeros(int(0.35 * SAMPLE_RATE), dtype=np.int16))
            cursor += len(speech) / SAMPLE_RATE + 0.35
            print(f"[{t_sim//60:02d}:{t_sim%60:02d}] {speaker:<14} -> audio {cursor:6.1f}s")

    audio = np.concatenate(chunks)
    with wave.open(str(out_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.tobytes())

    print(f"\nwrote {out_path}  ({len(audio)/SAMPLE_RATE:.0f}s, timescale x{scale:g}, "
          f"max sim-time drift {max_drift:.0f}s)")
    if max_drift > 20:
        print("warning: drift exceeds protocol tolerance — use a smaller scale")


if __name__ == "__main__":
    main()
