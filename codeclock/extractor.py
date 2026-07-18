"""Streaming clinical-event extractor (component 1 of 5).

Feeds the transcript to Claude in small chunks — simulating a live audio
stream — and yields ClinicalEvent records as each chunk is processed. Every
event carries the exact source utterance it came from and a self-evaluated
confidence score. Prior events are passed back as context so the model can
resolve references ("second epi") and avoid double-logging recorder echoes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterator, Literal

import anthropic
from pydantic import BaseModel, Field

from .models import ClinicalEvent

MODEL = "claude-opus-4-8"
CHUNK_SIZE = 4  # transcript lines per extraction call (~15-30s of audio)

_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2})\]\s+(.*)$")


class TranscriptLine(BaseModel):
    offset_seconds: int
    text: str  # "SPEAKER: utterance"

    @property
    def stamp(self) -> str:
        return f"{self.offset_seconds // 60:02d}:{self.offset_seconds % 60:02d}"


class ExtractedEvent(BaseModel):
    """What Claude emits per event — converted to ClinicalEvent by the harness."""

    timestamp: str = Field(
        description="Elapsed time MM:SS of the utterance the event occurred in, "
        "copied from that line's [MM:SS] marker."
    )
    type: Literal[
        "medication", "rhythm_check", "shock", "procedure", "assessment", "milestone"
    ]
    entity: str = Field(
        description="Normalized clinical entity, e.g. 'epinephrine', 'v-fib', "
        "'defibrillation', 'intubation', 'ROSC'."
    )
    dose: str | None = Field(
        description="Dose if stated, normalized like '1 mg' or '300 mg'; null otherwise."
    )
    value: str | None = Field(
        description="Stated measurement or clock time carried by the event, e.g. "
        "NIHSS score '11', glucose '122', last-known-well time '09:30', code-start "
        "wall-clock time '14:32'; null if none."
    )
    source_utterance: str = Field(
        description="The exact utterance text this event came from, copied verbatim "
        "from the transcript (speaker label included)."
    )
    confidence: float = Field(
        description="Self-evaluated confidence 0-1 that this event, its type, entity, "
        "dose, and timestamp are all correct. Use < 0.75 when the utterance is "
        "ambiguous, indirect, or the dose/entity is uncertain."
    )


class ChunkExtraction(BaseModel):
    events: list[ExtractedEvent]


SYSTEM_PROMPT = """\
You are the extraction component of a real-time emergency-code documentation \
agent (cardiac arrest / code blue, code stroke, and similar). You receive a \
live transcript in small chunks as it happens and emit structured clinical \
events. A separate deterministic protocol engine consumes your events — your \
only job is faithful extraction, not clinical judgment.

Rules:
- Log an event only when the utterance states it actually happened ("shock \
delivered", "epi is in"). Do NOT log intentions, orders not yet carried out \
("let's draw up amiodarone", "charging to 200"), questions, or refusals.
- One event per real-world occurrence. When a recorder or second speaker \
confirms an event already announced ("Copy, epi one milligram in at two \
forty-five"), do not emit a duplicate — the original announcement is the event.
- Events already extracted from earlier chunks are listed under PRIOR EVENTS. \
Never re-emit them; use them to resolve references like "second epi".
- A rhythm check and the rhythm found are ONE rhythm_check event whose entity \
is the rhythm (e.g. 'v-fib', 'asystole', 'organized rhythm'). If a check is \
announced but no rhythm stated yet, entity is 'rhythm check - result pending'.
- source_utterance must be copied verbatim from the transcript line, including \
the speaker label if present, excluding the [MM:SS] marker.
- Transcripts may come from speech recognition and can mis-render clinical \
terms (e.g. "EP" for epi, "a meoduron" for amiodarone, "a systole" for \
asystole, "pulse-less" for pulseless). When context makes the intended term \
clear, normalize entity to the correct clinical term but keep \
source_utterance verbatim as heard, and lower confidence to reflect the \
ambiguity.
- Real-world EMS/bodycam audio: AED/monitor voice prompts are loggable — \
"shock advised" is a rhythm_check with entity 'shock advised' (the device \
classified the rhythm as shockable); "no shock advised" is a rhythm_check \
with entity 'no shock advised'. The advisory itself is NOT a delivered \
shock — log a shock only when delivery is evident ("shock delivered", \
"everybody clear" followed by confirmation, or "delivering shock" from the \
device). "I've got a pulse" / "we have pulses" is ROSC. Contemporaneous \
third-person narration of an action happening now ("they're pushing epi") \
is loggable; retrospective summary ("they had shocked her twice earlier") \
is not.
- timestamp is the [MM:SS] marker of that line.
- Milestones: code start (entity 'code start' for an arrest, 'code stroke' for \
a stroke activation), ROSC, time of death. If a wall-clock time is stated \
("time is fourteen thirty-two"), put it in value as HH:MM (24h).
- Assessments carry their stated measurement in value: NIHSS score, glucose, \
last-known-well time (entity 'last known well', value HH:MM), CT result \
(entity like 'CT no hemorrhage'). Losing a pulse mid-code is an assessment \
(entity 'pulseless').
- Do NOT log: home medications a patient takes, hypotheticals ("if she goes \
into v-fib..."), drugs merely drawn up or ready, or orders explicitly held \
("hold the epi").
- If a chunk contains no loggable events, return an empty list.\
"""


def parse_transcript(text: str) -> list[TranscriptLine]:
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        m = _LINE_RE.match(raw)
        if not m:
            continue
        minutes, seconds, utterance = m.groups()
        lines.append(
            TranscriptLine(offset_seconds=int(minutes) * 60 + int(seconds), text=utterance)
        )
    return lines


def _chunk_prompt(chunk: list[TranscriptLine], prior: list[ClinicalEvent], code_start: datetime) -> str:
    if prior:
        prior_lines = "\n".join(
            f"- [{e.elapsed_str(code_start)}] {e.type}: {e.entity}"
            + (f" {e.dose}" if e.dose else "")
            for e in prior
        )
    else:
        prior_lines = "(none yet)"
    chunk_lines = "\n".join(f"[{ln.stamp}] {ln.text}" for ln in chunk)
    return (
        f"PRIOR EVENTS:\n{prior_lines}\n\n"
        f"NEW TRANSCRIPT CHUNK:\n{chunk_lines}\n\n"
        "Extract the clinical events from the new chunk only."
    )


class Extractor:
    """Chunk-at-a-time extractor. Iterate stream() to get events as they land."""

    def __init__(self, code_start: datetime | None = None):
        self.client = anthropic.Anthropic()
        self.code_start = code_start or datetime.now().replace(microsecond=0)
        self.events: list[ClinicalEvent] = []

    def stream(self, transcript_text: str) -> Iterator[ClinicalEvent]:
        lines = parse_transcript(transcript_text)
        for i in range(0, len(lines), CHUNK_SIZE):
            yield from self.feed(lines[i : i + CHUNK_SIZE])

    def feed(self, chunk: list[TranscriptLine]) -> list[ClinicalEvent]:
        """Incremental extraction — used by the live (ASR) pipeline."""
        if not chunk:
            return []
        events = self._extract_chunk(chunk)
        self.events.extend(events)
        return events

    def _extract_chunk(self, chunk: list[TranscriptLine]) -> list[ClinicalEvent]:
        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _chunk_prompt(chunk, self.events, self.code_start)}
            ],
            output_format=ChunkExtraction,
        )
        extraction = response.parsed_output
        if extraction is None:
            return []
        return [self._to_clinical_event(e) for e in extraction.events]

    def _to_clinical_event(self, e: ExtractedEvent) -> ClinicalEvent:
        minutes, seconds = e.timestamp.split(":")
        return ClinicalEvent(
            timestamp=self.code_start + timedelta(minutes=int(minutes), seconds=int(seconds)),
            type=e.type,
            entity=e.entity,
            dose=e.dose,
            value=e.value,
            source_utterance=e.source_utterance,
            confidence=e.confidence,
        )
