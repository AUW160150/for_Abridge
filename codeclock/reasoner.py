"""Reasoner (component 4 of 5).

Bridges the timeline and the protocol engine: on each new event it updates the
engine and converts findings to Guidance; on each clock tick it asks the
engine what's due. Deduplicates by each finding's stable key so the same
prompt isn't re-issued every tick — a prompt is only re-emitted when its
severity escalates (e.g. due_now -> alert).
"""

from __future__ import annotations

from datetime import datetime

from .engine import Finding, ProtocolEngine
from .models import ClinicalEvent, Guidance

_SEVERITY_RANK = {"info": 0, "due_soon": 1, "due_now": 2, "alert": 3}


class Reasoner:
    def __init__(self, engine: ProtocolEngine | None = None):
        self.engine = engine or ProtocolEngine()
        self.guidance_log: list[Guidance] = []
        self._issued: dict[str, int] = {}  # finding key -> highest severity rank issued
        self.key_for: dict[str, str] = {}  # guidance id -> finding key (for UI resolution)

    def on_event(self, event: ClinicalEvent) -> list[Guidance]:
        return self._convert(self.engine.ingest(event), event.timestamp)

    def on_tick(self, now: datetime) -> list[Guidance]:
        return self._convert(self.engine.poll(now), now)

    def _convert(self, findings: list[Finding], now: datetime) -> list[Guidance]:
        issued: list[Guidance] = []
        for f in findings:
            rank = _SEVERITY_RANK[f.severity]
            if self._issued.get(f.key, -1) >= rank:
                continue
            self._issued[f.key] = rank
            guidance = Guidance(
                message=f.message,
                urgency=f.severity,
                triggering_event_ids=[e for e in f.event_ids if e],
                rule_id=f.rule_id,
                issued_at=now,
            )
            self.guidance_log.append(guidance)
            self.key_for[guidance.id] = f.key
            issued.append(guidance)
        return issued
