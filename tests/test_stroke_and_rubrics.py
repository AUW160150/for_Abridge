"""Tests for the stroke engine and the multi-rubric router (deterministic, no LLM)."""

from datetime import datetime, timedelta

from codeclock.models import ClinicalEvent
from codeclock.rubrics import ProtocolRouter
from codeclock.stroke_engine import StrokeEngine

T0 = datetime(2026, 7, 18, 10, 5, 0)


def at(minutes: int, seconds: int = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, seconds=seconds)


def ev(mm: int, ss: int, type: str, entity: str, dose=None, value=None,
       source: str | None = None) -> ClinicalEvent:
    return ClinicalEvent(
        timestamp=at(mm, ss),
        type=type,
        entity=entity,
        dose=dose,
        value=value,
        source_utterance=source or f"test: {entity}",
        confidence=1.0,
    )


def stroke_started() -> StrokeEngine:
    engine = StrokeEngine()
    engine.ingest(ev(0, 0, "milestone", "code stroke", value="10:05"))
    return engine


def rules(findings):
    return {f.rule_id for f in findings}


# --- stroke clocks ----------------------------------------------------------

def test_ct_prompt_escalates_and_clears():
    engine = stroke_started()
    assert rules(engine.poll(at(10, 0))) == set() or "stroke_door_to_ct" not in rules(engine.poll(at(10, 0)))
    assert any(f.rule_id == "stroke_door_to_ct" and f.severity == "due_soon"
               for f in engine.poll(at(16, 0)))
    assert any(f.rule_id == "stroke_door_to_ct" and f.severity == "due_now"
               for f in engine.poll(at(26, 0)))
    engine.ingest(ev(27, 0, "procedure", "CT head non-contrast"))
    assert "stroke_door_to_ct" not in rules(engine.poll(at(28, 0)))


def test_door_to_needle_escalation():
    engine = stroke_started()
    assert any(f.rule_id == "stroke_door_to_needle" and f.severity == "due_soon"
               for f in engine.poll(at(31, 0)))
    assert any(f.rule_id == "stroke_door_to_needle" and f.severity == "due_now"
               for f in engine.poll(at(46, 0)))
    assert any(f.rule_id == "stroke_door_to_needle" and f.severity == "alert"
               for f in engine.poll(at(61, 0)))


def test_lkw_prompt_when_undocumented():
    engine = stroke_started()
    assert any(f.rule_id == "stroke_lkw_documented" for f in engine.poll(at(11, 0)))


def test_needle_in_time_and_within_window():
    engine = stroke_started()
    engine.ingest(ev(0, 45, "assessment", "last known well", value="09:30"))
    findings = engine.ingest(ev(41, 20, "medication", "tenecteplase", dose="25 mg"))
    by_rule = {f.rule_id: f for f in findings}
    assert by_rule["stroke_door_to_needle"].severity == "info"      # 41 min < 60
    assert by_rule["stroke_tpa_window"].severity == "info"          # ~1.3 h < 4.5 h


def test_needle_without_lkw_alerts():
    engine = stroke_started()
    findings = engine.ingest(ev(30, 0, "medication", "alteplase", dose="90 mg"))
    window = [f for f in findings if f.rule_id == "stroke_tpa_window"]
    assert window and window[0].severity == "alert"


def test_needle_outside_window_alerts():
    engine = stroke_started()
    engine.ingest(ev(0, 45, "assessment", "last known well", value="05:00"))  # 5h05 before door
    findings = engine.ingest(ev(20, 0, "medication", "tenecteplase", dose="25 mg"))
    window = [f for f in findings if f.rule_id == "stroke_tpa_window"]
    assert window and window[0].severity == "alert"


def test_hold_silences_polls_and_release_resumes():
    engine = stroke_started()
    assert engine.poll(at(31, 0))                       # needle warn active
    assert engine.hold(["evt_x"]) is not None
    assert engine.poll(at(32, 0)) == []                 # held: silent
    assert engine.release(["evt_y"], at(35, 0)) is not None
    assert engine.poll(at(36, 0))                       # resumed


# --- rubric router ----------------------------------------------------------

def _route(router: ProtocolRouter, event: ClinicalEvent):
    activations, guidance = router.on_event(event)
    return activations, guidance


def test_stroke_activation_with_provenance():
    router = ProtocolRouter()
    trigger = ev(0, 0, "milestone", "code stroke", value="10:05",
                 source="CHARGE NURSE: Code stroke, bay three.")
    activations, _ = _route(router, trigger)
    assert [a.rubric_id for a in activations] == ["stroke_code"]
    assert activations[0].triggering_event_ids == [trigger.id]
    assert "code stroke" in activations[0].reason


def test_arrest_mid_stroke_activates_second_rubric_and_holds_stroke():
    router = ProtocolRouter()
    _route(router, ev(0, 0, "milestone", "code stroke", value="10:05"))
    arrest_trigger = ev(24, 10, "assessment", "pulseless",
                        source="NURSE 1: I can't feel a pulse — she's pulseless!")
    activations, guidance = _route(router, arrest_trigger)
    assert [a.rubric_id for a in activations] == ["acls_cardiac_arrest"]
    hold = [g for g in guidance if g.rule_id == "stroke_clock_hold"]
    assert hold and hold[0].rubric_id == "stroke_code"
    assert router.on_tick(at(25, 0)) == [] or all(
        g.rubric_id != "stroke_code" for g in router.on_tick(at(25, 0))
    )


def test_rosc_releases_stroke_hold():
    router = ProtocolRouter()
    _route(router, ev(0, 0, "milestone", "code stroke", value="10:05"))
    _route(router, ev(24, 10, "assessment", "pulseless"))
    _, guidance = _route(router, ev(32, 10, "milestone", "ROSC"))
    resume = [g for g in guidance if g.rule_id == "stroke_clock_hold"]
    assert resume and "resumes" in resume[0].message
    # stroke polls are live again (door-to-needle well past warn)
    assert any(g.rubric_id == "stroke_code" for g in router.on_tick(at(33, 0)))


def test_no_false_stroke_activation_on_plain_arrest():
    router = ProtocolRouter()
    activations, _ = _route(
        router,
        ev(0, 0, "milestone", "code start",
           source="CODE LEADER: we have a code blue. No pulse — starting compressions."),
    )
    assert [a.rubric_id for a in activations] == ["acls_cardiac_arrest"]
    assert "stroke_code" not in router.rubrics


def test_rearrest_reopens_fresh_arrest_rubric():
    router = ProtocolRouter()
    _route(router, ev(0, 0, "milestone", "code start", source="code blue, no pulse"))
    _route(router, ev(8, 24, "milestone", "ROSC"))
    activations, _ = _route(
        router, ev(12, 0, "assessment", "pulseless", source="she's pulseless again")
    )
    assert [a.rubric_id for a in activations] == ["acls_cardiac_arrest"]
    assert len([a for a in router.activations if a.rubric_id == "acls_cardiac_arrest"]) == 2
