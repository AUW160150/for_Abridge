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

import sys
from datetime import datetime, timedelta
from pathlib import Path

from codeclock.display import print_activation, print_event, print_guidance
from codeclock.models import ClinicalEvent
from codeclock.rubrics import ProtocolRouter
from codeclock.store import extract_or_load

TRANSCRIPT = Path("data/transcript.txt")
CACHE = Path("data/events.json")


def load_or_extract(
    transcript: Path = TRANSCRIPT, cache: Path = CACHE
) -> tuple[list[ClinicalEvent], datetime]:
    fresh = "--fresh" in sys.argv
    if cache.exists() and not fresh:
        print(f"(loading cached events from {cache} — use --fresh to re-extract)\n")
    else:
        print(f"(extracting live from {transcript} via Claude...)\n")
    return extract_or_load(transcript, cache, fresh=fresh)


def print_code_record(events: list[ClinicalEvent], reasoner: ProtocolRouter, code_start: datetime) -> None:
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
    if "--stroke" in sys.argv:
        transcript, cache = Path("data/transcript_stroke.txt"), Path("data/events_stroke.json")
    else:
        transcript, cache = TRANSCRIPT, CACHE
    events, code_start = load_or_extract(transcript, cache)
    events.sort(key=lambda e: e.timestamp)
    router = ProtocolRouter()

    print("=== Code Clock — live replay (events + real-time guidance) ===\n")
    end = events[-1].timestamp + timedelta(seconds=10)
    now = code_start
    pending = list(events)
    while now <= end:
        while pending and pending[0].timestamp <= now:
            event = pending.pop(0)
            print_event(event, code_start)
            activations, guidance = router.on_event(event)
            for activation in activations:
                print_activation(activation, code_start)
            for g in guidance:
                print_guidance(g, code_start)
            print()
        for g in router.on_tick(now):
            print_guidance(g, code_start)
            print()
        now += timedelta(seconds=1)

    print_code_record(events, router, code_start)


if __name__ == "__main__":
    main()
