"""Core data models for Code Clock.

These are the contracts between all five components. The extractor emits
ClinicalEvent, the protocol engine consumes them, the reasoner emits Guidance,
and every record carries its provenance (source utterance / triggering events /
rule id) so the UI can always answer "why?".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

# Event taxonomy — the extractor must classify into exactly one of these.
EVENT_TYPES = (
    "medication",     # drug administered ("pushing an amp of epi")
    "rhythm_check",   # rhythm/pulse assessment ("rhythm check — v-fib")
    "shock",          # defibrillation delivered
    "procedure",      # airway, IV access, compressions started, etc.
    "assessment",     # clinical observation (pulse present, EtCO2 reading)
    "milestone",      # code start, ROSC, time of death
)

URGENCY_LEVELS = ("info", "due_soon", "due_now", "alert")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class ClinicalEvent:
    """One clinical event extracted from the live audio/transcript stream."""

    timestamp: datetime          # when the event occurred in the code
    type: str                    # one of EVENT_TYPES
    entity: str                  # e.g. "epinephrine", "asystole", "defibrillation"
    source_utterance: str        # exact spoken text this came from (traceability)
    confidence: float            # 0-1 self-eval; low values get flagged to the human
    dose: str | None = None      # e.g. "1 mg"
    id: str = field(default_factory=lambda: _new_id("evt"))

    def elapsed_str(self, code_start: datetime) -> str:
        total = int((self.timestamp - code_start).total_seconds())
        return f"{total // 60:02d}:{total % 60:02d}"


@dataclass
class Guidance:
    """One prompt from the reasoner, traceable to events and a protocol rule."""

    message: str                 # e.g. "Epinephrine due — last dose 3:40 ago"
    urgency: str                 # one of URGENCY_LEVELS
    triggering_event_ids: list[str]   # which events caused this (traceability)
    rule_id: str                 # which protocol rule fired (traceability)
    issued_at: datetime
    id: str = field(default_factory=lambda: _new_id("gd"))


@dataclass
class ProtocolRule:
    """A protocol rule, data-driven so it is readable and testable.

    Evaluation happens in deterministic code (the protocol engine), never in
    the LLM. guideline_source ties the rule back to real guidance.
    """

    id: str                      # e.g. "acls_epi_interval"
    description: str
    guideline_source: str        # citation string (traceability to guidelines)
