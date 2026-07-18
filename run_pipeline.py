"""Phase 3: full pipeline — extractor -> timeline -> engine -> reasoner -> console.

Replays the code on a simulated clock: events land at their real elapsed
times, the reasoner polls the engine every second, and guidance prints
interleaved with the events that triggered it. Extraction results are cached
to data/events.json so replays don't re-call the API (pass --fresh to
re-extract).

Usage:
    python run_pipeline.py [--fresh]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from codeclock.display import print_event, print_guidance
from codeclock.models import ClinicalEvent
from codeclock.reasoner import Reasoner

TRANSCRIPT = Path("data/transcript.txt")
CACHE = Path("data/events.json")


def load_or_extract() -> tuple[list[ClinicalEvent], datetime]:
    if CACHE.exists() and "--fresh" not in sys.argv:
        payload = json.loads(CACHE.read_text())
        code_start = datetime.fromisoformat(payload["code_start"])
        events = [
            ClinicalEvent(
                id=e["id"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
                type=e["type"],
                entity=e["entity"],
                dose=e["dose"],
                source_utterance=e["source_utterance"],
                confidence=e["confidence"],
            )
            for e in payload["events"]
        ]
        print(f"(loaded {len(events)} cached events from {CACHE} — use --fresh to re-extract)\n")
        return events, code_start

    from run_extractor import _load_dotenv
    from codeclock.extractor import Extractor

    _load_dotenv()
    extractor = Extractor()
    print(f"(extracting live from {TRANSCRIPT} via Claude...)\n")
    events = list(extractor.stream(TRANSCRIPT.read_text()))
    CACHE.write_text(
        json.dumps(
            {
                "code_start": extractor.code_start.isoformat(),
                "events": [
                    {
                        "id": e.id,
                        "timestamp": e.timestamp.isoformat(),
                        "type": e.type,
                        "entity": e.entity,
                        "dose": e.dose,
                        "source_utterance": e.source_utterance,
                        "confidence": e.confidence,
                    }
                    for e in events
                ],
            },
            indent=2,
        )
    )
    return events, extractor.code_start


def print_code_record(events: list[ClinicalEvent], reasoner: Reasoner, code_start: datetime) -> None:
    print("\n" + "=" * 72)
    print("CODE RECORD (auto-generated, hands-free — every line source-linked)")
    print("=" * 72)
    for e in events:
        dose = f" {e.dose}" if e.dose else ""
        flag = "  [LOW CONFIDENCE — needs confirmation]" if e.confidence < 0.75 else ""
        print(f"  {e.elapsed_str(code_start)}  {e.type:<13} {e.entity}{dose}{flag}")
    epi = sum(1 for e in events if e.type == "medication" and "epi" in e.entity.lower())
    shocks = sum(1 for e in events if e.type == "shock")
    alerts = [g for g in reasoner.guidance_log if g.urgency == "alert"]
    print("-" * 72)
    print(f"  totals: {epi}x epinephrine, {shocks} shocks, "
          f"{len(reasoner.guidance_log)} guidance prompts ({len(alerts)} alerts)")
    for g in alerts:
        print(f"  ALERT: {g.message}  [rule {g.rule_id}]")


def main() -> None:
    events, code_start = load_or_extract()
    events.sort(key=lambda e: e.timestamp)
    reasoner = Reasoner()

    print("=== Code Clock — live replay (events + real-time guidance) ===\n")
    end = events[-1].timestamp + timedelta(seconds=10)
    now = code_start
    pending = list(events)
    while now <= end:
        while pending and pending[0].timestamp <= now:
            event = pending.pop(0)
            print_event(event, code_start)
            for guidance in reasoner.on_event(event):
                print_guidance(guidance, code_start)
            print()
        for guidance in reasoner.on_tick(now):
            print_guidance(guidance, code_start)
            print()
        now += timedelta(seconds=1)

    print_code_record(events, reasoner, code_start)


if __name__ == "__main__":
    main()
