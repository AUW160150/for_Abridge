# Code Clock — Evaluation Report

Offline evals in the style of enterprise clinical-AI evaluation:
clinical accuracy vs ground truth, boundary/adversarial traps, guidance
safety, rubric-activation provenance, and confidence calibration.
All data is synthetic; no PHI. Ground truth authored with the transcripts
(pending clinician review — treat labels as provisional).

## Scenario: adversarial

| metric | value |
|---|---|
| events_extracted | 4 |
| ground_truth_events | 4 |
| true_positives | 4 |
| false_positives | 0 |
| critical_false_positives | 0 |
| false_negatives | 0 |
| precision | 1.0 |
| recall | 1.0 |
| f1 | 1.0 |
| timestamp_mae_s | 3.8 |
| dose_accuracy | 1/1 |
| value_accuracy | n/a |
| provenance_intact_events | 4/4 |
| guidance_fired | 1 |
| expected_guidance_hit | 0/0 |
| forbidden_guidance_fired | 0 |
| guidance_chain_intact | 1/1 |

Confidence calibration (bucket accuracy):

| confidence | n | accuracy |
|---|---|---|
| 0.90-1.00 | 4 | 1.00 |

Findings: none — all checks passed.


## Scenario: arrest

| metric | value |
|---|---|
| events_extracted | 17 |
| ground_truth_events | 13 |
| true_positives | 13 |
| false_positives | 0 |
| critical_false_positives | 0 |
| false_negatives | 0 |
| precision | 1.0 |
| recall | 1.0 |
| f1 | 1.0 |
| timestamp_mae_s | 0.0 |
| dose_accuracy | 3/3 |
| value_accuracy | 1/1 |
| provenance_intact_events | 17/17 |
| guidance_fired | 12 |
| expected_guidance_hit | 8/8 |
| forbidden_guidance_fired | 0 |
| guidance_chain_intact | 12/12 |

Confidence calibration (bucket accuracy):

| confidence | n | accuracy |
|---|---|---|
| 0.75-0.90 | 4 | 1.00 |
| 0.90-1.00 | 13 | 1.00 |

Findings: none — all checks passed.


## Scenario: stroke_arrest

| metric | value |
|---|---|
| events_extracted | 15 |
| ground_truth_events | 13 |
| true_positives | 13 |
| false_positives | 0 |
| critical_false_positives | 0 |
| false_negatives | 0 |
| precision | 1.0 |
| recall | 1.0 |
| f1 | 1.0 |
| timestamp_mae_s | 0.0 |
| dose_accuracy | 3/3 |
| value_accuracy | 3/3 |
| provenance_intact_events | 15/15 |
| guidance_fired | 12 |
| expected_guidance_hit | 8/8 |
| forbidden_guidance_fired | 0 |
| guidance_chain_intact | 12/12 |

Confidence calibration (bucket accuracy):

| confidence | n | accuracy |
|---|---|---|
| 0.75-0.90 | 1 | 1.00 |
| 0.90-1.00 | 14 | 1.00 |

Findings: none — all checks passed.

