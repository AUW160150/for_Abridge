"""Unit tests for the deterministic protocol engine. No LLM involved."""

from datetime import datetime, timedelta

from codeclock.engine import ProtocolEngine
from codeclock.models import ClinicalEvent

T0 = datetime(2026, 7, 18, 14, 32, 0)


def at(minutes: int, seconds: int = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, seconds=seconds)


def ev(mm: int, ss: int, type: str, entity: str, dose: str | None = None) -> ClinicalEvent:
    return ClinicalEvent(
        timestamp=at(mm, ss),
        type=type,
        entity=entity,
        dose=dose,
        source_utterance=f"test: {entity}",
        confidence=1.0,
    )


def started_engine() -> ProtocolEngine:
    engine = ProtocolEngine()
    engine.ingest(ev(0, 0, "milestone", "code start"))
    return engine


def keys(findings):
    return {f.key for f in findings}


def rule_ids(findings):
    return {f.rule_id for f in findings}


# --- epinephrine interval ---------------------------------------------------

def test_epi_due_then_overdue():
    engine = started_engine()
    engine.ingest(ev(2, 45, "medication", "epinephrine", "1 mg"))

    assert not any(f.rule_id == "acls_epi_interval" for f in engine.poll(at(5, 0)))

    due = [f for f in engine.poll(at(5, 50)) if f.rule_id == "acls_epi_interval"]
    assert due and due[0].severity == "due_now" and due[0].key == "epi:2"

    overdue = [f for f in engine.poll(at(7, 50)) if f.rule_id == "acls_epi_interval"]
    assert overdue and overdue[0].severity == "alert" and overdue[0].key == "epi:2"


def test_first_epi_clock_runs_from_code_start():
    engine = started_engine()
    due = [f for f in engine.poll(at(3, 30)) if f.rule_id == "acls_epi_interval"]
    assert due and due[0].key == "epi:1"


def test_late_epi_flagged_on_ingest():
    engine = started_engine()
    first = ev(2, 45, "medication", "epinephrine", "1 mg")
    engine.ingest(first)
    findings = engine.ingest(ev(7, 55, "medication", "epinephrine", "1 mg"))
    late = [f for f in findings if f.rule_id == "acls_epi_interval"]
    assert late and late[0].severity == "alert"
    assert first.id in late[0].event_ids


def test_double_dose_epi_flagged():
    engine = started_engine()
    engine.ingest(ev(2, 0, "medication", "epi", "1 mg"))
    findings = engine.ingest(ev(3, 0, "medication", "epinephrine", "1 mg"))
    assert "acls_epi_duplicate" in rule_ids(findings)


# --- rhythm checks ----------------------------------------------------------

def test_rhythm_check_due_after_interval():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "v-fib"))
    assert not any(
        f.rule_id == "acls_rhythm_check_interval" and f.severity == "due_now"
        for f in engine.poll(at(3, 30))
    )
    due = [f for f in engine.poll(at(4, 10)) if f.rule_id == "acls_rhythm_check_interval"]
    assert due and due[0].severity == "due_now" and due[0].key == "rhythm_check:2"


# --- shock logic ------------------------------------------------------------

def test_shockable_rhythm_prompts_defib_until_shocked():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "v-fib"))
    assert "acls_shock_shockable" in rule_ids(engine.poll(at(2, 5)))
    engine.ingest(ev(2, 20, "shock", "defibrillation", "200 J"))
    assert "acls_shock_shockable" not in rule_ids(engine.poll(at(2, 30)))


def test_no_shock_prompt_on_asystole():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "asystole"))
    assert "acls_shock_shockable" not in rule_ids(engine.poll(at(2, 10)))


def test_shock_on_non_shockable_rhythm_alerts():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "asystole"))
    findings = engine.ingest(ev(2, 30, "shock", "defibrillation"))
    mismatch = [f for f in findings if f.rule_id == "acls_shock_rhythm_mismatch"]
    assert mismatch and mismatch[0].severity == "alert"


# --- amiodarone -------------------------------------------------------------

def test_amiodarone_suggested_after_refractory_shocks():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "v-fib"))
    engine.ingest(ev(2, 20, "shock", "defibrillation"))
    assert "acls_amiodarone_refractory" not in rule_ids(engine.poll(at(3, 0)))
    engine.ingest(ev(4, 0, "rhythm_check", "v-fib"))
    engine.ingest(ev(4, 10, "shock", "defibrillation"))
    suggestion = [
        f for f in engine.poll(at(4, 20)) if f.rule_id == "acls_amiodarone_refractory"
    ]
    assert suggestion and "300" in suggestion[0].message and suggestion[0].key == "amio:1"


def test_amiodarone_ceiling_alert():
    engine = started_engine()
    engine.ingest(ev(4, 0, "medication", "amiodarone", "300 mg"))
    findings = engine.ingest(ev(8, 0, "medication", "amiodarone", "300 mg"))
    ceiling = [f for f in findings if f.rule_id == "acls_amiodarone_max"]
    assert ceiling and ceiling[0].severity == "alert" and "600" in ceiling[0].message


# --- ROSC -------------------------------------------------------------------

def test_rosc_stops_all_prompts():
    engine = started_engine()
    engine.ingest(ev(2, 0, "rhythm_check", "v-fib"))
    assert engine.poll(at(2, 5))  # shock prompt pending
    engine.ingest(ev(8, 24, "milestone", "ROSC"))
    assert engine.poll(at(8, 30)) == []
    assert engine.poll(at(15, 0)) == []
