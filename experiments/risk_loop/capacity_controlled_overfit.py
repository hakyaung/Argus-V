"""Capacity-controlled overfit: real-label vs shuffled-label train fit as model capacity grows (D-v1-dedup train only).

With 765 rows and 2,880 dims any labelling is separable, so raw overfit says nothing. Restricting capacity
(PCA-k + logistic, or L1 with few dims) makes the real-minus-shuffled train AUROC gap a measure of
how much label information the activation actually holds. Also reports what the top PCs encode.
"""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h

ds = h.load("D-v1-dedup"); rows = list(ds.train_idx)
y = ds.y(rows); fam = ds.groups(rows); dup = ds.dup_groups(rows)
plen = ds.prefix_length(rows); loglen = np.log(plen); step = ds.step_index(rows)
task = ds.column("task_group_id", rows)
out = {}
def fit_auc(X, yy, C=1.0, l1=False):
    m = LogisticRegression(C=C, class_weight="balanced", max_iter=5000,
                           penalty="l1" if l1 else "l2", solver="liblinear" if l1 else "lbfgs").fit(X, yy)
    return h.auroc(m.decision_function(X), yy), m
for layer in (11, 16, 23):
    X = StandardScaler().fit_transform(ds.features(layer, rows).astype(np.float64))
    R = {}
    pca = PCA(n_components=100, random_state=0).fit(X); Z = pca.transform(X)
    R["top_pc_meaning"] = [{"pc": i, "corr_loglen": round(float(np.corrcoef(Z[:, i], loglen)[0, 1]), 2),
                            "corr_step": round(float(np.corrcoef(Z[:, i], step)[0, 1]), 2),
                            "corr_label": round(float(np.corrcoef(Z[:, i], y)[0, 1]), 2)} for i in range(6)]
    for k in (2, 5, 10, 20, 50, 100):
        real, _ = fit_auc(Z[:, :k], y)
        sh = [fit_auc(Z[:, :k], h.within_group_shuffle(y, fam, np.random.default_rng(s)))[0] for s in range(20)]
        fp = lambda Xa, ya, Xb, k=k: (lambda p: LogisticRegression(class_weight="balanced", max_iter=5000)
              .fit(p.transform(Xa)[:, :k], ya).decision_function(p.transform(Xb)[:, :k]))(PCA(n_components=k, random_state=0).fit(Xa))
        cv = h.grouped_cv(fp, X, y, fam, 5, strata=plen, dup_groups=dup)
        R[f"pca{k}"] = {"train_real": round(real, 3), "train_shuffled_mean": round(float(np.mean(sh)), 3),
                        "train_shuffled_p95": round(float(np.quantile(sh, .95)), 3),
                        "cv": round(cv["auroc"], 3), "cv_len_strat": round(cv["stratified_auroc"], 3)}
    for C in (0.002, 0.005, 0.01):
        real, m = fit_auc(X, y, C, l1=True); nz = int((m.coef_ != 0).sum())
        sh = [fit_auc(X, h.within_group_shuffle(y, fam, np.random.default_rng(s)), C, l1=True)[0] for s in range(10)]
        R[f"l1 C={C}"] = {"nonzero_dims": nz, "train_real": round(real, 3), "train_shuffled_mean": round(float(np.mean(sh)), 3)}
    out[f"L{layer}"] = R; print(layer, json.dumps(R, ensure_ascii=False), flush=True)
dest = ROOT / "artifacts/risk-loop/iter-01/overfit-diagnosis/capacity_controlled.json"
dest.write_text(json.dumps(out, indent=1, ensure_ascii=False))
