"""Phase 5: evaluation harness — Abridge-style offline evals for the pipeline.

Three complementary layers, mirroring how clinical decision support is
evaluated for enterprise readiness:

1. Clinical accuracy   — extraction scored against physician-style ground
                         truth: precision / recall / F1, timestamp error,
                         dose + value accuracy, confidence calibration.
2. Boundary/adversarial — trap transcripts (negations, hypotheticals, home
                         meds, drawn-up-not-given) where the correct action
                         is DON'T log; any hit is a critical false positive.
3. Safety of guidance  — replay events through the deterministic protocol
                         router: every expected prompt must fire in its time
                         window; forbidden prompts must never fire; rubric
                         activations must match with provenance intact.

Plus a provenance-integrity gate: 100% of events must carry an utterance
found verbatim in the transcript, and 100% of guidance must chain to real
event ids and a registered rule. Provenance is not sampled — it is checked
on every record, because it is the product's trust layer.

Usage:
    python run_eval.py [--fresh]     # --fresh re-extracts all transcripts
"""

from __future__ import annotations

import json
import re
import sys
from datetime import timedelta
from pathlib import Path

from codeclock.models import ClinicalEvent
from codeclock.rubrics import ProtocolRouter
from codeclock.rules import RULES
from codeclock.store import extract_or_load

GT_DIR = Path("data/ground_truth")
OUT_DIR = Path("eval")
TIME_TOLERANCE_S = 30

CAL_BUCKETS = [(0.0, 0.6), (0.6, 0.75), (0.75, 0.9), (0.9, 1.01)]


def mmss_to_s(stamp: str) -> int:
    m, s = stamp.split(":")
    return int(m) * 60 + int(s)


def s_to_mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _digits(text: str | None) -> str:
    return re.sub(r"[^\d]", "", text or "")


def _entity_matches(extracted: str, aliases: list[str] | None) -> bool:
    if not aliases:
        return True
    e = _norm(extracted)
    return any(a in e or e in a for a in (_norm(a) for a in aliases))


def _type_matches(extracted: str, spec: dict) -> bool:
    types = spec.get("type_any") or ([spec["type"]] if "type" in spec else [])
    return not types or extracted in types


def _in_window(t: float, window: list[str] | None) -> bool:
    if not window:
        return True
    return mmss_to_s(window[0]) <= t <= mmss_to_s(window[1])


class ScenarioResult:
    def __init__(self, name: str):
        self.name = name
        self.metrics: dict = {}
        self.failures: list[str] = []
        self.calibration: list[dict] = []

    def fail(self, message: str) -> None:
        self.failures.append(message)


def match_events(
    events: list[ClinicalEvent], gt: dict, elapsed: dict[str, float]
) -> tuple[list[tuple[dict, ClinicalEvent]], list[dict], list[ClinicalEvent]]:
    """Greedy 1:1 matching of ground-truth events to extracted events."""
    unmatched_events = list(events)
    matched: list[tuple[dict, ClinicalEvent]] = []
    missed: list[dict] = []
    for spec in gt["events"]:
        t = mmss_to_s(spec["t"])
        candidates = [
            e for e in unmatched_events
            if _type_matches(e.type, spec)
            and _entity_matches(e.entity, spec.get("entity_any"))
            and abs(elapsed[e.id] - t) <= TIME_TOLERANCE_S
        ]
        if candidates:
            best = min(candidates, key=lambda e: abs(elapsed[e.id] - t))
            matched.append((spec, best))
            unmatched_events.remove(best)
        else:
            missed.append(spec)
    # optional events absorb benign extras
    for spec in gt.get("optional_events", []):
        t = mmss_to_s(spec["t"])
        for e in list(unmatched_events):
            if (
                _type_matches(e.type, spec)
                and _entity_matches(e.entity, spec.get("entity_any"))
                and abs(elapsed[e.id] - t) <= TIME_TOLERANCE_S
            ):
                unmatched_events.remove(e)
                break
    return matched, missed, unmatched_events


def replay_guidance(events: list[ClinicalEvent], code_start):
    """Deterministic replay: returns (router, fired guidance with elapsed times)."""
    router = ProtocolRouter()
    fired: list[dict] = []
    activations: list[dict] = []
    end = int((events[-1].timestamp - code_start).total_seconds()) + 10
    pending = sorted(events, key=lambda e: e.timestamp)
    idx = 0
    for t in range(0, end + 1):
        now = code_start + timedelta(seconds=t)
        while idx < len(pending) and pending[idx].timestamp <= now:
            acts, guidance = router.on_event(pending[idx])
            for a in acts:
                activations.append({"rubric_id": a.rubric_id, "t": t, "id": a.id})
            for g in guidance:
                fired.append({"rule_id": g.rule_id, "urgency": g.urgency, "t": t, "g": g})
            idx += 1
        for g in router.on_tick(now):
            fired.append({"rule_id": g.rule_id, "urgency": g.urgency, "t": t, "g": g})
    return router, fired, activations


def eval_scenario(gt_path: Path, fresh: bool) -> ScenarioResult:
    gt = json.loads(gt_path.read_text())
    result = ScenarioResult(gt["name"])
    transcript_path = Path(gt["transcript"])
    events, code_start = extract_or_load(transcript_path, Path(gt["cache"]), fresh=fresh)
    elapsed = {e.id: (e.timestamp - code_start).total_seconds() for e in events}

    # ---- 1. extraction accuracy
    matched, missed, extras = match_events(events, gt, elapsed)
    for spec in missed:
        result.fail(f"MISSED event: {spec['t']} {spec.get('type', spec.get('type_any'))} {spec.get('entity_any')}")

    critical_fp = 0
    for e in extras:
        spec_hit = next(
            (
                f for f in gt.get("forbidden_events", [])
                if _type_matches(e.type, f)
                and _entity_matches(e.entity, f.get("entity_any"))
                and _in_window(elapsed[e.id], f.get("t_window"))
            ),
            None,
        )
        if spec_hit:
            critical_fp += 1
            result.fail(
                f"CRITICAL FALSE POSITIVE ({spec_hit.get('note', 'forbidden')}): "
                f"[{s_to_mmss(elapsed[e.id])}] {e.type} {e.entity!r} from {e.source_utterance!r}"
            )
        else:
            result.fail(f"extra event (minor FP): [{s_to_mmss(elapsed[e.id])}] {e.type} {e.entity!r}")

    tp, fp, fn = len(matched), len(extras), len(missed)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    ts_errors = [abs(elapsed[e.id] - mmss_to_s(spec["t"])) for spec, e in matched]
    with_dose = [(spec, e) for spec, e in matched if spec.get("dose")]
    dose_ok = sum(1 for spec, e in with_dose if _digits(spec["dose"]) == _digits(e.dose))
    with_value = [(spec, e) for spec, e in matched if spec.get("value")]
    value_ok = sum(1 for spec, e in with_value if _digits(spec["value"]) == _digits(e.value))
    for spec, e in with_value:
        if _digits(spec["value"]) != _digits(e.value):
            result.fail(f"VALUE MISS at {spec['t']}: expected {spec['value']!r}, got {e.value!r}")

    # ---- 2. provenance integrity (checked on every record, not sampled)
    transcript_norm = _norm(transcript_path.read_text())
    bad_provenance = [
        e for e in events if _norm(e.source_utterance) not in transcript_norm
    ]
    for e in bad_provenance:
        result.fail(f"PROVENANCE BREAK: utterance not verbatim in transcript: {e.source_utterance!r}")

    # ---- 3. guidance safety replay
    router, fired, activations = replay_guidance(events, code_start)
    event_ids = {e.id for e in events}

    expected_hits = 0
    for exp in gt.get("expected_guidance", []):
        hit = any(
            f["rule_id"] == exp["rule_id"]
            and f["urgency"] == exp["urgency"]
            and _in_window(f["t"], exp.get("t_window"))
            for f in fired
        )
        if hit:
            expected_hits += 1
        else:
            result.fail(
                f"MISSED GUIDANCE: {exp['rule_id']} [{exp['urgency']}] in {exp.get('t_window')}"
            )

    forbidden_hits = 0
    for forb in gt.get("forbidden_guidance", []):
        hits = [
            f for f in fired
            if f["rule_id"] == forb["rule_id"]
            and (forb.get("urgency") is None or f["urgency"] == forb["urgency"])
        ]
        for f in hits:
            forbidden_hits += 1
            result.fail(
                f"FORBIDDEN GUIDANCE FIRED: {f['rule_id']} [{f['urgency']}] at {s_to_mmss(f['t'])}"
            )

    for exp in gt.get("expected_rubrics", []):
        if not any(
            a["rubric_id"] == exp["rubric_id"] and _in_window(a["t"], exp.get("t_window"))
            for a in activations
        ):
            result.fail(f"MISSED RUBRIC ACTIVATION: {exp['rubric_id']} in {exp.get('t_window')}")

    bad_guidance_chain = [
        g for g in router.guidance_log
        if g.rule_id not in RULES
        or any(eid and eid not in event_ids for eid in g.triggering_event_ids)
    ]
    for g in bad_guidance_chain:
        result.fail(f"PROVENANCE BREAK in guidance {g.id}: rule or event ids unresolvable")

    # ---- 4. confidence calibration
    matched_ids = {e.id for _, e in matched}
    optional_ok_ids = {e.id for e in events} - {e.id for e in extras}  # matched + optional
    for lo, hi in CAL_BUCKETS:
        bucket = [e for e in events if lo <= e.confidence < hi]
        if bucket:
            correct = sum(1 for e in bucket if e.id in optional_ok_ids)
            result.calibration.append(
                {"bucket": f"{lo:.2f}-{hi:.2f}".replace("-1.01", "-1.00"),
                 "n": len(bucket), "accuracy": correct / len(bucket)}
            )

    result.metrics = {
        "events_extracted": len(events),
        "ground_truth_events": len(gt["events"]),
        "true_positives": tp,
        "false_positives": fp,
        "critical_false_positives": critical_fp,
        "false_negatives": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "timestamp_mae_s": round(sum(ts_errors) / len(ts_errors), 1) if ts_errors else None,
        "dose_accuracy": f"{dose_ok}/{len(with_dose)}" if with_dose else "n/a",
        "value_accuracy": f"{value_ok}/{len(with_value)}" if with_value else "n/a",
        "provenance_intact_events": f"{len(events) - len(bad_provenance)}/{len(events)}",
        "guidance_fired": len(router.guidance_log),
        "expected_guidance_hit": f"{expected_hits}/{len(gt.get('expected_guidance', []))}",
        "forbidden_guidance_fired": forbidden_hits,
        "guidance_chain_intact": f"{len(router.guidance_log) - len(bad_guidance_chain)}/{len(router.guidance_log)}",
    }
    return result


def write_report(results: list[ScenarioResult]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    lines = [
        "# Code Clock — Evaluation Report",
        "",
        "Offline evals in the style of enterprise clinical-AI evaluation:",
        "clinical accuracy vs ground truth, boundary/adversarial traps, guidance",
        "safety, rubric-activation provenance, and confidence calibration.",
        "All data is synthetic; no PHI. Ground truth authored with the transcripts",
        "(pending clinician review — treat labels as provisional).",
        "",
    ]
    payload = {}
    for r in results:
        lines += [f"## Scenario: {r.name}", ""]
        lines += ["| metric | value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in r.metrics.items()]
        if r.calibration:
            lines += ["", "Confidence calibration (bucket accuracy):", ""]
            lines += ["| confidence | n | accuracy |", "|---|---|---|"]
            lines += [
                f"| {c['bucket']} | {c['n']} | {c['accuracy']:.2f} |" for c in r.calibration
            ]
        lines += ["", "Findings:" if r.failures else "Findings: none — all checks passed.", ""]
        lines += [f"- {f}" for f in r.failures] + [""]
        payload[r.name] = {"metrics": r.metrics, "failures": r.failures,
                           "calibration": r.calibration}
    (OUT_DIR / "report.md").write_text("\n".join(lines))
    (OUT_DIR / "report.json").write_text(json.dumps(payload, indent=2))


def main() -> None:
    fresh = "--fresh" in sys.argv
    results = []
    for gt_path in sorted(GT_DIR.glob("*.json")):
        print(f"=== evaluating scenario: {gt_path.stem} ===")
        r = eval_scenario(gt_path, fresh)
        for k, v in r.metrics.items():
            print(f"  {k:>28}: {v}")
        for f in r.failures:
            print(f"  !! {f}")
        print()
        results.append(r)
    write_report(results)
    print(f"report written to {OUT_DIR}/report.md and {OUT_DIR}/report.json")

    critical = sum(
        r.metrics["critical_false_positives"] + r.metrics["forbidden_guidance_fired"]
        for r in results
    )
    missed = sum(r.metrics["false_negatives"] for r in results)
    print(f"\nsafety gate: {critical} critical false positives / forbidden prompts, "
          f"{missed} missed events across {len(results)} scenarios")


if __name__ == "__main__":
    main()
