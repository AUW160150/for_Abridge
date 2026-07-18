"""Console rendering for the event stream.

This is the Phase 0/1 stand-in for the live view: every printed event shows
its classification, confidence, and the exact source utterance behind it.
"""

from __future__ import annotations

from datetime import datetime

from .models import ClinicalEvent

LOW_CONFIDENCE_THRESHOLD = 0.75

_TYPE_COLORS = {
    "medication": "\033[95m",    # magenta
    "rhythm_check": "\033[96m",  # cyan
    "shock": "\033[93m",         # yellow
    "procedure": "\033[94m",     # blue
    "assessment": "\033[92m",    # green
    "milestone": "\033[1;97m",   # bold white
}
_RESET = "\033[0m"
_DIM = "\033[2m"
_WARN = "\033[1;91m"


def format_event(event: ClinicalEvent, code_start: datetime) -> str:
    color = _TYPE_COLORS.get(event.type, "")
    dose = f"  {event.dose}" if event.dose else ""
    conf_flag = (
        f"  {_WARN}⚠ LOW CONFIDENCE — confirm{_RESET}"
        if event.confidence < LOW_CONFIDENCE_THRESHOLD
        else ""
    )
    lines = [
        f"[{event.elapsed_str(code_start)}] "
        f"{color}{event.type.upper():<12}{_RESET} "
        f"{event.entity}{dose}"
        f"  {_DIM}(conf {event.confidence:.2f}){_RESET}{conf_flag}",
        f'         {_DIM}source: "{event.source_utterance}"{_RESET}',
    ]
    return "\n".join(lines)


def print_event(event: ClinicalEvent, code_start: datetime) -> None:
    print(format_event(event, code_start))
