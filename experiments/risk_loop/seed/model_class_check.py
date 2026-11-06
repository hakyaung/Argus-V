"""Is the linear model the bottleneck? Same reviewed train/validation samples, linear vs nonlinear classifiers.

Protocol: (A) GroupKFold(5) by task family inside TRAIN only (out-of-fold AUROC/AP);
(B) fit on all TRAIN, score VALIDATION (AUROC/AP + trajectory-bootstrap 95% CI). Test is never loaded.
Variants: all snapshots vs. one row per identical prefix (duplicates removed within each split).
"""

import json
import sys
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler, StandardScaler
from sklearn.svm import SVC

sys.path.insert(0, "src")
from argus_v.probes import load_probe_dataset  # noqa: E402

warnings.filterwarnings("ignore")
RUN = Path("artifacts/probe-reviewed-20261022T123507Z-237dc0/dataset/manifest.json")
LAYER = int(sys.argv[1]) if len(sys.argv) > 1 else 23


def clip(x):
    return np.clip(x, -5, 5)


def models():
    from sklearn.preprocessing import FunctionTransformer
    clipper = FunctionTransformer(clip)
    return {
        "linear: logistic L2 C=0.001": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.001, max_iter=5000, class_weight="balanced")),
        "linear: logistic L2 C=0.01": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.01, max_iter=5000, class_weight="balanced")),
        "linear: logistic L2 C=0.1": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=5000, class_weight="balanced")),
        "linear: robust+clip logistic C=0.01": lambda: make_pipeline(RobustScaler(), clipper, LogisticRegression(C=0.01, max_iter=5000, class_weight="balanced")),
        "linear: PCA64 + logistic": lambda: make_pipeline(StandardScaler(), PCA(64, random_state=0), LogisticRegression(C=0.1, max_iter=5000, class_weight="balanced")),
        "linear: shrinkage LDA": lambda: make_pipeline(StandardScaler(), LinearDiscriminantAnalysis(solver="lsqr", shrinkage="auto")),
        "nonlinear: kNN k=15 (PCA64)": lambda: make_pipeline(StandardScaler(), PCA(64, random_state=0), KNeighborsClassifier(15, weights="distance")),
        "nonlinear: random forest": lambda: RandomForestClassifier(500, min_samples_leaf=3, class_weight="balanced_subsample", n_jobs=-1, random_state=0),
        "nonlinear: grad boosting (PCA64)": lambda: make_pipeline(StandardScaler(), PCA(64, random_state=0), HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, class_weight="balanced", random_state=0)),
        "nonlinear: RBF SVM (PCA64)": lambda: make_pipeline(StandardScaler(), PCA(64, random_state=0), SVC(C=1.0, gamma="scale", class_weight="balanced")),
        "nonlinear: MLP 256-64": lambda: make_pipeline(StandardScaler(), MLPClassifier((256, 64), alpha=1e-2, early_stopping=True, max_iter=500, random_state=0)),
    }


def score(model, x):
    if hasattr(model, "decision_function"):
        return model.decision_function(x)
    return model.predict_proba(x)[:, 1]


def boot_ci(s, y, traj, reps=300, seed=0):
    rng = np.random.default_rng(seed)
    ids = np.unique(traj)
    index = {t: np.flatnonzero(traj == t) for t in ids}
    vals = []
    for _ in range(reps):
        pick = np.concatenate([index[t] for t in rng.choice(ids, len(ids))])
        if 0 < y[pick].sum() < len(pick):
            vals.append(roc_auc_score(y[pick], s[pick]))
    return np.percentile(vals, [2.5, 97.5])


def dedup(ds):
    keep, seen = [], {}
    conflicts = 0
    for i, snap in enumerate(ds.snapshots):
        h = snap.capture_prefix_ids_sha256
        if h in seen:
            conflicts += ds.labels[seen[h]] != ds.labels[i]
            continue
        seen[h] = i
        keep.append(i)
    return np.array(keep), int(conflicts)


def main():
    t0 = time.time()
    train = load_probe_dataset(RUN, split="train", layers=(LAYER,))
    val = load_probe_dataset(RUN, split="validation", layers=(LAYER,))
    print(f"layer {LAYER}: loaded in {time.time() - t0:.0f}s")
    out = {"layer": LAYER, "variants": {}}
    for variant in ("all snapshots", "duplicates removed"):
        ti = np.arange(len(train.labels)) if variant == "all snapshots" else dedup(train)[0]
        vi = np.arange(len(val.labels)) if variant == "all snapshots" else dedup(val)[0]
        xt, yt = train.features[ti], train.labels[ti].astype(int)
        xv, yv = val.features[vi], val.labels[vi].astype(int)
        groups = np.array([train.records[i]["family_id"] for i in ti])
        vtraj = np.array([val.records[i]["trajectory_id"] for i in vi])
        print(f"\n== {variant}: train {len(yt)} (viol {yt.sum()}, families {len(set(groups))}), validation {len(yv)} (viol {yv.sum()})"
              + (f", label conflicts among duplicates: train {dedup(train)[1]}, val {dedup(val)[1]}" if variant != "all snapshots" else ""))
        rows = []
        # trivial baselines (no activation)
        for name, getter in (("baseline: prefix length", lambda ds, i: ds.snapshots[i].token_index),
                             ("baseline: step index", lambda ds, i: int(ds.records[i]["action_id"].rsplit(":", 1)[1]))):
            st = np.array([getter(train, i) for i in ti], float)
            sv = np.array([getter(val, i) for i in vi], float)
            lo, hi = boot_ci(sv, yv, vtraj)
            rows.append({"model": name, "train_cv_auroc": roc_auc_score(yt, st), "train_cv_ap": average_precision_score(yt, st),
                         "val_auroc": roc_auc_score(yv, sv), "val_ap": average_precision_score(yv, sv), "val_ci": [lo, hi], "train_fit_auroc": None})
        for name, factory in models().items():
            oof = np.zeros(len(yt))
            for fit_idx, hold_idx in GroupKFold(5).split(xt, yt, groups):
                m = factory().fit(xt[fit_idx], yt[fit_idx])
                oof[hold_idx] = score(m, xt[hold_idx])
            full = factory().fit(xt, yt)
            sv = score(full, xv)
            lo, hi = boot_ci(sv, yv, vtraj)
            rows.append({"model": name, "train_fit_auroc": roc_auc_score(yt, score(full, xt)),
                         "train_cv_auroc": roc_auc_score(yt, oof), "train_cv_ap": average_precision_score(yt, oof),
                         "val_auroc": roc_auc_score(yv, sv), "val_ap": average_precision_score(yv, sv), "val_ci": [lo, hi]})
        print(f"{'model':<38}{'train fit':>10}{'train CV':>10}{'CV AP':>8}{'val AUROC':>11}{'95% CI':>15}{'val AP':>8}")
        for r in rows:
            fit = "—" if r["train_fit_auroc"] is None else f"{r['train_fit_auroc']:.3f}"
            print(f"{r['model']:<38}{fit:>10}{r['train_cv_auroc']:>10.3f}{r['train_cv_ap']:>8.3f}{r['val_auroc']:>11.3f}"
                  f"  [{r['val_ci'][0]:.2f},{r['val_ci'][1]:.2f}]{r['val_ap']:>8.3f}")
        out["variants"][variant] = {"train_n": int(len(yt)), "train_pos": int(yt.sum()), "val_n": int(len(yv)), "val_pos": int(yv.sum()),
                                    "prevalence_train": float(yt.mean()), "prevalence_val": float(yv.mean()), "rows": rows}
    Path(f"/tmp/model_class_L{LAYER}.json").write_text(json.dumps(out, indent=1, default=float))
    print(f"\ndone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
