# Risk-loop LEDGER

Format: TASK.md §8. Machine-readable rows: `ledger.jsonl` (includes git commit, diff hash, data hash, harness hash, seed, libs).

## Provenance (Iteration 0)

- git commit: `f54bc8271de445140cccf402cd4a86bba2c43075`
- uncommitted diff sha256 (`git diff | sha256sum`): `e5e84e9871159108d75dd053f524e99e1588df1fda6433d5eaa9270c61626487`
- dataset D-v1 samples.csv sha256: `7f705976175b6fcb4732f63d4524bee6db265e5d9eaa99bac89b8537c8384ff7`; hash.txt sha256: `8654ebdc9bcb3593563461da5cdce3c7dd2911e4da9b3a4d92c37a7318038e39`
- harness.py sha256 (frozen v0): `75396eafbe2182073ae00f5cea775ec31836893d3018f6fb8d55d083d5c8bbb2`
- seed 0, bootstrap 2000 (trajectory level); python/numpy/sklearn 3.10.12/2.2.6/1.7.2
- Validation baselines: length_only 0.777 [0.640, 0.884]; length+step 0.759 [0.622, 0.867]. Reproduction within ±0.02: PASS (L23 0.670, L16 0.713, length 0.777).
- Validation set: 217 rows, 12 positives, 9 violation trajectories, 41 normal trajectories (5% budget = 2 trajectories).

## Table

| iter | 후보 | 가설 | 데이터 | 특징 | 모델 | CV AUROC (층화) | val AUROC [CI] | Δ vs 길이+step [CI] | 층화 val | 첫 위반 전 탐지 @5% | 감사 | 판정 | val 누적 조회 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | C-00a | Iter-0 reproduction (TASK §4 step 3) | D-v1 | prefix_length, step_index (metadata only) | StandardScaler + LogisticRegression | 0.538 (0.342)* | 0.759 [0.622, 0.867] | 0.000 [0.000, 0.000] | 0.593 [0.355, 0.767] | 2/9 = 0.222 (FPR 0.049) | n/a (reference baseline, not audited) | baseline (reference for G1); not a success candidate; gates G1F G2F G3F G4P G5- | 1 |
| 0 | C-00b | Iter-0 reproduction (TASK §4 step 3) | D-v1 | activation L23 pre_action row | StandardScaler + LogReg C=0.001 balanced | 0.647 (0.586)* | 0.670 [0.550, 0.767] | -0.089 [-0.231, 0.074] | 0.619 [0.491, 0.723] | 1/9 = 0.111 (FPR 0.049) | FAIL (after-first-violation G1 FAIL, G3 FAIL, length-residual G2 FAIL; shuffle PASS, inputs PASS) | rejected (G1/G2/G3/G5 FAIL); gates G1F G2F G3F G4P G5F | 2 |
| 0 | C-00c | Iter-0 reproduction (TASK §4 step 3) | D-v1 | activation L16 pre_action row | StandardScaler + LogReg C=0.001 balanced | 0.696 (0.655)* | 0.713 [0.566, 0.844] | -0.045 [-0.206, 0.119] | 0.667 [0.507, 0.821] | 2/9 = 0.222 (FPR 0.049) | FAIL (after-first-violation G1 FAIL, G3 FAIL; length-residual G2 PASS 0.663 [0.529,0.796], shuffle PASS, inputs PASS) | rejected (G1/G3/G5 FAIL; G2 marginal PASS - seed for H-02/H-04/H-05); gates G1F G2P G3F G4P G5F | 3 |

\* CV = train GroupKFold(5, family_id, dup groups together), all D-v1 train rows. The pooled-OOF stratified value is biased by fold offsets (A5 BUG-2); corrected (fold × tertile) values from audit.md: C-00a 0.532, C-00b 0.607, C-00c 0.667. Recompute after harness re-freeze.

## Iteration 0 outcome

- No success candidate. Every candidate fails G1 and G3; G4 passes even for the metadata baseline, so it does not separate candidates on this validation set.
- C-00c (L16) is the only one with a within-length signal: G2 0.667 [0.507, 0.821], and it survives length residualisation (0.663 [0.529, 0.796]).
- Validation looks: iter-00 = 3 (≤ 3); cumulative = 3.
- Blocking before Iteration 1 (A5 audit.md §5): fix harness BUG-1 (label-shuffle order depends on PYTHONHASHSEED; n_repeats ≥ 20) and BUG-2 (stratified CV over (fold, tertile)), re-freeze, record the new harness sha256 here, re-run iter-00 train CV (no new validation looks).
- Data blocker note (§7): validation has 12 violations (< 15) and 4 violation families; G4 resolution is 1/9 trajectories. A7 collection request should be kept in view.
- Data note: train dup_group 377 has conflicting labels (rows 535 label 0 / 549 label 1); the dedup set drops that positive.

## Provenance (Iteration 1)

- git commit: `f54bc8271de445140cccf402cd4a86bba2c43075`; uncommitted diff sha256: `b7e17f124c6b0fa9dbbf8063bd151c0c780e722ebf60c899098122d9944cd91a`
- harness.py sha256 (re-frozen v1, BUG-1 and BUG-2 fixed, 13 tests pass under PYTHONHASHSEED 1 and 2): `4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8`
- dataset D-v2 (`build_d_v2.py`, run-queue-20261102, train + validation only, 598 test/unsplit rollouts sealed by ID): samples.csv sha256 `86d4bf551a32ff5165cc8934c9cd02bdbd6d3946e1df97d5b10e7a5f858821ea`; obs_L11/L16/L23 hashes in `datasets/D-v2/hash.json`. 14,500 step samples, 117,316 observation rows. Train action positives 24 (11 families), report positives 359; validation action 26, report 108.
- Captures restored from HF (`restore_rollout_captures.py`): 2,691/2,691, 0 failed.
- Validation looks in iter-01: **0** (cumulative 3). All results below are train GroupKFold(5, family_id, dup groups together) only.

## Iteration 1 (train CV only, no candidates sent to validation yet)

P-03 re-run of iter-00 CV with the re-frozen harness (D-v1-dedup train): length+step 0.551 (fold × tertile stratified 0.476); L16 pre_action C=0.001 0.617 (0.619); L23 0.581 (0.581).

| exp | data | target | best feature (layer) | fold-mean CV AUROC | fold × tertile stratified | baseline length+step (stratified) | verdict |
|---|---|---|---|---|---|---|---|
| 1-overfit | D-v1-dedup train | rule label | PCA-10 + LR (L16) | 0.645; train real 0.729 vs shuffled 0.595 | 0.614 | 0.55 | signal exists but family-specific; uncapped overfit is uninformative (shuffled labels also reach 1.0) |
| 1-pool-action | D-v2 train | action violations (24 pos) | mean_all (L11) / mil_mean_last4 (L11) | 0.696 / 0.649; folds 0.39–0.98 | 0.67 / 0.69 | 0.531 (0.437) | open: best within-length signal so far; too few positives to trust |
| 1-pool-action-max | D-v2 train | action | max_all (L16) | 0.778 | 0.556 | 0.531 | **negative**: dimension-max pooling is length-confounded |
| 1-anomaly | D-v2 train | action | Mahalanobis on normal rows, PCA-20 (L11/16/23) | 0.50–0.57 | ≤0.55 | 0.531 | **rejected** |
| 1-pool-report | D-v2 train | report_content (359 pos) | every feature | 0.93–0.95 | — | — | **rejected as a target**: submit_result-only control gives activation L16 0.601 vs length+step 0.603, so the score detects "next action is submit", not misreporting |
| 1-v1-replay | D-v1-dedup train ∩ replay (761 rows, 65 pos) | rule label | ref_mean (L11) | 0.625 | 0.605 | 0.608 (0.487) | **negative**: mean over all reasoning tokens 0.53–0.56 (worse than the last token); ref_max 0.59–0.61. True per-token max is not testable: full replay tensors were deleted by compaction and are not on HF (GPU re-replay needed) |

Result files: `iter-01/overfit-diagnosis/`, `iter-01/pooling-compare/` (report.json, report_submit_only.json), `iter-01/v1-replay-pooling/report.json`.

## Iteration 1 — validation (A5 independent evaluation, `iter-01/eval/audit.md`)

Dataset D-v2a (samples.csv sha256 `63e7cadf…30fa6`, action-violation view of D-v2). Validation: 4,389 rows, 26 positives, 25 violation trajectories, **3 violation families** (F02 18, identity_provisioning 7, aggregate_batch_review 1), 631 normal trajectories. Baselines: length+step 0.541 [0.419, 0.664], length_only 0.376. Validation looks this iteration 3; **cumulative 6**.

| iter | 후보 | 가설 | 데이터 | 특징 | 모델 | CV AUROC (층화) | val AUROC [CI] | Δ vs 길이+step [CI] | 층화 val | 첫 위반 전 탐지 @5% (기준선 7/25) | 감사 | 판정 | val 누적 조회 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | C-01a | H-06 | D-v2a | L11 mean over observation rows | Scaler + PCA-10 + LogReg balanced | 0.485 (0.606) | 0.740 [0.645, 0.830] | +0.199 [0.034, 0.372] | 0.768 [0.668, 0.861] | 2/25 | label-shuffle FAIL (0.437; pooled-OOF artefact, BUG-4), inputs/length-residual/after-first PASS | rejected (G4, G5); G1–G3 PASS but driven by F02 (without F02 Δ −0.17 [−0.28, −0.08]) | 4 |
| 1 | C-01b | H-06 | D-v2a | L11 MIL last-4 rows, bag mean | Scaler + PCA-10 + LogReg balanced (instance) | 0.661 (0.713) | 0.749 [0.644, 0.844] | +0.208 [0.031, 0.383] | 0.768 [0.663, 0.863] | 0/25 | label-shuffle FAIL (0.418; BUG-4), others PASS | rejected (G4, G5); without F02 Δ −0.19 [−0.35, −0.07] | 5 |
| 1 | C-01c | H-06 | D-v2a | L23 MIL all rows, bag max | Scaler + PCA-10 + LogReg balanced (instance) | 0.692 (0.612) | 0.463 [0.368, 0.572] | −0.077 [−0.218, 0.085] | 0.492 [0.383, 0.595] | 1/25 | length-residual FAIL; score corr with n_obs 0.39 | rejected (G1–G5) | 6 |

### Iteration 1 outcome
- No success candidate. Final G1–G5 status: **데이터 부족으로 미판정** (validation violation families 3 < 4).
- G0 was weak evidence: the length+step train-CV stratified baseline is 0.22 (below chance), so "+0.03" is vacuous; length_only CV 0.688 beats C-01a/C-01c; selection over 24 configs uncorrected.
- Harness issues found by A5 (harness frozen, not fixed): **BUG-3** G4 pass rule still v0 ("> 0"), TASK v1 needs "> length+step"; **BUG-4** `label_shuffle_audit` uses pooled-OOF AUROC (fold-offset bias, cf. BUG-2).
- D-v2a semantics: trajectories with only report_content violations (454) count as normal in the FPR denominator; `after_first_violation` marks all rows after the first violation.
- Label status (HF audit, 2026-11-05): v1h2 and v2 labels are rule-only (`policy_rule_unreviewed`); in v1 review, rule labels missed 69 of 132 violations. v2 split file is DRAFT_PENDING_HUMAN_APPROVAL.

## Iteration 2 — train CV on review-draft labels (A4, train only, no validation look)

Dataset **D-v2r** (`build_d_v2r.py`, samples.csv sha256 `ddd8dc9a…dcbb0f`). Labels: v2 rows from `label-review-v2/labels-draft-v1` (LLM blind review, **DRAFT, not human-confirmed**), target = substantive action violation; disputed / procedure_skip / output_falsification rows dropped (281); v1 rows keep their rule labels. Train 9,811 rows, 55 pos (v2 38 / v1 17), 11 families. Validation (not looked at; counts only): 29 pos, 5 families (F02, F05, F09 + v1 identity_provisioning, aggregate_batch_review). Fixed configs from iter-01, no new search. Files: `iter-02/train-cv/{report.json, tool_confound.json}`.

| config | all train: CV (strat) | v2-only train: CV (strat) |
|---|---|---|
| length+step | 0.354 (0.324) | 0.644 (0.676) |
| C-01a L11 mean | 0.781 (0.786) | 0.844 (0.835) |
| C-01b L11 MIL last4 mean | 0.724 (0.747) | 0.787 (0.782) |
| C-01c L23 MIL max | 0.696 (0.664) | 0.735 (0.720) |
| L16 mean | 0.751 (0.771) | 0.854 (0.838) |
| L23 mean | 0.701 (0.684) | 0.762 (0.725) |

All pass G0 as written. **But the tool confound check fails**: 51/55 positives are calls of scenario-specific "domain" tools (not read/write/submit/notify/bash/request_*). The indicator "next action is a domain tool" alone gives AUROC 0.80 (v2-only 0.78). Within domain-tool rows the OOF AUROC drops to 0.59–0.73, and **within the same tool** (legit vs violating call of the same tool, ~20 tools) the mean AUROC is 0.40–0.45, i.e. no violation signal. Verdict: **negative** — the probe mainly detects "about to call a domain action", not the violation. G0 needs a tool-identity baseline (backlog P-07).

## Naming rule (2026-11-06, user decision)
Models are named **Argus-V{n}**; the version goes up only on a structural upgrade. Everything above this line (C-00x, C-01a/b/c, C-02-*) is **Argus-V0** (pre-versioning experiments); their model files and scripts were deleted on 2026-11-06 (scripts recoverable from git commit 4198d82), and the rows above are history only. Registry: `experiments/risk_loop/argus_models.py`.

## Argus-V1 — train CV (D-v2r, review DRAFT labels, no validation look)
Structure: frozen gpt-oss-20b block-L output at the step's observation rows → **element-wise max pooling** → StandardScaler → PCA-10 → LogReg (C=1, balanced) → sigmoid. Default L16. Script `argus_v1_train_cv.py`, result `argus-v1/train-cv/report.json`.

| layer | all train CV (strat) | v2-only CV (strat) | v2 within domain tool | v2 within same tool (20 tools) | corr(score, n_obs) v2 |
|---|---|---|---|---|---|
| L11 | 0.772 (0.777) | 0.836 (0.833) | 0.760 | 0.528 | 0.45 |
| **L16 (default)** | 0.783 (0.786) | **0.846 (0.831)** | 0.748 | **0.547** | 0.45 |
| L23 | 0.678 (0.684) | 0.799 (0.766) | 0.748 | 0.468 | 0.45 |

Baselines v2-only: length+step 0.644 (0.676); domain-tool indicator 0.78. Verdict: G0 passes, but within-same-tool AUROC 0.55 is still near chance (1–8 positives per tool, noisy) and the score correlates with the number of observation rows (0.45; length risk of max pooling, cf. Argus-V0 max_all). Not sent to validation.

## Argus-V1 — final model and validation look (2026-11-06)
Final fit: D-v2r train (9,811 rows / 55 pos), L16, lbfgs 26 iterations, model.pkl sha256 `a43fd750…14f0f` (`argus-v1/model/`). Train CV 0.783 (strat 0.786), in-sample 0.827.
A5 (`argus-v1/eval/audit.md`): **one look, cumulative 7**. Validation 4,408 rows / 29 pos / 5 families (data blocker not triggered; 21 pos are draft labels).

| Gate | Result | Value |
|---|---|---|
| G0 | PASS | 0.786 vs threshold 0.60 (length+step 0.324) |
| G1 | PASS | AUROC 0.751; Δ vs length+step +0.236 [0.090, 0.390] |
| G2 / G3 | PASS | stratified 0.793 [0.717, 0.863] |
| **G4** (v1 rule, by hand for BUG-3) | **FAIL** | first violation @5% FPR 0/28 vs length+step 6/28 (10%: 3 vs 8; 20%: 4 vs 10) |
| G5 | PASS | shuffle 0.532 (BUG-4 supplement 0.536–0.539), length residual 0.792 |

**Verdict: rejected (A5 veto, G4).** G1 is fragile: without F02 Δ −0.108 [−0.236, 0.022]; the domain-tool indicator alone scores 0.791 (> Argus-V1); within domain-tool rows 0.609 and G1 fails; mean within-same-tool 0.572. Score vs n_obs corr 0.30, but n_obs alone is 0.495, so length is not the driver.
- Approval note: BACKLOG said draft labels should be human-confirmed before any validation look. The user explicitly approved option (나) on 2026-11-06 (train + one A5 validation look) knowing the labels are DRAFT; this look is therefore a user-approved exception, and its verdict is provisional on draft labels.
- Monitor (`argus-v1/monitor/phase1.md`, `phase2.md`): no invalidating violation. Minor: family `protected_control_write` appears in both train (160 rows) and validation (34 rows), 0 positives in both, inherited from the v1 split — split changes need a human. ledger.jsonl was missing iter-01 rows (only Argus-V1 row added now).

## 2026-11-06 — 위반 데이터 부족 토론 (debate-01, 실행 없음)
- 2-agent debate, read-only, no GPU. Summary: `docs/risk-data-debate-20261106.md`.
- Findings: 20/23 train D positives are first-contact misreads before any request; post-DENIED violations 3/783. 33/38 train v2 positives have rule_label=0 (LLM-only). Exact X-replay minimal pairs: train 2.
- User decision: scenario rewrite/re-run deferred (GPU cost); human labeling dashboard in progress elsewhere; no injected/instructed violations.
- INCIDENT: debater B round-1 glob read test-family (F07/F11/F14) rollouts while counting DENIED; no content printed, recomputed excluding them; reported numbers train/val only. No test information used for any model decision.

## 2026-11-07 — Argus-V1 structure CV (train only, October labelled + November scenario data)
- Script `experiments/risk_loop/argus_v1_structure_cv.py`; output `argus-v1/structure-cv/`; monitor `argus-v1/monitor/structure-cv.md`. CPU, 120 s. No validation/test.
- Data: 10,817 train steps / 110 positives (November 9,811/55; October 1,006/55 after dropping 33 report/stop/submit positives, option 가); 41 families.
- Baselines: length+step strat 0.569; tool-identity rate strat 0.782; domain-tool 0.788; source indicator (oct) 0.706.
- Grid (max pool, L x PCA x C, 18) pooled AUROC 0.63–0.88; per-source AUROC nov 0.61–0.86, oct 0.65–0.73; within-same-tool 0.45–0.55. C has no effect. Structural: mean pool within-tool 0.576 (best), max+mean 0.550, tool-removed 0.474 (nov 0.534).
- **Label shuffle FAIL: 0.748 / 0.771 / 0.798** (expected ≈0.5) — source/family base-rate shortcut (October positive rate 5.5% vs November 0.56%, source trivially decodable). Pooled scores invalid; only per-source scores meaningful.
- Pre-registered rule picked L23-PCA20-C1 (within-tool 0.548 vs 0.545 runner-up = noise); G0 threshold non-discriminative (all 21 pass). **Verdict: negative / PROVISIONAL — no structure fixed.** Argus-V1 default stays L16/PCA-10 until human labels. Monitor: rules 1–5 PASS, 6 FAIL (shuffle), 7 WARN.
- Fixes for next run: per-source scoring and shuffle within source x family; scaler fit inside folds for length+step baseline; within-tool AUROC per fold.
