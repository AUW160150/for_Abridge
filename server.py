"""Phase 4: live view — a thin window onto the acting agent.

Serves static/index.html and an SSE endpoint that replays the code in real
time (time-scaled): events land at their true elapsed times, the reasoner
polls the engine every simulated second, and guidance streams to the browser
as it fires. The browser renders; the agent acts here.

Usage:
    python server.py            # http://127.0.0.1:5057  (default 8x speed)
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta

from flask import Flask, Response, request, send_from_directory

from pathlib import Path

from codeclock.models import ClinicalEvent, Guidance, RubricActivation
from codeclock.rubrics import RUBRIC_NAMES, ProtocolRouter
from codeclock.rules import RULES
from run_pipeline import load_or_extract

app = Flask(__name__, static_folder="static")

PORT = int(os.environ.get("PORT", "5057"))

SCENARIOS = {
    "arrest": (Path("data/transcript.txt"), Path("data/events.json")),
    "stroke": (Path("data/transcript_stroke.txt"), Path("data/events_stroke.json")),
}


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/record")
def record_page():
    return send_from_directory("static", "record.html")


@app.route("/rubrics")
def rubrics_page():
    return send_from_directory("static", "rubrics.html")


@app.route("/api/record")
def api_record():
    from run_eval import replay_guidance

    scenario = request.args.get("scenario", "arrest")
    if scenario == "live":
        return _live_record()
    transcript, cache = SCENARIOS.get(scenario, SCENARIOS["arrest"])
    events, code_start = load_or_extract(transcript, cache)
    events.sort(key=lambda e: e.timestamp)
    router, fired, activations = replay_guidance(events, code_start)

    def el(ts):
        return (ts - code_start).total_seconds()

    return {
        "scenario": scenario,
        "events": [
            {
                "id": e.id, "t": el(e.timestamp), "type": e.type, "entity": e.entity,
                "dose": e.dose, "value": e.value, "source": e.source_utterance,
                "confidence": e.confidence, "low_confidence": e.confidence < 0.75,
            }
            for e in events
        ],
        "activations": [
            {"rubric_id": a["rubric_id"], "t": a["t"]} for a in activations
        ],
        "alerts": [
            {"t": (g.issued_at - code_start).total_seconds(), "message": g.message,
             "rule_id": g.rule_id, "rubric_id": g.rubric_id,
             "trigger_ids": g.triggering_event_ids}
            for g in router.guidance_log if g.urgency == "alert"
        ],
        "totals": {
            "events": len(events),
            "guidance": len(router.guidance_log),
            "low_confidence": sum(1 for e in events if e.confidence < 0.75),
        },
    }


def _live_record():
    """Build the record payload from the most recent live session dump."""
    import json as _json

    dumps = sorted(Path("data/live_sessions").glob("session-*.json"))
    if not dumps:
        return {"scenario": "live", "events": [], "alerts": [], "activations": [],
                "totals": {"events": 0, "guidance": 0, "low_confidence": 0},
                "note": "no live sessions recorded yet"}
    d = _json.loads(dumps[-1].read_text())
    events = d.get("events", [])
    guidance = d.get("guidance", [])
    for i, e in enumerate(events):
        e.setdefault("id", f"live_{i}")
        e["source"] = e.pop("source_utterance", "")
        e["low_confidence"] = e.get("confidence", 1.0) < 0.75
    return {
        "scenario": f"live ({dumps[-1].stem})",
        "events": events,
        "activations": [
            {"rubric_id": a["rubric_id"], "t": a["t"]}
            for a in d.get("rubric_activations", [])
        ],
        "alerts": [
            {"t": g["t"], "message": g["message"], "rule_id": g["rule_id"],
             "rubric_id": g.get("rubric_id", ""),
             "trigger_ids": g.get("triggering_event_ids", [])}
            for g in guidance if g["urgency"] == "alert"
        ],
        "totals": {
            "events": len(events),
            "guidance": len(guidance),
            "low_confidence": sum(1 for e in events if e["low_confidence"]),
        },
    }


@app.route("/api/rubrics")
def api_rubrics():
    import json as _json

    from codeclock import protocol_config as cfg

    constants = {
        k: v for k, v in vars(cfg).items()
        if k.isupper() and isinstance(v, (int, float))
    }
    scenarios = []
    for gt_path in sorted(Path("data/ground_truth").glob("*.json")):
        gt = _json.loads(gt_path.read_text())
        scenarios.append(
            {
                "name": gt["name"],
                "transcript": gt["transcript"],
                "n_events": len(gt["events"]),
                "n_optional": len(gt.get("optional_events", [])),
                "n_forbidden": len(gt.get("forbidden_events", [])),
                "expected_guidance": gt.get("expected_guidance", []),
                "forbidden_guidance": gt.get("forbidden_guidance", []),
            }
        )
    report_path = Path("eval/report.json")
    proposals_path = Path("data/rubric_proposals.json")
    return {
        "rules": [
            {"id": r.id, "description": r.description, "source": r.guideline_source}
            for r in RULES.values()
        ],
        "constants": constants,
        "scenarios": scenarios,
        "report": _json.loads(report_path.read_text()) if report_path.exists() else None,
        "proposals": _json.loads(proposals_path.read_text()) if proposals_path.exists() else [],
    }


@app.route("/api/run_eval", methods=["POST"])
def api_run_eval():
    from run_eval import eval_scenario, write_report

    results = []
    for gt_path in sorted(Path("data/ground_truth").glob("*.json")):
        results.append(eval_scenario(gt_path, fresh=False))
    write_report(results)
    return {
        r.name: {"metrics": r.metrics, "failures": r.failures} for r in results
    }


@app.route("/api/propose", methods=["POST"])
def api_propose():
    import json as _json
    from datetime import datetime as _dt

    proposals_path = Path("data/rubric_proposals.json")
    proposals = (
        _json.loads(proposals_path.read_text()) if proposals_path.exists() else []
    )
    body = request.get_json(force=True)
    proposals.append(
        {
            "constant": body.get("constant"),
            "current": body.get("current"),
            "proposed": body.get("proposed"),
            "rationale": body.get("rationale", ""),
            "author": body.get("author", "anonymous"),
            "submitted_at": _dt.now().isoformat(timespec="seconds"),
            "status": "pending clinician review",
        }
    )
    proposals_path.write_text(_json.dumps(proposals, indent=2))
    return {"ok": True, "pending": len(proposals)}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _event_msg(e: ClinicalEvent, t: float) -> dict:
    return {
        "kind": "event",
        "event": {
            "id": e.id,
            "t": t,
            "type": e.type,
            "entity": e.entity,
            "dose": e.dose,
            "source": e.source_utterance,
            "confidence": e.confidence,
        },
    }


def _guidance_msg(g: Guidance, t: float) -> dict:
    rule = RULES.get(g.rule_id)
    return {
        "kind": "guidance",
        "guidance": {
            "id": g.id,
            "t": t,
            "urgency": g.urgency,
            "message": g.message,
            "rule_id": g.rule_id,
            "rubric_id": g.rubric_id,
            "rubric_name": RUBRIC_NAMES.get(g.rubric_id, g.rubric_id),
            "rule_desc": rule.description if rule else "",
            "rule_source": rule.guideline_source if rule else "",
            "trigger_ids": g.triggering_event_ids,
        },
    }


def _rubric_msg(a: RubricActivation, t: float) -> dict:
    return {
        "kind": "rubric",
        "rubric": {
            "id": a.id,
            "t": t,
            "rubric_id": a.rubric_id,
            "name": RUBRIC_NAMES.get(a.rubric_id, a.rubric_id),
            "reason": a.reason,
            "trigger_ids": a.triggering_event_ids,
        },
    }


def _resolution_prefixes(e: ClinicalEvent, router: ProtocolRouter) -> list[str]:
    """Which guidance-key prefixes this event satisfies (display concern only)."""
    if e.type == "rhythm_check":
        prefixes = ["rhythm_check:"]
        acls = router.rubrics.get("acls_cardiac_arrest")
        if acls and acls.engine.pending_shock_for is None:
            prefixes.append("shock:")
        return prefixes
    if e.type == "shock":
        return ["shock:"]
    if e.type == "medication":
        entity = e.entity.lower()
        if "epi" in entity or "adrenaline" in entity:
            return ["epi:"]
        if "amio" in entity:
            return ["amio:"]
        if any(k in entity for k in ("tenecteplase", "tnk", "tpa", "alteplase")):
            return ["stroke_needle", "stroke_ct", "stroke_lkw"]
    if e.type in ("procedure", "assessment"):
        entity = e.entity.lower()
        if "ct" in entity.split() or entity.startswith("ct"):
            return ["stroke_ct"]
        if "last known well" in entity or "lkw" in entity:
            return ["stroke_lkw"]
    if e.type == "milestone" and "rosc" in e.entity.lower():
        # ROSC satisfies the open ACLS prompts; stroke prompts resume separately
        return ["rhythm_check:", "epi:", "amio:", "shock:"]
    return []


def _live_stream():
    """SSE generator for the live ambient mode (mic or real-time audio file)."""
    import re as _re

    from codeclock.live import LiveSession

    source = request.args.get("source", "file")
    speak = request.args.get("speak") == "1"
    listen = request.args.get("listen") == "1"
    audio_name = request.args.get("audio", "transcript_x3")
    audio_path = Path("data/audio") / f"{audio_name}.wav"
    m = _re.search(r"_x([\d.]+)$", audio_name)
    timescale = float(m.group(1)) if m else 1.0
    if source == "mic":
        timescale = 1.0

    session = LiveSession(
        source=source,
        audio_path=audio_path,
        timescale=timescale,
        speak=speak,
        listen=listen and source == "file",
    )
    session.start()

    def generate():
        yield _sse({"kind": "init", "speed": 1, "live": True, "timescale": timescale})
        try:
            while True:
                msg = session.queue.get()
                kind = msg.get("kind")
                if kind == "done":
                    break
                if kind == "event_obj":
                    yield _sse(_event_msg(msg["event"], msg["t"]))
                elif kind == "guidance_obj":
                    yield _sse(_guidance_msg(msg["guidance"], msg["t"]))
                elif kind == "rubric_obj":
                    yield _sse(_rubric_msg(msg["activation"], msg["t"]))
                elif kind == "finished":
                    events, guidance = msg["events"], msg["guidance"]
                    alerts = [g for g in guidance if g.urgency == "alert"]
                    yield _sse(
                        {
                            "kind": "record",
                            "lines": [
                                {
                                    "t": (e.timestamp - session.code_start).total_seconds(),
                                    "type": e.type,
                                    "entity": e.entity,
                                    "dose": e.dose,
                                    "low_confidence": e.confidence < 0.75,
                                }
                                for e in events
                            ],
                            "totals": {
                                "epi": sum(
                                    1 for e in events
                                    if e.type == "medication" and "epi" in e.entity.lower()
                                ),
                                "shocks": sum(1 for e in events if e.type == "shock"),
                                "guidance": len(guidance),
                                "alerts": len(alerts),
                            },
                            "alerts": [
                                {"message": g.message, "rule_id": g.rule_id} for g in alerts
                            ],
                        }
                    )
                else:
                    yield _sse(msg)  # asr, asr_status, tick, status, error
        finally:
            session.stop()

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/stream")
def stream():
    speed = max(0.5, min(60.0, float(request.args.get("speed", 8))))
    scenario = request.args.get("scenario", "arrest")
    if scenario == "live":
        return _live_stream()
    transcript, cache = SCENARIOS.get(scenario, SCENARIOS["arrest"])

    def generate():
        events, code_start = load_or_extract(transcript, cache)
        events.sort(key=lambda e: e.timestamp)
        router = ProtocolRouter()
        resolved: set[str] = set()

        def elapsed(ts) -> float:
            return (ts - code_start).total_seconds()

        def resolutions(e: ClinicalEvent) -> list[str]:
            ids = []
            for gid, key in router.key_for.items():
                if gid in resolved:
                    continue
                g = next(x for x in router.guidance_log if x.id == gid)
                if g.urgency == "alert":
                    continue  # alerts stay pinned — they're part of the record
                if any(key.startswith(p) for p in _resolution_prefixes(e, router)):
                    resolved.add(gid)
                    ids.append(gid)
            return ids

        yield _sse({"kind": "init", "speed": speed, "n_events": len(events)})
        end = elapsed(events[-1].timestamp) + 8
        pending = list(events)
        t = 0
        while t <= end:
            now = code_start + timedelta(seconds=t)
            while pending and pending[0].timestamp <= now:
                e = pending.pop(0)
                yield _sse(_event_msg(e, elapsed(e.timestamp)))
                activations, guidance = router.on_event(e)
                for a in activations:
                    yield _sse(_rubric_msg(a, elapsed(e.timestamp)))
                for g in guidance:
                    yield _sse(_guidance_msg(g, elapsed(e.timestamp)))
                ids = resolutions(e)
                if ids:
                    yield _sse({"kind": "resolve", "ids": ids})
                if e.type == "milestone" and "rosc" in e.entity.lower():
                    yield _sse({"kind": "status", "status": "rosc"})
            for g in router.on_tick(now):
                yield _sse(_guidance_msg(g, t))
            yield _sse({"kind": "tick", "t": t})
            time.sleep(1.0 / speed)
            t += 1

        alerts = [g for g in router.guidance_log if g.urgency == "alert"]
        yield _sse(
            {
                "kind": "record",
                "lines": [
                    {
                        "t": elapsed(e.timestamp),
                        "type": e.type,
                        "entity": e.entity,
                        "dose": e.dose,
                        "low_confidence": e.confidence < 0.75,
                    }
                    for e in events
                ],
                "totals": {
                    "epi": sum(
                        1 for e in events
                        if e.type == "medication" and "epi" in e.entity.lower()
                    ),
                    "shocks": sum(1 for e in events if e.type == "shock"),
                    "guidance": len(router.guidance_log),
                    "alerts": len(alerts),
                },
                "alerts": [{"message": g.message, "rule_id": g.rule_id} for g in alerts],
            }
        )

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    print(f"Code Clock live view -> http://127.0.0.1:{PORT}")
    app.run(port=PORT, threaded=True)
