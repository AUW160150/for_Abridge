"""Protocol engine (component 3 of 5) — a deterministic state machine.

Plain, testable code. No LLM anywhere in this layer. The engine consumes
ClinicalEvents in timeline order and answers two questions:

  ingest(event) -> findings   violations detectable the moment an event lands
                              (late epi, double-dose, wrong-rhythm shock,
                              dose ceiling)
  poll(now)     -> findings   what is currently due or owed (rhythm check due,
                              epi due/overdue, shockable rhythm needs defib,
                              amiodarone suggestion)

Findings carry the rule_id and the triggering event ids so guidance stays
fully source-linked. poll() is idempotent — the reasoner deduplicates by
each finding's stable key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import protocol_config as cfg
from .models import ClinicalEvent

_DOSE_RE = re.compile(r"([\d.]+)\s*mg", re.IGNORECASE)


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _parse_mg(dose: str | None) -> float | None:
    if not dose:
        return None
    m = _DOSE_RE.search(dose)
    return float(m.group(1)) if m else None


def _canonical_rhythm(entity: str) -> str | None:
    return cfg.RHYTHM_SYNONYMS.get(entity.lower().strip())


def _canonical_med(entity: str) -> str | None:
    return cfg.MED_SYNONYMS.get(entity.lower().strip())


@dataclass
class Finding:
    rule_id: str
    severity: str            # info | due_soon | due_now | alert
    message: str
    event_ids: list[str]
    key: str                 # stable identity for dedup/escalation by the reasoner


@dataclass
class ProtocolEngine:
    code_start: datetime | None = None
    arrest_active: bool = False

    last_rhythm_check_at: datetime | None = None
    last_rhythm_check_id: str | None = None
    rhythm_check_count: int = 0
    current_rhythm: str | None = None          # canonical: vf/pvt/asystole/pea/organized
    pending_shock_for: str | None = None       # rhythm_check event id awaiting defib

    last_epi_at: datetime | None = None
    last_epi_id: str | None = None
    epi_count: int = 0

    amio_total_mg: float = 0.0
    amio_dose_count: int = 0
    amio_event_ids: list[str] = field(default_factory=list)

    shock_count: int = 0
    shocks_on_shockable: int = 0

    # ------------------------------------------------------------------ ingest

    def ingest(self, event: ClinicalEvent) -> list[Finding]:
        if self.code_start is None:
            self.code_start = event.timestamp
            self.arrest_active = True

        handler = {
            "milestone": self._ingest_milestone,
            "rhythm_check": self._ingest_rhythm_check,
            "shock": self._ingest_shock,
            "medication": self._ingest_medication,
        }.get(event.type)
        return handler(event) if handler else []

    def _ingest_milestone(self, event: ClinicalEvent) -> list[Finding]:
        entity = event.entity.lower()
        if "rosc" in entity or "death" in entity:
            self.arrest_active = False
            self.pending_shock_for = None
        elif "code" in entity:
            self.code_start = event.timestamp
            self.arrest_active = True
        return []

    def _ingest_rhythm_check(self, event: ClinicalEvent) -> list[Finding]:
        self.last_rhythm_check_at = event.timestamp
        self.last_rhythm_check_id = event.id
        self.rhythm_check_count += 1
        rhythm = _canonical_rhythm(event.entity)
        if rhythm is not None:
            self.current_rhythm = rhythm
            self.pending_shock_for = event.id if rhythm in cfg.SHOCKABLE_RHYTHMS else None
        return []

    def _ingest_shock(self, event: ClinicalEvent) -> list[Finding]:
        findings: list[Finding] = []
        self.shock_count += 1
        if self.current_rhythm in cfg.SHOCKABLE_RHYTHMS:
            self.shocks_on_shockable += 1
        elif self.current_rhythm in cfg.NON_SHOCKABLE_RHYTHMS:
            findings.append(
                Finding(
                    rule_id="acls_shock_rhythm_mismatch",
                    severity="alert",
                    message=(
                        f"Shock delivered but last documented rhythm was "
                        f"{self.current_rhythm} (non-shockable) — verify rhythm."
                    ),
                    event_ids=[e for e in (event.id, self.last_rhythm_check_id) if e],
                    key=f"shock_mismatch:{event.id}",
                )
            )
        self.pending_shock_for = None
        return findings

    def _ingest_medication(self, event: ClinicalEvent) -> list[Finding]:
        med = _canonical_med(event.entity)
        if med == "epinephrine":
            return self._ingest_epi(event)
        if med == "amiodarone":
            return self._ingest_amio(event)
        return []

    def _ingest_epi(self, event: ClinicalEvent) -> list[Finding]:
        findings: list[Finding] = []
        if self.last_epi_at is not None:
            gap = (event.timestamp - self.last_epi_at).total_seconds()
            if gap < cfg.EPI_MIN_GAP_S:
                findings.append(
                    Finding(
                        rule_id="acls_epi_duplicate",
                        severity="alert",
                        message=(
                            f"Epinephrine given only {_mmss(gap)} after the previous "
                            f"dose — possible double-dose (minimum interval "
                            f"{_mmss(cfg.EPI_MIN_GAP_S)})."
                        ),
                        event_ids=[event.id, self.last_epi_id or ""],
                        key=f"epi_duplicate:{event.id}",
                    )
                )
            elif gap > cfg.EPI_OVERDUE_S:
                findings.append(
                    Finding(
                        rule_id="acls_epi_interval",
                        severity="alert",
                        message=(
                            f"Epinephrine given {_mmss(gap)} after the previous dose — "
                            f"outside the 3-5 min window."
                        ),
                        event_ids=[event.id, self.last_epi_id or ""],
                        key=f"epi_late:{event.id}",
                    )
                )
        self.last_epi_at = event.timestamp
        self.last_epi_id = event.id
        self.epi_count += 1
        return findings

    def _ingest_amio(self, event: ClinicalEvent) -> list[Finding]:
        findings: list[Finding] = []
        mg = _parse_mg(event.dose) or 0.0
        self.amio_total_mg += mg
        self.amio_dose_count += 1
        self.amio_event_ids.append(event.id)
        if self.amio_total_mg > cfg.AMIODARONE_MAX_TOTAL_MG:
            findings.append(
                Finding(
                    rule_id="acls_amiodarone_max",
                    severity="alert",
                    message=(
                        f"Amiodarone cumulative dose {self.amio_total_mg:.0f} mg exceeds "
                        f"the {cfg.AMIODARONE_MAX_TOTAL_MG:.0f} mg ceiling."
                    ),
                    event_ids=list(self.amio_event_ids),
                    key=f"amio_max:{event.id}",
                )
            )
        return findings

    # -------------------------------------------------------------------- poll

    def poll(self, now: datetime) -> list[Finding]:
        if not self.arrest_active or self.code_start is None:
            return []
        findings: list[Finding] = []
        findings += self._poll_rhythm_check(now)
        findings += self._poll_epi(now)
        findings += self._poll_shock()
        findings += self._poll_amiodarone()
        return findings

    def _poll_rhythm_check(self, now: datetime) -> list[Finding]:
        ref = self.last_rhythm_check_at or self.code_start
        since = (now - ref).total_seconds()
        key = f"rhythm_check:{self.rhythm_check_count + 1}"
        ids = [self.last_rhythm_check_id] if self.last_rhythm_check_id else []
        if since >= cfg.RHYTHM_CHECK_INTERVAL_S:
            return [
                Finding(
                    rule_id="acls_rhythm_check_interval",
                    severity="due_now",
                    message=f"Rhythm check due — {_mmss(since)} since last check.",
                    event_ids=ids,
                    key=key,
                )
            ]
        if since >= cfg.RHYTHM_CHECK_WARN_S:
            return [
                Finding(
                    rule_id="acls_rhythm_check_interval",
                    severity="due_soon",
                    message=f"Rhythm check coming up — {_mmss(since)} since last check.",
                    event_ids=ids,
                    key=key,
                )
            ]
        return []

    def _poll_epi(self, now: datetime) -> list[Finding]:
        ref = self.last_epi_at or self.code_start
        since = (now - ref).total_seconds()
        key = f"epi:{self.epi_count + 1}"
        ids = [self.last_epi_id] if self.last_epi_id else []
        context = (
            f"last dose {_mmss(since)} ago"
            if self.last_epi_at
            else f"none given, {_mmss(since)} into the code"
        )
        if since >= cfg.EPI_OVERDUE_S:
            return [
                Finding(
                    rule_id="acls_epi_interval",
                    severity="alert",
                    message=f"Epinephrine OVERDUE — {context} (target every 3-5 min).",
                    event_ids=ids,
                    key=key,
                )
            ]
        if since >= cfg.EPI_DUE_S:
            return [
                Finding(
                    rule_id="acls_epi_interval",
                    severity="due_now",
                    message=f"Epinephrine due — {context} (target every 3-5 min).",
                    event_ids=ids,
                    key=key,
                )
            ]
        return []

    def _poll_shock(self) -> list[Finding]:
        if self.pending_shock_for is None:
            return []
        return [
            Finding(
                rule_id="acls_shock_shockable",
                severity="due_now",
                message=f"Shockable rhythm ({self.current_rhythm}) — defibrillate.",
                event_ids=[self.pending_shock_for],
                key=f"shock:{self.pending_shock_for}",
            )
        ]

    def _poll_amiodarone(self) -> list[Finding]:
        if self.current_rhythm not in cfg.SHOCKABLE_RHYTHMS:
            return []
        if self.amio_dose_count == 0 and self.shocks_on_shockable >= cfg.REFRACTORY_SHOCK_COUNT:
            dose, key = cfg.AMIODARONE_FIRST_MG, "amio:1"
        elif (
            self.amio_dose_count == 1
            and self.shocks_on_shockable >= cfg.REFRACTORY_SHOCK_COUNT + 1
        ):
            dose, key = cfg.AMIODARONE_SECOND_MG, "amio:2"
        else:
            return []
        ids = [self.last_rhythm_check_id] if self.last_rhythm_check_id else []
        return [
            Finding(
                rule_id="acls_amiodarone_refractory",
                severity="due_soon",
                message=(
                    f"Refractory {self.current_rhythm} after {self.shocks_on_shockable} "
                    f"shocks — consider amiodarone {dose:.0f} mg."
                ),
                event_ids=ids + self.amio_event_ids,
                key=key,
            )
        ]
