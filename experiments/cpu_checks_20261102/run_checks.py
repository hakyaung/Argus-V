"""CPU signal checks agreed in the 각반Agent debate (2026-11-02). Train split only; validation/test are never read.

Question: does the L16 pre_action signal (train family-GroupKFold CV) survive when
  (a) scenario-defect-attributable positives are removed,
  (b) only action-type positives are kept (report_content positives removed),
  (c) prefix length is controlled (length-tertile stratified AUROC, and length direction projected out of X),
plus the learning curve (fraction of training trajectories) that was previously computed ad hoc.

Positives that a filter removes are dropped from the data (not relabelled as 0).
Usage: .venv/bin/python experiments/cpu_checks_20261102/run_checks.py
"""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DV1 = os.path.join(ROOT, "artifacts", "risk-loop", "datasets", "D-v1")
DRAFT = os.path.join(ROOT, "artifacts", "label-typing-20261102", "violation_type_draft.json")
OUT = os.path.dirname(os.path.abspath(__file__))
SEED = 0
BOOT = 1000


def load():
    rows_by_id = {int(r["row"]): r for r in csv.DictReader(open(os.path.join(DV1, "samples.csv"), encoding="utf-8"))}
    order = np.load(os.path.join(DV1, "rows_tv.npy"))
    tv = [rows_by_id[int(i)] for i in order]
    train = np.array([r["split"] == "train" for r in tv])
    assert all(r["split"] in ("train", "validation") for r in tv)
    feats = np.load(os.path.join(DV1, "features_L16.npy"), mmap_mode="r")
    rows = [r for r, k in zip(tv, train) if k]
    X = np.asarray(feats[train], dtype=np.float32)
    return rows, X


def cv_scores(X, y, groups, meta_len, frac=1.0, rng=None, C=0.001):
    """Out-of-fold scores (family GroupKFold 5). frac<1 subsamples training trajectories per fold."""
    oof = np.full(len(y), np.nan)
    fold_id = np.full(len(y), -1)
    for f, (tr, te) in enumerate(GroupKFold(5).split(X, y, groups)):
        if frac < 1.0:
            u = np.unique(meta_len["traj"][tr])
            keep = rng.choice(u, max(2, int(len(u) * frac)), replace=False)
            tr = tr[np.isin(meta_len["traj"][tr], keep)]
        if y[tr].sum() < 2:
            continue
        clf = make_pipeline(StandardScaler(), LogisticRegression(C=C, class_weight="balanced", max_iter=3000))
        clf.fit(X[tr], y[tr])
        oof[te] = clf.decision_function(X[te])
        fold_id[te] = f
    return oof, fold_id


def fold_mean_auc(y, s, fold_id):
    vals = [roc_auc_score(y[fold_id == f], s[fold_id == f]) for f in np.unique(fold_id)
            if f >= 0 and len(set(y[fold_id == f])) == 2]
    return float(np.mean(vals)) if vals else float("nan")


def stratified_auc(y, s, fold_id, loglen):
    """(fold x length-tertile) strata, weighted by stratum size (the corrected A5 BUG-2 scheme)."""
    num = den = 0.0
    for f in np.unique(fold_id):
        m = fold_id == f
        if f < 0 or m.sum() == 0:
            continue
        cuts = np.quantile(loglen[m], [1 / 3, 2 / 3])
        tert = np.digitize(loglen[m], cuts)
        for t in range(3):
            mm = np.where(m)[0][tert == t]
            if len(set(y[mm])) == 2:
                num += roc_auc_score(y[mm], s[mm]) * len(mm)
                den += len(mm)
    return num / den if den else float("nan")


def boot_ci(y, s, fold_id, traj, fn, rng):
    """Trajectory-level bootstrap CI of a fold-based metric."""
    ut = np.unique(traj)
    idx_by = {t: np.where(traj == t)[0] for t in ut}
    vals = []
    for _ in range(BOOT):
        pick = rng.choice(ut, len(ut), replace=True)
        idx = np.concatenate([idx_by[t] for t in pick])
        v = fn(y[idx], s[idx], fold_id[idx])
        if not np.isnan(v):
            vals.append(v)
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def residualize_length(X, loglen, groups):
    """Project the linear length component out of X, fitted inside each training fold (no leakage)."""
    Xr = np.empty_like(X)
    for tr, te in GroupKFold(5).split(X, loglen, groups):
        A = np.c_[np.ones(len(tr)), loglen[tr]]
        coef, *_ = np.linalg.lstsq(A, X[tr], rcond=None)
        Xr[te] = X[te] - np.c_[np.ones(len(te)), loglen[te]] @ coef
    return Xr


def main():
    rows, X_all = load()
    draft = {e["action_id"]: e for e in json.load(open(DRAFT, encoding="utf-8"))["entries"]}
    dedup = np.array([r["is_dup_representative"] == "true" for r in rows])
    y_all = np.array([int(r["label"]) for r in rows])
    pos_ids = {r["action_id"] for r, yy in zip(rows, y_all) if yy == 1}
    missing = sorted(pos_ids - set(draft))
    if missing:
        sys.exit(f"train positives missing from draft: {missing[:5]}")

    def subset(name, drop_pos):
        keep = dedup & ~np.array([yy == 1 and drop_pos(draft[r["action_id"]]) for r, yy in zip(rows, y_all)])
        return name, keep

    subsets = [
        subset("0_all_positives", lambda e: False),
        subset("a_drop_defect_strong", lambda e: e["scenario_defect_suspected"] and e["defect_strength"] == "strong"),
        subset("a2_drop_defect_any", lambda e: e["scenario_defect_suspected"]),
        subset("b_action_type_only", lambda e: e["violation_type"] == "report_content"),
        subset("ab_action_type_no_strong_defect",
               lambda e: e["violation_type"] == "report_content"
               or (e["scenario_defect_suspected"] and e["defect_strength"] == "strong")),
    ]

    results = {"seed": SEED, "bootstrap": BOOT, "data": "D-v1 train, dedup representatives, L16 pre_action",
               "cv": "GroupKFold(5) by family_id; metric = mean per-fold AUROC; CI = trajectory bootstrap",
               "subsets": {}}
    for name, keep in subsets:
        rng = np.random.default_rng(SEED)
        d = [r for r, k in zip(rows, keep) if k]
        X = X_all[keep]
        y = y_all[keep]
        g = np.array([r["family_id"] for r in d])
        traj = np.array([r["trajectory_id"] for r in d])
        loglen = np.log(np.array([float(r["prefix_length"]) for r in d]))
        step = np.array([int(r["step_index"]) for r in d], dtype=float)
        meta = {"traj": traj}
        res = {"rows": int(len(y)), "positives": int(y.sum()), "families": int(len(set(g))),
               "families_with_pos": int(len(set(g[y == 1])))}

        s_act, fid = cv_scores(X, y, g, meta)
        s_meta, fid_m = cv_scores(np.c_[loglen, step], y, g, meta, C=1.0)
        s_res, fid_r = cv_scores(residualize_length(X, loglen, g), y, g, meta)
        fm = lambda yy, ss, ff: fold_mean_auc(yy, ss, ff)
        res["L16_auc"] = fold_mean_auc(y, s_act, fid)
        res["L16_auc_ci"] = boot_ci(y, s_act, fid, traj, fm, rng)
        res["len_step_auc"] = fold_mean_auc(y, s_meta, fid_m)
        res["delta_vs_len_step"] = res["L16_auc"] - res["len_step_auc"]
        # paired trajectory bootstrap of the difference
        ut = np.unique(traj)
        idx_by = {t: np.where(traj == t)[0] for t in ut}
        diffs = []
        for _ in range(BOOT):
            idx = np.concatenate([idx_by[t] for t in rng.choice(ut, len(ut), replace=True)])
            a = fold_mean_auc(y[idx], s_act[idx], fid[idx])
            b = fold_mean_auc(y[idx], s_meta[idx], fid_m[idx])
            if not (np.isnan(a) or np.isnan(b)):
                diffs.append(a - b)
        res["delta_ci"] = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
        res["L16_length_stratified_auc"] = stratified_auc(y, s_act, fid, loglen)
        res["len_step_length_stratified_auc"] = stratified_auc(y, s_meta, fid_m, loglen)
        res["L16_length_residualized_auc"] = fold_mean_auc(y, s_res, fid_r)
        res["L16_length_residualized_ci"] = boot_ci(y, s_res, fid_r, traj, fm, rng)
        res["corr_score_loglen"] = float(np.corrcoef(s_act[fid >= 0], loglen[fid >= 0])[0, 1])
        res["G0_like_pass"] = bool(res["L16_length_stratified_auc"] >= 0.60
                                   and res["L16_length_stratified_auc"] - res["len_step_length_stratified_auc"] >= 0.03)

        if name == "0_all_positives":
            curve = {}
            for frac in (0.25, 0.5, 0.75, 1.0):
                reps = 1 if frac == 1.0 else 6
                vals = []
                for _ in range(reps):
                    s, f = cv_scores(X, y, g, meta, frac=frac, rng=rng) if frac < 1 else (s_act, fid)
                    vals.append(fold_mean_auc(y, s, f))
                curve[str(frac)] = {"mean": float(np.mean(vals)), "std": float(np.std(vals)), "reps": reps}
            res["learning_curve_trajectory_fraction"] = curve
        results["subsets"][name] = res
        print(name, json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in res.items()
                                if not isinstance(v, (dict, list))}, ensure_ascii=False), flush=True)

    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
