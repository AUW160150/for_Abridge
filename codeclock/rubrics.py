"""Protocol router — decides WHICH rubric(s) apply, deterministically, with provenance.

Multiple rubrics can be active at once (a code stroke that arrests runs both).
Every activation is recorded as a RubricActivation carrying the triggering
event and the exact evidence matched, so "why is this protocol running?" is
always answerable. Rubric interplay is explicit and deterministic: an arrest
holds the stroke workflow; ROSC releases it.

The LLM never chooses the rubric — activation is keyword-evidence in plain
code, auditable and testable. Ambiguous cases surface as an activation with
its evidence attached for the human to confirm, never a silent switch.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import protocol_config as cfg
from .engine import ProtocolEngine
from .models import ClinicalEvent, Guidance, RubricActivation
from .reasoner import Reasoner
from .stroke_engine import StrokeEngine

RUBRIC_NAMES = {
    "acls_cardiac_arrest": "ACLS Cardiac Arrest",
    "stroke_code": "Code Stroke",
}

# Strong evidence required to re-open an arrest rubric after ROSC (re-arrest).
REARREST_KEYWORDS = ("code blue", "cardiac arrest", "pulseless", "no pulse")


def _match_evidence(event: ClinicalEvent, keywords: tuple[str, ...]) -> str | None:
    text = f"{event.entity} {event.source_utterance}".lower()
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}\b", text):
            return kw
    return None


class ProtocolRouter:
    """Routes events to the engines of every active rubric."""

    def __init__(self) -> None:
        self.rubrics: dict[str, Reasoner] = {}
        self.activations: list[RubricActivation] = []

    # ------------------------------------------------------------- activation

    def _activate(self, rubric_id: str, event: ClinicalEvent, evidence: str) -> RubricActivation:
        if rubric_id == "stroke_code":
            engine = StrokeEngine()
            engine.start(event.timestamp)
        else:
            engine = ProtocolEngine()
        self.rubrics[rubric_id] = Reasoner(engine)
        activation = RubricActivation(
            rubric_id=rubric_id,
            activated_at=event.timestamp,
            triggering_event_ids=[event.id],
            reason=f'matched evidence "{evidence}" in: {event.source_utterance!r}',
        )
        self.activations.append(activation)
        return activation

    def _check_activations(self, event: ClinicalEvent) -> list[RubricActivation]:
        new: list[RubricActivation] = []
        for rubric_id, keywords in cfg.RUBRIC_ACTIVATION_KEYWORDS.items():
            if rubric_id not in self.rubrics:
                evidence = _match_evidence(event, keywords)
                if evidence:
                    new.append(self._activate(rubric_id, event, evidence))
            elif rubric_id == "acls_cardiac_arrest":
                # Re-arrest after ROSC: strong evidence re-opens a fresh arrest rubric.
                engine = self.rubrics[rubric_id].engine
                if isinstance(engine, ProtocolEngine) and not engine.arrest_active:
                    evidence = _match_evidence(event, REARREST_KEYWORDS)
                    if evidence:
                        new.append(self._activate(rubric_id, event, evidence))
        return new

    # ------------------------------------------------------------- event/tick

    def on_event(self, event: ClinicalEvent) -> tuple[list[RubricActivation], list[Guidance]]:
        activations = self._check_activations(event)
        guidance: list[Guidance] = []

        # Rubric interplay: an arrest activation holds the stroke workflow.
        stroke = self._stroke_reasoner()
        for activation in activations:
            if activation.rubric_id == "acls_cardiac_arrest" and stroke:
                finding = stroke.engine.hold(activation.triggering_event_ids)
                if finding:
                    guidance += self._tag("stroke_code", stroke.emit([finding], event.timestamp))

        for rubric_id, reasoner in self.rubrics.items():
            guidance += self._tag(rubric_id, reasoner.on_event(event))

        # ROSC releases the stroke hold.
        if (
            stroke
            and event.type == "milestone"
            and "rosc" in event.entity.lower()
        ):
            finding = stroke.engine.release([event.id], event.timestamp)
            if finding:
                guidance += self._tag("stroke_code", stroke.emit([finding], event.timestamp))

        return activations, guidance

    def on_tick(self, now: datetime) -> list[Guidance]:
        guidance: list[Guidance] = []
        for rubric_id, reasoner in self.rubrics.items():
            guidance += self._tag(rubric_id, reasoner.on_tick(now))
        return guidance

    # ---------------------------------------------------------------- helpers

    def _stroke_reasoner(self) -> Reasoner | None:
        return self.rubrics.get("stroke_code")

    @staticmethod
    def _tag(rubric_id: str, guidance: list[Guidance]) -> list[Guidance]:
        for g in guidance:
            g.rubric_id = rubric_id
        return guidance

    @property
    def guidance_log(self) -> list[Guidance]:
        return [g for r in self.rubrics.values() for g in r.guidance_log]

    @property
    def key_for(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for r in self.rubrics.values():
            merged.update(r.key_for)
        return merged
