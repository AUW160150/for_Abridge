# Code Clock

Ambient, hands-free agent for resuscitations (code blue / code stroke): it
listens to the room, extracts the clinical timeline as it happens, reasons
against the protocol clock in deterministic code, and fires source-linked
guidance in real time. Every logged event traces to the exact utterance that
produced it; every prompt traces to the triggering events and the protocol
rule that fired. Built at the Abridge hackathon; everything in this repo was
built at the event.

**Validated on real audio:** run through actual public body-cam footage of a
2023 cardiac arrest (local Whisper ASR -> extraction -> protocol engine), the
agent picked up the crew's PEA call, logged "300 amio" as amiodarone 300 mg,
tracked their counted pulse checks against the 2-minute clock, surfaced
"epinephrine due — none given, 3:00 into the code" while no epi had been
verbalized, and caught ROSC from "yeah, pulse." The footage itself is not
included in this repo (third-party content) — drop any 16 kHz mono WAV at
`data/audio/bodycam.wav` and use the "LIVE — real body-cam clip" option.

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

## Live ambient mode — the system actually listens

```bash
python make_audio.py data/transcript.txt 3   # synthesize demo audio (multi-voice TTS)
python server.py                             # then pick "LIVE — demo audio" or "LIVE — microphone"
```

Audio (microphone, or a WAV streamed in real time) is transcribed locally
with faster-whisper — raw audio never leaves the machine; only text chunks go
to the extraction model. Word-level timestamps anchor protocol timing, a
domain vocabulary prompt biases the ASR, and the extractor normalizes real
ASR noise ("EP" -> epinephrine, "a meoduron" -> amiodarone) while keeping the
verbatim heard text as provenance. The demo recording is synthesized from the
scripted transcript (multi-voice macOS TTS) so the audio demo stays aligned
with ground truth — no copyrighted TV audio needed. `_x3` files compress the
silences 3x; the pipeline maps audio time back to protocol time so the real
ACLS intervals still apply. The "voice alerts" toggle makes the agent speak
due-now/alert prompts aloud.

## Clinician-facing surfaces

- **/record** — the post-code artifact: alerts with their source utterances,
  the full event log with confirm/flag review per entry (low-confidence
  entries highlighted and never silently trusted), attending sign-off, and
  export (.txt / .json). The draft-until-signed flow keeps judgment with the
  human.
- **/rubrics** — the trust walkthrough: every protocol rule with its
  guideline citation, the timing/dose constants, each scenario's ground-truth
  rubric (required / optional / forbidden / expected guidance), the latest
  eval results with a run-eval-now button, and a governed change-proposal
  queue — clinicians propose constant changes with a rationale; accepted
  changes are one line in `protocol_config.py` and re-run the full test +
  eval suite before shipping.

The scripted transcript (`data/transcript.txt`) seeds one deliberate protocol
deviation — epi #2 given 5:10 after epi #1, outside the ACLS 3-5 min window —
which the protocol engine (Phase 2) will catch for the hero demo beat.

## Multiple rubrics, one router (Phase 5)

Different ER codes run different protocol clocks. A deterministic
`ProtocolRouter` (`codeclock/rubrics.py`) decides which rubric(s) apply —
never the LLM. Activation is evidence-based keyword matching in plain code,
and every activation is a `RubricActivation` record carrying the triggering
event and the exact evidence matched, so "why is this protocol running?" is
always answerable. Rubrics run in parallel when reality demands it: a code
stroke that arrests mid-workup activates the ACLS rubric alongside, holds
the stroke prompts, and resumes them at ROSC — each transition logged with
provenance (`python run_pipeline.py --stroke`, or the "Stroke → arrest"
scenario in the live view).

## Evaluation (Phase 5)

`python run_eval.py` runs offline evals modeled on how clinical AI is
evaluated for enterprise readiness (cf. Abridge's published approach:
clinical accuracy vs physician rubrics, boundary/adversarial testing,
clinical safety screens, staged rollout):

1. **Clinical accuracy** — extraction vs labeled ground truth
   (`data/ground_truth/`): precision / recall / F1, timestamp error, dose and
   value accuracy, confidence calibration by bucket.
2. **Boundary / adversarial** — `data/transcript_adversarial.txt` is nearly
   all traps (negations, holds, hypotheticals, home meds, drawn-up-not-given);
   logging any of them is a critical false positive.
3. **Guidance safety** — deterministic replay must fire every expected prompt
   in its time window and must never fire forbidden prompts (e.g.
   shock-on-PEA); rubric activations are asserted too.
4. **Provenance integrity, checked on every record** — each event's utterance
   must appear verbatim in the transcript; each guidance must chain to
   resolvable event ids and a registered rule. Provenance is the trust layer,
   so it is a gate, not a sampled metric.

Reports land in `eval/report.md` + `eval/report.json`. Current status: all
three scenarios pass every check (P/R/F1 = 1.0, 0 critical FPs, 8/8 expected
prompts per clinical scenario, 100% provenance). Ground truth is authored
with the transcripts and pending clinician review — the harness is built for
the sponsor's `synthetic-ambient-fhir-25` dataset to be dropped in as a
fourth scenario once available.

## Privacy posture

All data in this repo is synthetic — no PHI anywhere. For any real
deployment: transcript chunks are PHI and require a BAA + zero/limited data
retention with the model provider, on-prem or in-VPC ASR, PHI minimization
before the LLM boundary (the deterministic engine never needs identifiers),
and audit logging — the provenance chain doubles as the audit trail.
The eval harness runs entirely offline against cached extractions, so
benchmarking never re-sends data.

## Principles

- Hands-free: no taps, ever.
- Deterministic protocol engine, separate from the LLM.
- Everything source-linked (utterance -> event -> rule).
- Self-evaluating: every extraction carries a confidence score; low confidence
  is flagged for the human, never silently trusted.
- Human-in-the-loop cognitive aid, not an autonomous decision-maker.
