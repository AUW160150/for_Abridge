"""Registry of protocol rules the engine can fire.

Every Guidance the reasoner emits references one of these rule ids, and each
rule carries its guideline citation — that's the traceability chain from
prompt back to literature. Evaluation logic lives in engine.py, never here
and never in the LLM.
"""

from .models import ProtocolRule

_AHA_ACLS = "AHA 2020 Adult Cardiac Arrest Algorithm (verify with clinicians before demo)"

RULES: dict[str, ProtocolRule] = {
    r.id: r
    for r in [
        ProtocolRule(
            id="acls_rhythm_check_interval",
            description="Rhythm/pulse check approximately every 2 minutes.",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_epi_interval",
            description="Epinephrine 1 mg IV/IO every 3-5 minutes during arrest.",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_epi_duplicate",
            description="Epinephrine doses given too close together (double-dose catch).",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_shock_shockable",
            description="Shockable rhythm (VF / pulseless VT) — defibrillate.",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_shock_rhythm_mismatch",
            description="Shock delivered when last known rhythm was non-shockable.",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_amiodarone_refractory",
            description="Amiodarone (300 mg, then 150 mg) for refractory VF/pVT after repeated shocks.",
            guideline_source=_AHA_ACLS,
        ),
        ProtocolRule(
            id="acls_amiodarone_max",
            description="Amiodarone cumulative dose ceiling 450 mg (300 + 150).",
            guideline_source=_AHA_ACLS,
        ),
    ]
}
