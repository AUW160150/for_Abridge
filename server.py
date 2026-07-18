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

from codeclock.models import ClinicalEvent, Guidance
from codeclock.reasoner import Reasoner
from codeclock.rules import RULES
from run_pipeline import load_or_extract

app = Flask(__name__, static_folder="static")

PORT = 5057


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
            "rule_desc": rule.description if rule else "",
            "rule_source": rule.guideline_source if rule else "",
            "trigger_ids": g.triggering_event_ids,
        },
    }


def _resolution_prefixes(e: ClinicalEvent, reasoner: Reasoner) -> list[str]:
    """Which guidance-key prefixes this event satisfies (display concern only)."""
    if e.type == "rhythm_check":
        prefixes = ["rhythm_check:"]
        if reasoner.engine.pending_shock_for is None:
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
    if e.type == "milestone" and "rosc" in e.entity.lower():
        return [""]  # ROSC satisfies every open (non-alert) prompt
    return []


@app.route("/api/stream")
def stream():
    speed = max(0.5, min(60.0, float(request.args.get("speed", 8))))

    def generate():
        events, code_start = load_or_extract()
        events.sort(key=lambda e: e.timestamp)
        reasoner = Reasoner()
        resolved: set[str] = set()

        def elapsed(ts) -> float:
            return (ts - code_start).total_seconds()

        def resolutions(e: ClinicalEvent) -> list[str]:
            ids = []
            for gid, key in reasoner.key_for.items():
                if gid in resolved:
                    continue
                g = next(x for x in reasoner.guidance_log if x.id == gid)
                if g.urgency == "alert":
                    continue  # alerts stay pinned — they're part of the record
                if any(key.startswith(p) for p in _resolution_prefixes(e, reasoner)):
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
                for g in reasoner.on_event(e):
                    yield _sse(_guidance_msg(g, elapsed(e.timestamp)))
                ids = resolutions(e)
                if ids:
                    yield _sse({"kind": "resolve", "ids": ids})
                if e.type == "milestone" and "rosc" in e.entity.lower():
                    yield _sse({"kind": "status", "status": "rosc"})
            for g in reasoner.on_tick(now):
                yield _sse(_guidance_msg(g, t))
            yield _sse({"kind": "tick", "t": t})
            time.sleep(1.0 / speed)
            t += 1

        alerts = [g for g in reasoner.guidance_log if g.urgency == "alert"]
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
                    "guidance": len(reasoner.guidance_log),
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
