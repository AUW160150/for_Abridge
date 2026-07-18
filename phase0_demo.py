"""Phase 0: prove the pipe works.

One hardcoded ClinicalEvent flowing end-to-end (model -> display -> console),
no API involved.
"""

from datetime import datetime, timedelta

from codeclock.display import print_event
from codeclock.models import ClinicalEvent, Guidance, ProtocolRule

code_start = datetime(2026, 7, 18, 14, 32, 0)

event = ClinicalEvent(
    timestamp=code_start + timedelta(minutes=2, seconds=45),
    type="medication",
    entity="epinephrine",
    dose="1 mg",
    source_utterance="NURSE 1: Pushing an amp of epi now — one milligram.",
    confidence=0.97,
)

rule = ProtocolRule(
    id="acls_epi_interval",
    description="Epinephrine 1 mg IV/IO every 3-5 minutes during cardiac arrest.",
    guideline_source="AHA ACLS Adult Cardiac Arrest Algorithm, 2020 Guidelines (verify with clinicians)",
)

guidance = Guidance(
    message="Epinephrine due — last dose 5:10 ago (target interval 3-5 min).",
    urgency="alert",
    triggering_event_ids=[event.id],
    rule_id=rule.id,
    issued_at=code_start + timedelta(minutes=7, seconds=55),
)

print("=== Phase 0: one event end-to-end ===\n")
print_event(event, code_start)
print()
print(f"sample guidance -> [{guidance.urgency.upper()}] {guidance.message}")
print(f"                   triggered by {guidance.triggering_event_ids}, rule {guidance.rule_id}")
print(f"                   rule source: {rule.guideline_source}")
