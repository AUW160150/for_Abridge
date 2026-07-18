"""Code-stroke protocol engine — deterministic, no LLM (same contract as engine.py).

Tracks the stroke clocks: door time (rubric activation), last-known-well,
door-to-CT (<=25 min), door-to-needle (<=60 min), and the 4.5 h thrombolytic
window anchored at LKW. Supports hold/release so an intervening cardiac
arrest pauses stroke prompts without losing the clocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import protocol_config as cfg
from .engine import Finding, _mmss
from .models import ClinicalEvent

_WALL_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _parse_wall_minutes(value: str | None) -> int | None:
    if not value:
        return None
    m = _WALL_RE.search(value)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return hh * 60 + mm if hh < 24 and mm < 60 else None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


@dataclass
class StrokeEngine:
    door_at: datetime | None = None      # rubric activation time (arrival/recognition)
    door_wall_min: int | None = None     # stated wall clock at door, minutes past midnight
    active: bool = True
    held: bool = False
    hold_count: int = 0

    lkw_at: datetime | None = None       # absolute LKW time derived from wall clocks
    lkw_event_id: str | None = None
    ct_done_id: str | None = None
    needle_id: str | None = None
    milestone_id: str | None = None

    def start(self, at: datetime) -> None:
        if self.door_at is None:
            self.door_at = at

    # ------------------------------------------------------------------ ingest

    def ingest(self, event: ClinicalEvent) -> list[Finding]:
        self.start(event.timestamp)
        entity_words = _words(event.entity)

        if event.type == "milestone":
            return self._ingest_milestone(event)
        if event.type == "assessment" and any(
            k in event.entity.lower() for k in cfg.LKW_KEYWORDS
        ):
            return self._ingest_lkw(event)
        if entity_words & _words(" ".join(cfg.CT_KEYWORDS)) and event.type in (
            "procedure", "assessment"
        ):
            self.ct_done_id = self.ct_done_id or event.id
            return []
        if event.type == "medication" and entity_words & cfg.THROMBOLYTICS:
            return self._ingest_thrombolytic(event)
        return []

    def _ingest_milestone(self, event: ClinicalEvent) -> list[Finding]:
        entity = event.entity.lower()
        if "stroke" in entity:
            self.milestone_id = event.id
            self.door_at = event.timestamp
            self.door_wall_min = _parse_wall_minutes(event.value)
        elif "death" in entity:
            self.active = False
        return []

    def _ingest_lkw(self, event: ClinicalEvent) -> list[Finding]:
        self.lkw_event_id = event.id
        lkw_wall = _parse_wall_minutes(event.value)
        if lkw_wall is not None and self.door_wall_min is not None and self.door_at:
            delta_min = (self.door_wall_min - lkw_wall) % (24 * 60)
            self.lkw_at = self.door_at - timedelta(minutes=delta_min)
        return []

    def _ingest_thrombolytic(self, event: ClinicalEvent) -> list[Finding]:
        self.needle_id = event.id
        findings: list[Finding] = []
        door_ids = [self.milestone_id] if self.milestone_id else []

        elapsed = (event.timestamp - self.door_at).total_seconds()
        if elapsed <= cfg.STROKE_NEEDLE_DUE_S:
            findings.append(
                Finding(
                    rule_id="stroke_door_to_needle",
                    severity="info",
                    message=f"Thrombolytic given — door-to-needle {_mmss(elapsed)}, within the 60-min target.",
                    event_ids=[event.id] + door_ids,
                    key=f"needle_result:{event.id}",
                )
            )
        else:
            findings.append(
                Finding(
                    rule_id="stroke_door_to_needle",
                    severity="alert",
                    message=f"Door-to-needle {_mmss(elapsed)} — 60-min target missed.",
                    event_ids=[event.id] + door_ids,
                    key=f"needle_result:{event.id}",
                )
            )

        if self.lkw_at is None:
            findings.append(
                Finding(
                    rule_id="stroke_tpa_window",
                    severity="alert",
                    message="Thrombolytic given but last-known-well is not documented — window unverified.",
                    event_ids=[event.id],
                    key=f"tpa_window:{event.id}",
                )
            )
        else:
            from_lkw = (event.timestamp - self.lkw_at).total_seconds()
            hours = from_lkw / 3600
            if from_lkw <= cfg.STROKE_TPA_WINDOW_S:
                findings.append(
                    Finding(
                        rule_id="stroke_tpa_window",
                        severity="info",
                        message=f"Within thrombolytic window — {hours:.1f} h from last known well (limit 4.5 h).",
                        event_ids=[event.id, self.lkw_event_id or ""],
                        key=f"tpa_window:{event.id}",
                    )
                )
            else:
                findings.append(
                    Finding(
                        rule_id="stroke_tpa_window",
                        severity="alert",
                        message=f"Thrombolytic given {hours:.1f} h from last known well — OUTSIDE the 4.5 h window.",
                        event_ids=[event.id, self.lkw_event_id or ""],
                        key=f"tpa_window:{event.id}",
                    )
                )
        return findings

    # ------------------------------------------------------------ hold/release

    def hold(self, event_ids: list[str]) -> Finding | None:
        if self.held:
            return None
        self.held = True
        self.hold_count += 1
        return Finding(
            rule_id="stroke_clock_hold",
            severity="info",
            message="Stroke workflow on hold — cardiac arrest in progress. Door and LKW clocks keep running.",
            event_ids=event_ids,
            key=f"stroke_hold:{self.hold_count}",
        )

    def release(self, event_ids: list[str], now: datetime) -> Finding | None:
        if not self.held:
            return None
        self.held = False
        elapsed = (now - self.door_at).total_seconds() if self.door_at else 0
        return Finding(
            rule_id="stroke_clock_hold",
            severity="due_now",
            message=(
                f"ROSC — stroke workflow resumes. {_mmss(elapsed)} since door; "
                "reassess thrombolysis eligibility."
            ),
            event_ids=event_ids,
            key=f"stroke_resume:{self.hold_count}",
        )

    # -------------------------------------------------------------------- poll

    def poll(self, now: datetime) -> list[Finding]:
        if not self.active or self.held or self.door_at is None:
            return []
        findings: list[Finding] = []
        since_door = (now - self.door_at).total_seconds()
        door_ids = [self.milestone_id] if self.milestone_id else []

        if self.lkw_at is None and self.lkw_event_id is None and not self.needle_id:
            if since_door >= cfg.STROKE_LKW_PROMPT_S:
                findings.append(
                    Finding(
                        rule_id="stroke_lkw_documented",
                        severity="due_now",
                        message="Last known well not documented — establish LKW (anchors the 4.5 h window).",
                        event_ids=door_ids,
                        key="stroke_lkw",
                    )
                )

        if self.ct_done_id is None and not self.needle_id:
            if since_door >= cfg.STROKE_CT_DUE_S:
                findings.append(
                    Finding(
                        rule_id="stroke_door_to_ct",
                        severity="due_now",
                        message=f"Head CT overdue — {_mmss(since_door)} since door (target <=25 min).",
                        event_ids=door_ids,
                        key="stroke_ct",
                    )
                )
            elif since_door >= cfg.STROKE_CT_WARN_S:
                findings.append(
                    Finding(
                        rule_id="stroke_door_to_ct",
                        severity="due_soon",
                        message=f"Head CT pending — {_mmss(since_door)} since door (target <=25 min).",
                        event_ids=door_ids,
                        key="stroke_ct",
                    )
                )

        if self.needle_id is None:
            if since_door >= cfg.STROKE_NEEDLE_DUE_S:
                findings.append(
                    Finding(
                        rule_id="stroke_door_to_needle",
                        severity="alert",
                        message=f"Door-to-needle {_mmss(since_door)} — 60-min target missed and counting.",
                        event_ids=door_ids,
                        key="stroke_needle",
                    )
                )
            elif since_door >= cfg.STROKE_NEEDLE_LATE_S:
                findings.append(
                    Finding(
                        rule_id="stroke_door_to_needle",
                        severity="due_now",
                        message=f"Door-to-needle at {_mmss(since_door)} — 60-min target approaching.",
                        event_ids=door_ids,
                        key="stroke_needle",
                    )
                )
            elif since_door >= cfg.STROKE_NEEDLE_WARN_S:
                findings.append(
                    Finding(
                        rule_id="stroke_door_to_needle",
                        severity="due_soon",
                        message=f"Door-to-needle clock at {_mmss(since_door)} of the 60-min target.",
                        event_ids=door_ids,
                        key="stroke_needle",
                    )
                )
        return findings
