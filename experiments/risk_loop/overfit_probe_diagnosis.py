"""Overfit-then-inspect: what does an (almost) unregularised probe use to separate train positives?

Train split of D-v1-dedup only (validation/test untouched). For L16 and L23 pre_action rows:
  1. memorisation capacity: weak-reg fit train AUROC with real vs within-family shuffled labels
  2. what the overfit score tracks: corr with log length / step, share of score variance explained by task
  3. weight direction vs length direction (cosine, standardized space)
  4. refit after projecting out the length direction (+ grouped CV for reference)
  5. top-|weight| dims vs outlier (massive-activation) dims
  6. nearest neighbour of each positive: same task? same label? same prefix?
  .venv/bin/python experiments/risk_loop/overfit_probe_diagnosis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/iter-01/overfit-diagnosis"


def fit_scores(X, y, C):
    m = LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
    m.fit(X, y)
    return m, m.decision_function(X)


def eta2(score, groups):
    grand = score.mean()
    ss_t = ((score - grand) ** 2).sum()
    ss_b = sum((groups == g).sum() * (score[groups == g].mean() - grand) ** 2 for g in np.unique(groups))
    return float(ss_b / ss_t)


def main() -> None:
    ds = h.load("D-v1-dedup")
    rows = list(ds.train_idx)
    y = ds.y(rows)
    fam = ds.groups(rows)
    task = ds.column("task_group_id", rows)
    plen = ds.prefix_length(rows).astype(float)
    step = ds.step_index(rows).astype(float)
    dup = ds.dup_groups(rows)
    after = ds.column("after_first_violation", rows)
    loglen = np.log(plen)
    rng = np.random.default_rng(0)
    report = {"rows": len(rows), "positives": int(y.sum()), "families": int(len(np.unique(fam))),
              "tasks": int(len(np.unique(task))), "layers": {}}
    lr_cv = lambda C: (lambda Xa, ya, Xb: LogisticRegression(C=C, class_weight="balanced", max_iter=5000)
                       .fit(Xa, ya).decision_function(Xb))
    for layer in (16, 23):
        X = StandardScaler().fit_transform(ds.features(layer, rows).astype(np.float64))
        L = {}
        # 1. memorisation
        for C in (1e2, 1e-3):
            _, s = fit_scores(X, y, C)
            ys = h.within_group_shuffle(y, fam, rng)
            _, s_sh = fit_scores(X, ys, C)
            L[f"C={C:g}"] = {"train_auroc": h.auroc(s, y), "train_auroc_shuffled_labels": h.auroc(s_sh, ys)}
        model, s = fit_scores(X, y, 1e2)
        # 2. what the overfit score tracks
        L["score_corr_loglen"] = float(np.corrcoef(s, loglen)[0, 1])
        L["score_corr_step"] = float(np.corrcoef(s, step)[0, 1])
        L["score_var_explained_by_task"] = eta2(s, task)
        L["label_var_explained_by_task"] = eta2(y.astype(float), task)
        L["positives_per_task"] = {str(k): int(v) for k, v in zip(*np.unique(task[y == 1], return_counts=True))}
        # 3. length direction
        rid = Ridge(alpha=10.0).fit(X, loglen)
        L["length_r2_in_sample"] = float(rid.score(X, loglen))
        w = model.coef_.ravel()
        d = rid.coef_ / np.linalg.norm(rid.coef_)
        L["cos_weight_vs_length_direction"] = float(w @ d / np.linalg.norm(w))
        # 4. remove length direction (train-in-sample direction; CV re-estimates per fold below)
        Xr = X - np.outer(X @ d, d)
        _, sr = fit_scores(Xr, y, 1e2)
        L["train_auroc_length_removed"] = h.auroc(sr, y)

        def lr_resid(C):
            def fp(Xa, ya, Xb, la=None):
                return LogisticRegression(C=C, class_weight="balanced", max_iter=5000).fit(Xa, ya).decision_function(Xb)
            return fp
        cv = {}
        for name, XX in (("raw", X), ("length_removed", Xr)):
            for C in (1e2, 1e-3):
                r = h.grouped_cv(lr_cv(C), XX, y, fam, 5, strata=plen, dup_groups=dup)
                cv[f"{name} C={C:g}"] = {"cv_auroc": r["auroc"], "cv_len_strat_auroc": r["stratified_auroc"]}
        base = np.column_stack([loglen, step])
        r = h.grouped_cv(lr_cv(1.0), StandardScaler().fit_transform(base), y, fam, 5, strata=plen, dup_groups=dup)
        cv["length+step"] = {"cv_auroc": r["auroc"], "cv_len_strat_auroc": r["stratified_auroc"]}
        L["grouped_cv"] = cv
        # 5. top weights vs outlier dims
        raw = ds.features(layer, rows).astype(np.float64)
        mag = np.abs(raw).mean(0)
        outlier = set(np.argsort(-mag)[:20].tolist())
        top = np.argsort(-np.abs(w))[:20].tolist()
        L["top20_weight_dims"] = top
        L["top20_weight_dims_in_top20_magnitude"] = len(outlier & set(top))
        L["top20_magnitude_dims"] = sorted(outlier)
        # 6. nearest neighbours of positives (cosine, standardized)
        Xn = X / np.linalg.norm(X, axis=1, keepdims=True)
        sim = Xn @ Xn.T
        np.fill_diagonal(sim, -np.inf)
        same_dup = dup[:, None] == dup[None, :]
        sim_nd = np.where(same_dup, -np.inf, sim)
        pos = np.nonzero(y == 1)[0]
        nn = sim_nd[pos].argmax(1)
        L["positives_nn"] = {
            "nn_same_task": float(np.mean(task[nn] == task[pos])),
            "nn_same_family": float(np.mean(fam[nn] == fam[pos])),
            "nn_is_positive": float(np.mean(y[nn] == 1)),
            "nn_positive_rate_baseline": float(y.mean()),
            "mean_cos_to_nn": float(sim_nd[pos, nn].mean()),
            "abs_loglen_gap_to_nn": float(np.abs(loglen[nn] - loglen[pos]).mean()),
            "abs_loglen_gap_random_pair": float(np.abs(loglen[rng.integers(0, len(y), 2000)] - loglen[rng.integers(0, len(y), 2000)]).mean()),
        }
        L["after_first_violation_positives"] = int(((after == True) | (after == "True") | (after == "true"))[y == 1].sum())  # noqa: E712
        report["layers"][f"L{layer}"] = L
        print(f"L{layer} done", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))
    print(json.dumps(report, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
