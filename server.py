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
import time
from datetime import timedelta

from flask import Flask, Response, request, send_from_directory

from pathlib import Path

from codeclock.models import ClinicalEvent, Guidance, RubricActivation
from codeclock.rubrics import RUBRIC_NAMES, ProtocolRouter
from codeclock.rules import RULES
from run_pipeline import load_or_extract

app = Flask(__name__, static_folder="static")

PORT = 5057

SCENARIOS = {
    "arrest": (Path("data/transcript.txt"), Path("data/events.json")),
    "stroke": (Path("data/transcript_stroke.txt"), Path("data/events_stroke.json")),
}


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


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


@app.route("/api/stream")
def stream():
    speed = max(0.5, min(60.0, float(request.args.get("speed", 8))))
    scenario = request.args.get("scenario", "arrest")
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
