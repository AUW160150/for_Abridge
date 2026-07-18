"""Console rendering for the event stream.

This is the Phase 0/1 stand-in for the live view: every printed event shows
its classification, confidence, and the exact source utterance behind it.
"""

from __future__ import annotations

from datetime import datetime

from .models import ClinicalEvent, Guidance, RubricActivation
from .rules import RULES

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


_URGENCY_COLORS = {
    "info": _DIM,
    "due_soon": "\033[33m",      # yellow
    "due_now": "\033[1;93m",     # bold yellow
    "alert": "\033[1;91m",       # bold red
}


def format_guidance(guidance: Guidance, code_start: datetime) -> str:
    color = _URGENCY_COLORS.get(guidance.urgency, "")
    total = int((guidance.issued_at - code_start).total_seconds())
    stamp = f"{total // 60:02d}:{total % 60:02d}"
    events = ", ".join(guidance.triggering_event_ids) or "-"
    rule = RULES.get(guidance.rule_id)
    source = rule.guideline_source if rule else "unknown rule"
    rubric = f"rubric: {guidance.rubric_id} · " if guidance.rubric_id else ""
    return "\n".join(
        [
            f"[{stamp}]   {color}▶ {guidance.urgency.upper()}: {guidance.message}{_RESET}",
            f"           {_DIM}{rubric}rule: {guidance.rule_id} · events: {events}{_RESET}",
            f"           {_DIM}per: {source}{_RESET}",
        ]
    )


def print_guidance(guidance: Guidance, code_start: datetime) -> None:
    print(format_guidance(guidance, code_start))


def print_activation(activation: RubricActivation, code_start: datetime) -> None:
    total = int((activation.activated_at - code_start).total_seconds())
    stamp = f"{total // 60:02d}:{total % 60:02d}"
    print(
        f"[{stamp}]   \033[1;96m◆ RUBRIC ACTIVATED: {activation.rubric_id}{_RESET}\n"
        f"           {_DIM}{activation.reason}{_RESET}"
    )
