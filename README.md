# Code Clock

Ambient, hands-free agent for resuscitations (code blue): listens to the live
transcript, extracts the clinical timeline as it happens, reasons against the
ACLS protocol clock in deterministic code, and fires source-linked guidance.
Every logged event traces to the exact utterance that produced it; every prompt
traces to the triggering events and the protocol rule that fired.

## Architecture

```
[audio/transcript stream]
   -> (1) extractor        codeclock/extractor.py   (LLM: Claude — emits ClinicalEvent)
   -> (2) event timeline   append-only log of confirmed events
   -> (3) protocol engine  deterministic state machine, plain code (Phase 2)
   -> (4) reasoner         emits Guidance {message, urgency, events, rule_id} (Phase 3)
   -> (5) live view        timeline + guidance + click-through to source (Phase 4)
```

Data models for all components: `codeclock/models.py`.

## Setup

```bash
source abridge/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...   # or: ant auth login
```

## Run

```bash
python phase0_demo.py     # Phase 0: one hardcoded event end-to-end, no API
python run_extractor.py   # Phase 1: stream data/transcript.txt through Claude
python -m pytest tests/   # Phase 2: protocol engine unit tests (no LLM)
python run_pipeline.py    # Phase 3: full pipeline replay in the console
python server.py          # Phase 4: live view -> http://127.0.0.1:5057
```

The live view replays the code in real time (time-scaled): the timeline
assembles hands-free, guidance fires from the protocol engine, satisfied
prompts gray out, alerts stay pinned, and clicking any event or prompt
reveals the exact utterance heard plus the protocol rule that fired. At code
end it emits the downloadable code record. Extraction results are cached in
data/events.json; pass --fresh to run_pipeline.py to re-extract.

The scripted transcript (`data/transcript.txt`) seeds one deliberate protocol
deviation — epi #2 given 5:10 after epi #1, outside the ACLS 3-5 min window —
which the protocol engine (Phase 2) will catch for the hero demo beat.

## Principles

- Hands-free: no taps, ever.
- Deterministic protocol engine, separate from the LLM.
- Everything source-linked (utterance -> event -> rule).
- Self-evaluating: every extraction carries a confidence score; low confidence
  is flagged for the human, never silently trusted.
- Human-in-the-loop cognitive aid, not an autonomous decision-maker.
