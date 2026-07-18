"""Live ambient mode: audio -> local ASR (Whisper) -> extractor -> protocol router.

The system actually LISTENS. Audio comes either from the microphone (true
ambient capture) or from a WAV file streamed in real time (deterministic
demo). Chunks of ~8 s are transcribed locally with faster-whisper — audio
never leaves the machine; only transcribed text chunks go to the extraction
model. Events, guidance, and rubric activations are pushed onto a queue that
the SSE endpoint drains, so the live view fills in exactly as with replays.

`timescale` maps real audio seconds to protocol (sim) seconds, so a
time-compressed demo recording still exercises the real ACLS intervals.
"""

from __future__ import annotations

import queue
import subprocess
import threading
import time
import wave
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .extractor import Extractor, TranscriptLine
from .models import ClinicalEvent
from .rubrics import ProtocolRouter

SAMPLE_RATE = 16000
CHUNK_S = 8.0
ASR_MODEL = "small.en"
ASR_PROMPT = (
    "Hospital resuscitation code. Epinephrine, amiodarone, asystole, v-fib, "
    "PEA, defibrillation, rhythm check, ROSC, intubation, tenecteplase, "
    "NIHSS, last known well, code blue, code stroke."
)


class LiveSession:
    """One live listening session. Drain .queue until a {'kind':'done'} message."""

    def __init__(
        self,
        source: str = "file",
        audio_path: Path | None = None,
        timescale: float = 1.0,
        speak: bool = False,
        listen: bool = False,
    ):
        self.source = source
        self.audio_path = audio_path
        self.timescale = timescale
        self.speak = speak
        self.listen = listen        # file mode: also play the audio out loud
        self._player: subprocess.Popen | None = None
        self.queue: queue.Queue[dict] = queue.Queue()
        self.stop_flag = threading.Event()
        self.code_start = datetime.now().replace(microsecond=0)
        from .store import _load_dotenv

        _load_dotenv()
        self.extractor = Extractor(code_start=self.code_start)
        self.router = ProtocolRouter()
        self._last_tick = -1
        self.events: list[ClinicalEvent] = []
        self.asr_log: list[dict] = []
        self._dumped = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self.stop_flag.set()
        if self._player and self._player.poll() is None:
            self._player.terminate()
        self._dump()

    # ------------------------------------------------------------------ audio

    def _audio_chunks(self):
        """Yield (chunk_start_real_s, float32 mono 16k samples)."""
        if self.source == "mic":
            import sounddevice as sd

            frames = int(CHUNK_S * SAMPLE_RATE)
            t = 0.0
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
                while not self.stop_flag.is_set():
                    data, _ = stream.read(frames)
                    yield t, data[:, 0].copy()
                    t += CHUNK_S
        else:
            with wave.open(str(self.audio_path), "rb") as f:
                assert f.getframerate() == SAMPLE_RATE and f.getnchannels() == 1
                total = f.getnframes()
                pos = 0
                if self.listen:
                    # speakers and ASR reader start together, both real-time paced
                    self._player = subprocess.Popen(
                        ["afplay", str(self.audio_path)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                start_wall = time.monotonic()
                while pos < total and not self.stop_flag.is_set():
                    frames = min(int(CHUNK_S * SAMPLE_RATE), total - pos)
                    raw = np.frombuffer(f.readframes(frames), dtype=np.int16)
                    t = pos / SAMPLE_RATE
                    # pace to real time: don't get ahead of the recording
                    # (CODECLOCK_LIVE_FAST=1 skips pacing for offline verification)
                    import os

                    if not os.environ.get("CODECLOCK_LIVE_FAST"):
                        ahead = t - (time.monotonic() - start_wall)
                        if ahead > 0:
                            time.sleep(ahead)
                    yield t, raw.astype(np.float32) / 32768.0
                    pos += frames

    # ------------------------------------------------------------------- main

    def _run(self) -> None:
        try:
            from faster_whisper import WhisperModel

            self.queue.put({"kind": "asr_status", "text": "loading speech model..."})
            model = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
            self.queue.put({"kind": "asr_status", "text": "listening"})

            for chunk_start, samples in self._audio_chunks():
                if self.stop_flag.is_set():
                    break
                segments, _ = model.transcribe(
                    samples, language="en", vad_filter=False,
                    initial_prompt=ASR_PROMPT, beam_size=3,
                    word_timestamps=True,
                )
                lines = []
                for seg in segments:
                    text = seg.text.strip()
                    if not text:
                        continue
                    # anchor on the first spoken word — segment starts get
                    # padded into preceding silence, which skews protocol timing
                    start = seg.words[0].start if seg.words else seg.start
                    sim_s = int((chunk_start + start) * self.timescale)
                    lines.append(TranscriptLine(offset_seconds=sim_s, text=text))
                    self.asr_log.append({"t": sim_s, "text": text})
                    self.queue.put({"kind": "asr", "t": sim_s, "text": text})
                self._process(lines, chunk_end_sim=int((chunk_start + CHUNK_S) * self.timescale))

            self._finish()
        except Exception as exc:  # surfaced to the UI rather than dying silently
            self.queue.put({"kind": "error", "text": f"{type(exc).__name__}: {exc}"})
            self.queue.put({"kind": "done"})

    def _process(self, lines: list[TranscriptLine], chunk_end_sim: int) -> None:
        events = sorted(self.extractor.feed(lines), key=lambda e: e.timestamp)
        # interleave clock ticks with events so escalations (e.g. OVERDUE just
        # before a late dose lands) fire in true chronological order
        for event in events:
            t = (event.timestamp - self.code_start).total_seconds()
            self._tick_to(int(t) - 1)
            self.events.append(event)
            self.queue.put({"kind": "event_obj", "event": event, "t": t})
            activations, guidance = self.router.on_event(event)
            for a in activations:
                self.queue.put({"kind": "rubric_obj", "activation": a, "t": t})
            for g in guidance:
                self._push_guidance(g, t)
            if event.type == "milestone" and "rosc" in event.entity.lower():
                self.queue.put({"kind": "status", "status": "rosc"})
        self._tick_to(chunk_end_sim)

    def _tick_to(self, t_target: int) -> None:
        for t in range(self._last_tick + 1, t_target + 1):
            now = self.code_start + timedelta(seconds=t)
            for g in self.router.on_tick(now):
                self._push_guidance(g, t)
            self.queue.put({"kind": "tick", "t": t})
        self._last_tick = max(self._last_tick, t_target)

    def _push_guidance(self, g, t: float) -> None:
        self.queue.put({"kind": "guidance_obj", "guidance": g, "t": t})
        if self.speak and g.urgency in ("due_now", "alert"):
            subprocess.Popen(["say", "-v", "Samantha", g.message.split("—")[0]])

    def _finish(self) -> None:
        if self._player and self._player.poll() is None:
            self._player.terminate()
        self._dump()
        self.queue.put({"kind": "finished", "events": self.events,
                        "guidance": self.router.guidance_log})
        self.queue.put({"kind": "done"})

    def _dump(self) -> None:
        """Save the full session (heard / extracted / prompted) for post-hoc
        review — e.g. comparing agent guidance against what a real crew did."""
        if self._dumped or not self.asr_log:
            return
        self._dumped = True
        import json

        out_dir = Path("data/live_sessions")
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.code_start.strftime("%Y%m%d-%H%M%S")
        payload = {
            "started_at": self.code_start.isoformat(),
            "source": self.source,
            "timescale": self.timescale,
            "heard": self.asr_log,
            "events": [
                {
                    "t": (e.timestamp - self.code_start).total_seconds(),
                    "type": e.type, "entity": e.entity, "dose": e.dose,
                    "value": e.value, "source_utterance": e.source_utterance,
                    "confidence": e.confidence,
                }
                for e in self.events
            ],
            "guidance": [
                {
                    "t": (g.issued_at - self.code_start).total_seconds(),
                    "urgency": g.urgency, "message": g.message,
                    "rule_id": g.rule_id, "rubric_id": g.rubric_id,
                }
                for g in self.router.guidance_log
            ],
            "rubric_activations": [
                {
                    "t": (a.activated_at - self.code_start).total_seconds(),
                    "rubric_id": a.rubric_id, "reason": a.reason,
                }
                for a in self.router.activations
            ],
        }
        (out_dir / f"session-{stamp}.json").write_text(json.dumps(payload, indent=2))
