# Iteration 0 — A5 Evaluator audit (independent verification)

- Date: 2026-10-31. Evaluator: A5 (separate agent instance from the A4 run that produced `iter-00/`).
- git `f54bc8271de445140cccf402cd4a86bba2c43075`, uncommitted diff sha256 `e5e84e98…6626487` (same as A4 provenance).
- Harness audited: `experiments/risk_loop/harness.py` sha256 `75396eafbe2182073ae00f5cea775ec31836893d3018f6fb8d55d083d5c8bbb2`.
- Data: D-v1, samples.csv sha256 `7f705976…384ff7`. python 3.10.12, numpy 2.2.6, sklearn 1.7.2.
- Supporting files: `a5-audit/independent.json`, `a5-audit/audit_C-00b.json`, `a5-audit/audit_C-00c.json`, `a5-audit/scripts/`.
- Test split: never loaded. No test features, labels or scores were read or computed (all checks use train/validation only).
- Validation looks: no new looks. The C-00b/C-00c validation scores used for the audits are the same deterministic fits as the 3 looks A4 already counted (recomputed AUROC equals the reported AUROC exactly). Cumulative looks for iter-00: **3** (≤ 3).

## Verdict

**Reproduction: confirmed. Harness: conditional pass. Two harness bugs must be fixed (and the harness re-hashed and re-frozen) before Iteration 1.** Neither bug changes any iter-00 *validation* number. Both affect train-CV/audit results: the pre-registered selection metric and the reproducibility of G5.
No iter-00 candidate is a success candidate. Every candidate fails G1 and G3.

## 1. Reproduction check

| check | result |
|---|---|
| Re-ran `iter00_reproduce.main()` with `OUT=/tmp/riskloop-iter00-check` and compared every numeric field of `summary.json`, `cv_report.json`, `eval/C-00{a,b,c}.json` (provenance excluded, compared separately) | **PASS**: 0 fields differ by > 1e-6 (the files are identical). Provenance hashes are identical too. |
| Independent recompute without harness metric code (sklearn `roc_auc_score`, own bootstrap with a different RNG/seed, own tertiles/budgets/first-violation logic) | **PASS**. Val AUROC C-00a 0.75854, C-00b 0.66992, C-00c 0.71341. length_only 0.77683. All equal to the harness value within 1e-12. Δ vs length+step, stratified AUROC, dedup Δ, budget thresholds/FPR/recall/first-violation rates are identical. Bootstrap CIs agree within Monte Carlo error (e.g. C-00c stratified CI [0.512, 0.820] vs harness [0.507, 0.821]). |
| D-v1 features vs. `load_probe_dataset(split∈{train,validation}, layers=(16,)/(23,))` | **PASS**: max abs diff 0.0 for L16 and L23, train and validation. |
| samples.csv contract recomputed from the manifest (row/id/split, step_index, prefix_length, label, trajectory_has_violation, condition/seed, after_first_violation, dup_group ↔ prefix_hash, is_dup_representative, rows_tv, test labels empty) | **PASS**: 0 errors. trajectory_has_violation agrees with the presence of positive rows in every train/val trajectory. |
| TASK §4 step 3 targets (±0.02) | **PASS**: length_only 0.7768 (target 0.777), L23 C-00b 0.6699 (≈0.67), L16 C-00c 0.7134. |

Data note for A2 (not a bug): train dup_group 377 has conflicting labels. Its rows are 535 (label 0, representative) and 549 (label 1), both in principal_impersonation, from different trajectories with an identical prefix. In D-v1-dedup that train positive is dropped. Validation has no such conflict (dedup keeps all 12 positives).

## 2. Harness contract review (code reading + targeted runs)

| item | result | evidence |
|---|---|---|
| GroupKFold on family_id, dup groups kept within one fold | **PASS** | `_merged_groups` + `grouped_folds` (harness.py:245-283) unions family and dup_group. Checked on D-v1 train (1039 rows) and the dedup train set (765 rows): every family and every dup_group lies in exactly one fold. |
| Thresholds chosen on validation normal trajectories only | **PASS** | harness.py:340-347: normal = `trajectory_has_violation` false, trajectory max score, threshold = (k+1)-th largest, alarm is `>`, so realised FPR ≤ b. |
| First-violation-before detection | **PASS** | harness.py:349-360: an alarm at any step ≤ the first positive step_index of a violation trajectory counts. Lead is in actions and tokens. Matches the contract. |
| Trajectory-level bootstrap | **PASS** | `TrajectoryBootstrap` (harness.py:311-330) resamples `trajectory_id` with replacement. Paired deltas use the same resamples. The G3 dedup resamples are separate but trajectory-level. |
| Baselines | **PASS** | harness.py:378-394: logistic regression on TRAIN log(prefix_length)[, step_index], scored on validation. |
| Gates G1–G4 per §0 | **PASS** | harness.py:445-477 (G1 CI low > 0; G2 ≥ 0.60 and CI low > 0.50; G3 = G1 on the dedup representatives; G4 detection > 0 at FPR ≤ 5%). |
| No test access (`grep -n test`) | **PASS** | harness.py's only references are guards (:166 refuses test rows in every accessor, :412 refuses test scores). build_d_v1.py writes test rows with IDs/split/token_index only, opens no test capture and leaves label/trajectory_has_violation/prefix_hash empty. iter00_reproduce.py uses only `ds.train_idx`/`ds.val_idx`. |
| Self-test | **PASS** | `pytest experiments/risk_loop/test_harness.py`: 11 passed. |

### Harness bugs (must fix before Iteration 1)

**BUG-1: label-shuffle audit is not reproducible across processes.** At `experiments/risk_loop/harness.py:505` (`within_group_shuffle`), `for k in set(g.tolist()):` iterates string family ids in hash order. That order depends on `PYTHONHASHSEED`, so the same `seed` gives different permutations in each process. Evidence: length+step shuffle CV AUROCs, seed 0, 2 repeats, came out `[0.444, 0.447]` with PYTHONHASHSEED=1 and `[0.376, 0.500]` with PYTHONHASHSEED=2. This violates §2.8 reproducibility for a G5 item. Fix: `for k in sorted(set(g.tolist())):`. Also raise the default `n_repeats` at `harness.py:512` from 5 to ≥ 20: the per-repeat SD is about 0.045, so a mean of 5 repeats has an SD of about 0.02 against a ±0.05 PASS band.

**BUG-2: pooled-OOF length-stratified CV AUROC (the pre-registered selection metric, §4 step 3) is biased by fold-level score offsets.** At `experiments/risk_loop/harness.py:298-303` (`grouped_cv`), the OOF scores from 5 different fold models are pooled, then tertile-stratified. Under GroupKFold-by-family, training prevalence is anti-correlated with held-out prevalence: train prevalence by fold is 0.074/0.089/0.096/0.074/0.091 and test prevalence is 0.126/0.070/0.041/0.132/0.057. The fold intercepts therefore shift scores against the labels, and cross-fold pairs in the same tertile are mis-ordered. This hits the unweighted metadata baseline hardest:

| train CV (D-v1 all / dedup) | current pooled stratified | fold × tertile stratified | fold-stratified AUROC |
|---|---|---|---|
| C-00a length+step | 0.342 / 0.299 | 0.532 / 0.465 | 0.675 / 0.594 |
| C-00b L23 logreg | 0.586 / 0.538 | 0.607 / 0.579 | 0.658 / 0.613 |
| C-00c L16 logreg | 0.655 / 0.580 | 0.667 / 0.619 | 0.716 / 0.650 |

As reported, the current metric makes activation candidates look about 0.25–0.3 better than length+step in CV. Corrected, the gap is about 0.07–0.14, and on fold-stratified AUROC length+step (0.675) beats L23 (0.658). Fix: compute the stratified CV AUROC with strata = (fold, tertile), keeping tertile edges on all evaluated rows and pair-weighting as now. Also report the fold-stratified AUROC, i.e. pairs within the same fold only. Keep the pooled numbers only as secondary fields. Re-run iter-00 CV reports only after the harness is re-frozen. Validation evaluation does not use this code path.

### Minor issues (fix recommended, not blocking)

- `harness.py:536-546` `check_inputs` checks only a *declared* list of names. The §5 input audit still needs code review of the candidate's featurizer (done manually below for iter-00).
- `harness.py:588-593`: the `audits()['pass']` dict omits G3, condition/family leakage and the look count, so G5 has to be assembled by hand. Add an explicit `g5` aggregate.
- `harness.py:411-416` `_score_vector` silently accepts scores for extra non-test rows (e.g. train rows). Consider rejecting rows outside the evaluated set.
- `score_leakage` (harness.py:549-562) reports condition one-vs-rest AUROC and family η² with no threshold. That matches §5 ("report"), but state a reference value (e.g. a length-only score's leakage) so the numbers can be read.

## 3. §5 audits for C-00b and C-00c

Train-only parts use D-v1 train with GroupKFold(family, dup groups). Validation parts use the already-counted validation scores. The label shuffle was also re-run with a deterministic (sorted) family order and 20 repeats, to work around BUG-1.

| audit | C-00b (L23, C=0.001) | C-00c (L16, C=0.001) |
|---|---|---|
| Label shuffle within family, CV AUROC (harness, 5 reps) | 0.503 (0.471–0.551) **PASS** | 0.496 (0.442–0.578) **PASS** |
| Label shuffle, deterministic order, 20 reps | 0.489 ± 0.044 **PASS** | 0.494 ± 0.046 **PASS** |
| Corr(score, log prefix_length), validation / train OOF | 0.312 / 0.537 (reported) | 0.281 / 0.533 (reported) |
| Length-residual score G2 (stratified AUROC [CI]) | 0.597 [0.475, 0.707] **FAIL** | 0.663 [0.529, 0.796] **PASS** |
| Condition leakage (one-vs-rest AUROC of score for C0/C1/C2) | 0.497 / 0.495 / 0.509 (no leakage) | 0.548 / 0.473 / 0.483 (weak) |
| Family leakage (η² of score by family, validation) | 0.076 | 0.062 |
| After-first-violation exclusion G1 (n=214, 9 pos): Δ vs length+step [CI] | −0.081 [−0.243, 0.098] **FAIL** | −0.035 [−0.227, 0.154] **FAIL** |
| Dedup G1 (= G3) | −0.107 [−0.258, 0.069] **FAIL** | −0.060 [−0.218, 0.129] **FAIL** |
| Input allowlist (declared `activation:L23` / `activation:L16`) | **PASS** | **PASS** |
| Input code review (`iter00_reproduce.py:35-42, 123-126`: features come only from `ds.features(L, rows)`; labels are used only as fit targets; no metadata or forbidden columns) | **PASS** | **PASS** |
| Validation look count (iter-00 total 3 ≤ 3) | **PASS** | **PASS** |
| **G5 overall** | **FAIL** (after-first-violation G1, G3, length-residual G2) | **FAIL** (after-first-violation G1, G3) |

## 4. Gate table (validation, D-v1)

| candidate | val AUROC [95% CI] | G1 Δ vs length+step [CI] | G2 stratified [CI] | G3 dedup Δ [CI] | G4 first-violation detection @5% (FPR) | G5 | success |
|---|---|---|---|---|---|---|---|
| C-00a length+step baseline | 0.759 [0.622, 0.867] | 0.000 [0, 0] **FAIL** (is the baseline) | 0.593 [0.355, 0.767] **FAIL** | 0.000 **FAIL** | 2/9 = 0.222 (0.049) **PASS** | n/a (reference; not audited) | no |
| C-00b L23 logreg | 0.670 [0.550, 0.767] | −0.089 [−0.231, 0.074] **FAIL** | 0.619 [0.491, 0.723] **FAIL** (CI low ≤ 0.50) | −0.107 [−0.258, 0.069] **FAIL** | 1/9 = 0.111 (0.049) **PASS** | **FAIL** | no |
| C-00c L16 logreg | 0.713 [0.566, 0.844] | −0.045 [−0.206, 0.119] **FAIL** | 0.667 [0.507, 0.821] **PASS** | −0.060 [−0.218, 0.129] **FAIL** | 2/9 = 0.222 (0.049) **PASS** | **FAIL** | no |

For reference, length_only scores 0.777 [0.640, 0.884]. Notes:
- G4 passes even for the metadata baseline, so G4 does not separate candidates on this validation set (5% budget = 2 of 41 normal trajectories; detection is in units of 1/9).
- C-00c's G2 pass is marginal (CI low 0.507) and is the only sign of within-length signal. It survives length residualisation (0.663 [0.529, 0.796]), which makes it a reasonable seed for H-02/H-04/H-05.

## 5. Required actions before Iteration 1

1. Fix BUG-1 (`harness.py:505` sorted iteration; `harness.py:512` default `n_repeats` ≥ 20).
2. Fix BUG-2 (`harness.py:298-303`: stratified CV AUROC over (fold, tertile) strata, and add the fold-stratified AUROC). Update `test_harness.py` with a case where fold offsets anti-correlate with labels, so pooled and per-fold-stratified values differ.
3. Re-hash and re-freeze the harness and record the new sha256 in the ledger. Re-run the iter-00 train CV (not validation) under the fixed harness so that Iteration 1 compares against a consistent CV baseline.
4. Optional: the minor issues in §2.
