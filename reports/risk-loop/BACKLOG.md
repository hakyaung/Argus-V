# Risk-loop BACKLOG

Source: TASK.md §6 (initial hypotheses). Updated by A6 after Iteration 0. Statuses: next / open / blocked / on-hold / rejected / confirmed.
Evidence refers to LEDGER.md rows (iter-candidate).

- **Naming**: models are Argus-V{n} (structural upgrades only); earlier candidates = Argus-V0 history. Current: Argus-V1 (max pooling).

## Before Iteration 1 (blocking, from A5 audit `iter-00/eval/audit.md` §5)

| ID | item | status |
|---|---|---|
| P-01 | Harness BUG-1: `within_group_shuffle` iterates `set()` of family ids, so order depends on PYTHONHASHSEED. Fix with `sorted(...)` and set default n_repeats ≥ 20 | **done** (iter-01, harness sha256 4e9278bc…) |
| P-02 | Harness BUG-2: pooled-OOF length-stratified CV AUROC is biased by fold offsets. Use (fold, tertile) strata and report fold-stratified AUROC; add a test | **done** (iter-01, test added) |
| P-03 | Re-hash and re-freeze the harness, record the new sha256 in LEDGER, re-run iter-00 train CV only (no validation looks) | **done** (LEDGER iter-01: L16 0.617/0.619, L23 0.581/0.581, length+step 0.551/0.476) |
| P-05 | Harness BUG-3 (G4 rule → beats length+step) and BUG-4 (label-shuffle audit on fold-stratified AUROC); re-freeze before iteration 2 | open (blocking, from iter-01 A5) |
| P-06 | G0 should compare with the stronger of length_only / length+step and use fold-stratified AUROC; record selection count | open (needs TASK change by human) |
| P-04 | Minor fixes: `audits()` explicit g5 aggregate; `_score_vector` rejects non-validation rows; leakage reference values | optional |

## Hypotheses (TASK.md §6)

| ID | hypothesis | owner | status | notes / evidence |
|---|---|---|---|---|
| H-01 | Re-comparing existing models with dedup + family GroupKFold changes the ranking | A2·A4 | **next** (required in Iter 1) | Iter-0: on dedup CV, L16 > L23 > length+step (pooled). Under corrected fold-stratified AUROC (audit), length+step 0.675 beats L23 0.658, L16 0.716. Re-run after P-03 |
| H-02 | Signal remains after length/step-matched negatives or after removing the length direction from activations | A2·A3 | open (high priority) | 0-C-00c: length-residual G2 0.663 [0.529, 0.796] PASS; 0-C-00b FAIL (0.597). Corr(score, log length) 0.28–0.31 on validation |
| H-03 | Training on pre-first-violation samples only raises validation first-violation detection | A2 | open | 28/88 train positives come after the first violation; after-first-violation exclusion G1 FAIL for C-00b/c |
| H-04 | Observation point `reasoning_end` beats `pre_action` | A3 | open | Rule-label exploration: L11–17 AUROC 0.75–0.78; needs reviewed labels; data in replay artifacts |
| H-05 | Multi-layer (0·11·23, per-layer PCA, concatenated) beats single layer | A3 | open | 0-C-00c (L16) > 0-C-00b (L23) on CV and validation; mid layers are promising |
| H-06 | Reasoning-token sequence pooling (last-k mean, max, attention pooling) | A3·A4 | **next** (partly tested) | iter-01 D-v2 action: observation-row mean L11 0.696 (stratified 0.67) and MIL last-4 mean 0.649 (0.69) beat pre_action (0.53–0.65); dimension max 0.778 falls to 0.556 under length stratification. v1 replay: all-token mean 0.53–0.56 (negative). Per-token max needs a GPU re-replay |
| H-07 | Robust scaling / \|z\| clipping reduces memorisation of rare extreme dims | A3 | open | Train in-sample AUROC 0.971 / 0.982 vs CV 0.65 / 0.70: still overfitting |
| H-08 | Strongly regularised families (shrinkage LDA, PCA+logistic, L-BFGS L2) are more stable than Adam | A4 | open | Iter-0 already uses L-BFGS L2 C=0.001 |
| H-09 | Nonlinear models (GBM, MLP, kNN) | A4 | blocked | Only after H-02 confirms signal beyond length |
| H-10 | Trajectory aggregation (running max, exponential smoothing) reduces normal-trajectory false alarms | A4 | open | G4 passes even for the baseline (2/9 @5%); resolution 1/9 |
| H-11 | Horizon labels (y_h2, y_h3) strengthen the signal | A2 | open | Fix the horizon once on validation |
| H-12 | M2: activation added to length+step+M1 improves | A4·A5 | on-hold | Needs M1 scores (GPU judge needs human approval) |
| H-13 | Fix data shortage: add validation violation runs across more families | A7 | on-hold (human approval) | Validation has 12 violations (< 15) and 4 violation families, which is the §7 data-blocker zone; G4 is measured in steps of 1/9 |

- **P-07** (needs TASK change by a human): add a tool-identity baseline ("next action is a domain tool", plus within-same-tool AUROC) to G0/G1. Iter-02 on D-v2r passed G0 at 0.78–0.84 while the domain-tool indicator alone gives 0.80 and within-tool AUROC is 0.40–0.45.
- **H-14** (open): violation signal within the same tool. Needs more positives per tool (contrastive legit/violating calls of one tool); the current data has 1–8 positives per tool.

## Rejected
- **Argus-V1** (2026-11-06): max pooling L16, val AUROC 0.751, G4 FAIL (0/28 vs length+step 6/28); domain-tool indicator alone 0.791. See LEDGER.

| ID | item | evidence |
|---|---|---|
| R-01a | Mahalanobis anomaly score on pre_action (trained on normal rows only) | iter-01 D-v2 action CV 0.50–0.57, stratified ≤0.55 |
| R-01b | report_content violations as a risk-model target on current data | iter-01: 0.93–0.95 overall, but submit_result-only control activation 0.601 = length+step 0.603 |
| R-01d | C-01a / C-01b / C-01c (iter-01) | G4 fail (2/25, 0/25, 1/25 vs baseline 7/25); G1 pass driven by F02 only; C-01c length-confounded (n_obs corr 0.39) |
| R-01c | Dimension-wise max pooling over observation rows as a length-robust feature | iter-01: L16 0.778 → 0.556 stratified |
| R-00b | L23 strongly regularised logistic regression on pre_action (C-00b) as a success candidate | 0-C-00b: G1 −0.089 [−0.231, 0.074], G2/G3/G5 FAIL |
| R-00c | L16 strongly regularised logistic regression on pre_action (C-00c) as a success candidate | 0-C-00c: G1 −0.045 [−0.206, 0.119], G3/G5 FAIL (kept as the reference for H-02/H-05) |

## Data notes
- D-v2r (2026-11-05): review-draft labels; substantive action train 55 / val 29 positives, val families 5 (≥4 counts only if v1 rows and draft labels are accepted). Draft labels need human confirmation before any validation look.

- iter-01 (A5/label audit): validation action violations come from 3 families only (data blocker). v2/v1h2 labels are unreviewed rule labels and v1 review found the rules missed 69/132 violations: review of v2 action-violation candidates (H-14, new) is the cheapest way to add positives. v2 split is still a draft awaiting human approval.

- iter-01: D-v2 train has only 24 action violations (11 families); fold AUROCs range 0.39–0.98. H-13 (more action-violation runs, §10.1 pressure revision) is now the main blocker.
- v1 reasoning replays keep only mean/last/reference rows; full per-token tensors were deleted and are not on HF.

- Train dup_group 377 has conflicting labels (rows 535 and 549, principal_impersonation). Report to a human for label review (§10); do not change it.
