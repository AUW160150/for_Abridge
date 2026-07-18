"""Extraction cache: run the Claude extractor once per transcript, replay free.

Cached JSON keeps the full ClinicalEvent payload (ids, utterances, confidence)
so replays, the live view, and the eval harness all operate on identical data.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import ClinicalEvent


def _load_dotenv() -> None:
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    import os

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


def extract_or_load(
    transcript: Path, cache: Path, fresh: bool = False
) -> tuple[list[ClinicalEvent], datetime]:
    if cache.exists() and not fresh:
        payload = json.loads(cache.read_text())
        code_start = datetime.fromisoformat(payload["code_start"])
        events = [
            ClinicalEvent(
                id=e["id"],
                timestamp=datetime.fromisoformat(e["timestamp"]),
                type=e["type"],
                entity=e["entity"],
                dose=e.get("dose"),
                value=e.get("value"),
                source_utterance=e["source_utterance"],
                confidence=e["confidence"],
            )
            for e in payload["events"]
        ]
        return events, code_start

    from .extractor import Extractor

    _load_dotenv()
    extractor = Extractor()
    events = list(extractor.stream(transcript.read_text()))
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
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
                        "value": e.value,
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
