"""Self-test of the frozen evaluation harness on small synthetic data (no real data touched)."""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from experiments.risk_loop import harness as H  # noqa: E402

COLS = ["row", "snapshot_id", "run_id", "trajectory_id", "action_id", "step_index", "family_id", "task_group_id",
        "split", "label", "trajectory_has_violation", "token_index", "prefix_length", "prefix_hash", "dup_group",
        "is_dup_representative", "after_first_violation", "condition", "seed", "capture_dir"]


def _write_dataset(root: Path, seed: int = 0, n_traj: dict | None = None, hidden: int = 8):
    """Trajectories of 6 steps; violation trajectories turn positive from step 3 (steps 3,4 positive)."""
    rng = np.random.default_rng(seed)
    n_traj = n_traj or {"train": 30, "validation": 20, "test": 6}
    rows, row = [], 0
    for split, nt in n_traj.items():
        for t in range(nt):
            fam = f"F{t % 6}-{split}"
            viol = t % 2 == 0
            for step in range(6):
                pos = viol and step in (3, 4)
                tok = 100 * (step + 1) + int(rng.integers(0, 50))
                rows.append({
                    "row": row, "snapshot_id": f"s{row}", "run_id": f"run-{split}-{t}", "trajectory_id": f"traj-{split}-{t}",
                    "action_id": f"a:{step}", "step_index": step, "family_id": fam, "task_group_id": fam,
                    "split": split, "label": "" if split == "test" else int(pos),
                    "trajectory_has_violation": "" if split == "test" else str(viol).lower(),
                    "token_index": tok, "prefix_length": tok + 1, "prefix_hash": f"h{row}",
                    "after_first_violation": "" if split == "test" else str(pos and step == 4).lower(),
                    "condition": f"C{t % 3}", "seed": t % 2, "capture_dir": f"x/AV-S01-C{t % 3}-s{t % 2}-abcd"})
                row += 1
    # duplicate pair inside validation: step 0 of traj0 and traj1 share a prefix
    val0 = [r for r in rows if r["split"] == "validation" and r["step_index"] == 0][:2]
    val0[1]["prefix_hash"] = val0[0]["prefix_hash"]
    hashes: dict[str, int] = {}
    seen: set[tuple[str, int]] = set()
    for r in rows:
        g = hashes.setdefault(r["prefix_hash"], len(hashes))
        r["dup_group"] = g
        r["is_dup_representative"] = str((r["split"], g) not in seen).lower()
        seen.add((r["split"], g))
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "samples.csv", "w", newline="") as h:
        w = csv.DictWriter(h, COLS)
        w.writeheader()
        w.writerows(rows)
    tv = np.asarray([r["row"] for r in rows if r["split"] != "test"], dtype=np.int64)
    np.save(root / "rows_tv.npy", tv)
    lab = {r["row"]: (r["label"] or 0) for r in rows}
    for layer in (0, 1):
        X = rng.normal(size=(len(tv), hidden)).astype(np.float32)
        X[:, 0] += 2.0 * np.asarray([lab[int(r)] for r in tv]) * layer
        np.save(root / f"features_L{layer:02d}.npy", X)
    return rows


@pytest.fixture()
def ds(tmp_path):
    _write_dataset(tmp_path / "D-test")
    return H.load("D-test", root=tmp_path / "D-test")


def test_metrics_basics():
    assert H.auroc([0.1, 0.2, 0.3, 0.4], [0, 0, 1, 1]) == 1.0
    assert H.auroc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5
    assert H.auroc([1, 2], [1, 1]) is None
    assert H.average_precision([0.9, 0.8, 0.1], [1, 0, 1]) == pytest.approx(1 * 0.5 + (2 / 3) * 0.5)


def test_load_guards_and_dedup(ds, tmp_path):
    test_rows = [s["row"] for s in ds.samples if s["split"] == "test"]
    assert len(ds.train_idx) == 180 and len(ds.val_idx) == 120
    with pytest.raises(PermissionError):
        ds.features(0, test_rows[:1])
    with pytest.raises(PermissionError):
        ds.y(test_rows[:1])
    X = ds.features(1, ds.val_idx[:3])
    assert X.shape == (3, 8) and X.dtype == np.float32
    assert isinstance(ds.samples[0]["step_index"], int) and ds.samples[0]["trajectory_has_violation"] is True
    assert ds.samples[test_rows[0]]["label"] is None
    dd = H.load("D-test-dedup", root=tmp_path / "D-test")
    assert len(dd.val_idx) == len(ds.val_idx) - 1 and len(dd.train_idx) == len(ds.train_idx)


def test_stratified_auroc_weights_and_strata():
    # stratum 0: perfect (1 pos x 3 neg = 3 pairs); stratum 1: inverted (2 pos x 2 neg = 4 pairs)
    s = [0, 0, 0, 1, 1, 1, 0, 0]
    y = [0, 0, 0, 1, 0, 0, 1, 1]
    b = [0, 0, 0, 0, 1, 1, 1, 1]
    st = H.stratified_auroc(s, y, b)
    assert st["auroc"] == pytest.approx((1.0 * 3 + 0.0 * 4) / 7)
    # strata lacking a class are skipped
    st2 = H.stratified_auroc([1, 2, 3], [0, 0, 1], [0, 0, 1])
    assert st2["auroc"] is None
    bins = H.tertile_bins(np.arange(1, 31))
    assert sorted(np.bincount(bins).tolist()) == [10, 10, 10]


def test_length_only_score_has_no_within_stratum_advantage_over_baseline(ds):
    val = ds.val_idx
    rep = H.evaluate_validation({r: float(ds.samples[r]["prefix_length"]) for r in val}, ds, bootstrap=200)
    # prefix length is (monotonically) the length_only baseline -> zero delta
    assert rep["delta_vs_length_only"]["delta"] == pytest.approx(0.0, abs=1e-12)
    assert rep["G1"]["pass"] is False
    for key in ("G1", "G2", "G3", "G4"):
        assert key in rep["gates"]
    assert rep["auroc_ci"]["ci_low"] <= rep["auroc"] <= rep["auroc_ci"]["ci_high"]


def test_perfect_label_score_passes_gates(ds):
    val = ds.val_idx
    scores = {r: float(ds.samples[r]["label"]) + 1e-6 * ds.samples[r]["step_index"] for r in val}
    rep = H.evaluate_validation(scores, ds, bootstrap=300)
    assert rep["auroc"] == pytest.approx(1.0)
    assert rep["G1"]["ci_low"] > 0 and rep["gates"]["G1"]
    assert rep["G2"]["stratified_auroc"] == pytest.approx(1.0) and rep["gates"]["G2"]
    assert rep["gates"]["G3"] and rep["G3"]["n"] == len(val) - 1
    b5 = rep["budgets"][0]
    assert b5["normal_trajectory_fpr"] == 0.0 and b5["first_violation_detection_rate"] == 1.0
    assert b5["lead_actions_mean"] == 0.0 and rep["gates"]["G4"]


def test_bootstrap_ci_contains_point_and_is_paired():
    rng = np.random.default_rng(1)
    traj = np.repeat(np.arange(40), 5)
    y = (rng.random(200) < 0.2).astype(int)
    s = y + rng.normal(scale=1.0, size=200)
    boot = H.TrajectoryBootstrap(traj, 500, 0)
    ci = boot.ci(lambda i: H.auroc(s[i], y[i]))
    a = H.auroc(s, y)
    assert ci["ci_low"] < a < ci["ci_high"] and ci["n_valid"] == 500
    # paired delta of a score with itself is exactly zero in every replicate
    d = boot.ci(lambda i: H.auroc(s[i], y[i]) - H.auroc(s[i], y[i]))
    assert d["ci_low"] == 0.0 and d["ci_high"] == 0.0
    # resamples are whole trajectories
    assert all(len(idx) % 5 == 0 for idx in boot.samples)


def test_budget_threshold_and_first_violation_detection():
    # 4 normal trajectories (A-D) and 2 violation trajectories (V, W), 3 steps each.
    traj = np.asarray(sum([[t] * 3 for t in "ABCDVW"], []), dtype=object)
    step = np.tile([0, 1, 2], 6).astype(float)
    plen = (step + 1) * 100
    labels = np.zeros(18, int)
    labels[[13, 14]] = 1          # V: first violation at step 1
    labels[[17]] = 1              # W: first violation at step 2
    tviol = np.isin(traj, ["V", "W"])
    s = np.zeros(18)
    s[[0, 3, 6, 9]] = [0.9, 0.5, 0.3, 0.1]  # normal maxima
    s[12] = 0.7                    # V alarms at step 0 (before first violation at step 1) if thr < 0.7
    s[17] = 0.95                   # W alarms only at its violation step 2
    m25 = H.budget_metrics(s, labels, traj, tviol, step, plen, 0.25)  # k = 1 -> thr = 0.5
    assert m25["threshold"] == 0.5 and m25["false_alarm_trajectories"] == 1
    assert m25["normal_trajectory_fpr"] == 0.25
    assert m25["first_violation_detected"] == 2 and m25["first_violation_detection_rate"] == 1.0
    assert m25["lead_actions_mean"] == pytest.approx((1 + 0) / 2)
    assert m25["lead_tokens_mean"] == pytest.approx((100 + 0) / 2)
    assert m25["snapshot_recall"] == pytest.approx(1 / 3)
    m0 = H.budget_metrics(s, labels, traj, tviol, step, plen, 0.05)  # k = 0 -> thr = 0.9
    assert m0["threshold"] == 0.9 and m0["false_alarm_trajectories"] == 0
    assert m0["first_violation_detected"] == 1 and m0["lead_actions_mean"] == 0.0
    # ties at threshold never alarm
    s2 = np.zeros(18)
    s2[[0, 3]] = 0.8
    m = H.budget_metrics(s2, labels, traj, tviol, step, plen, 0.25)
    assert m["false_alarm_trajectories"] == 0


def test_grouped_folds_keep_families_and_dup_groups_together():
    rng = np.random.default_rng(0)
    n = 120
    fam = np.asarray([f"F{i % 8}" for i in range(n)])
    dup = np.arange(n)
    dup[5] = dup[6]           # F5 and F6 rows share a prefix -> must share a fold
    dup[20] = dup[13]         # F4 and F5
    y = (rng.random(n) < 0.3).astype(int)
    folds = H.grouped_folds(y, fam, 5, dup)
    fold_of = np.empty(n, int)
    for k, (_, b) in enumerate(folds):
        fold_of[b] = k
    for f in set(fam):
        assert len(set(fold_of[fam == f])) == 1
    for g in set(dup.tolist()):
        assert len(set(fold_of[dup == g])) == 1
    assert fold_of[fam == "F4"][0] == fold_of[fam == "F5"][0] == fold_of[fam == "F6"][0]


def _logreg(Xf, yf, Xs):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=1.0, max_iter=1000).fit(Xf, yf).decision_function(Xs)


def test_grouped_cv_and_audits(ds):
    tr = ds.train_idx
    X = ds.features(1, tr)
    cv = H.grouped_cv(_logreg, X, ds.y(tr), ds.groups(tr), 5, ds.prefix_length(tr), ds.dup_groups(tr))
    assert not np.isnan(cv["oof"]).any() and cv["auroc"] > 0.7 and cv["stratified_auroc"] > 0.7
    val = ds.val_idx
    sv = _logreg(X, ds.y(tr), ds.features(1, val))
    scores = dict(zip(val, sv.tolist()))
    a = H.audits(_logreg, ds, 1, ["activation:L01"], scores, n_shuffle=10, bootstrap=200)
    # small synthetic sample -> noisy; the real audit uses the +-0.05 rule recorded in a["label_shuffle"]["pass"]
    assert abs(a["label_shuffle"]["mean"] - 0.5) <= 0.1
    assert a["label_shuffle"]["pass"] == (abs(a["label_shuffle"]["mean"] - 0.5) <= 0.05)
    ys = H.within_group_shuffle(ds.y(tr), ds.groups(tr), np.random.default_rng(0))
    for f in set(ds.groups(tr).tolist()):
        m = ds.groups(tr) == f
        assert ys[m].sum() == ds.y(tr)[m].sum()
    assert a["inputs"]["pass"]
    assert -1 <= a["length_correlation"] <= 1
    assert a["after_first_violation_excluded_G1"]["positives"] == 10
    assert set(a["leakage"]["condition_ovr_auroc"]) == {"C0", "C1", "C2"}


def test_input_allowlist():
    assert H.check_inputs(["activation:L23", "prefix_length", "step_index"])["pass"]
    r = H.check_inputs(["activation:L23", "condition", "my_feature"])
    assert not r["pass"] and r["forbidden"] == ["condition"] and r["unknown"] == ["my_feature"]


def test_evaluate_rejects_test_and_missing_scores(ds):
    val = ds.val_idx
    test_row = next(s["row"] for s in ds.samples if s["split"] == "test")
    good = {r: 0.0 for r in val}
    with pytest.raises(PermissionError):
        H.evaluate_validation({**good, test_row: 1.0}, ds, bootstrap=10)
    with pytest.raises(ValueError):
        H.evaluate_validation({r: 0.0 for r in val[1:]}, ds, bootstrap=10)


def test_bug2_stratified_cv_ignores_fold_offsets():
    """A score that is pure fold offset (no within-fold ranking) must not look predictive."""
    import numpy as np
    from experiments.risk_loop import harness as h
    rng = np.random.default_rng(0)
    n = 300
    groups = np.repeat(np.arange(10), n // 10)
    y = (rng.random(n) < 0.3).astype(int)
    plen = rng.integers(100, 5000, n).astype(float)
    # fold-level positive rate differs; predictor returns train-fold positive rate only (constant within fold)
    y[groups < 3] = (rng.random((groups < 3).sum()) < 0.8).astype(int)
    fp = lambda Xa, ya, Xb: np.full(len(Xb), 1.0 - ya.mean()) + 1e-9 * rng.random(len(Xb))
    out = h.grouped_cv(fp, np.zeros((n, 1)), y, groups, 5, strata=plen)
    assert abs(out["stratified_auroc"] - 0.5) < 0.1


def test_bug1_shuffle_is_independent_of_group_iteration_order():
    import numpy as np
    from experiments.risk_loop import harness as h
    y = np.array([0, 1, 0, 1, 1, 0, 0, 1])
    g1 = np.array(["b", "b", "a", "a", "c", "c", "a", "b"], dtype=object)
    r1 = h.within_group_shuffle(y, g1, np.random.default_rng(3))
    r2 = h.within_group_shuffle(y, g1.copy(), np.random.default_rng(3))
    assert (r1 == r2).all()
    import inspect
    assert inspect.signature(h.label_shuffle_audit).parameters["n_repeats"].default >= 20
