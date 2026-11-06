"""Argus-V1 train-only CV with source-shortcut corrections (October labelled + November scenario data). No val/test.

Follow-up to argus_v1_structure_cv.py (label shuffle FAIL 0.75-0.80: source base-rate shortcut).
Model fixed to Argus-V1 default: L16 observation rows -> max pooling -> StandardScaler -> PCA-10 -> LogReg(C=1).
Variants (all fitted inside each training fold):
  base         class_weight=balanced (as before)
  balanced     sample weights equalising every (source, class) cell -> knowing the source gives no advantage
  demeaned     per-source mean of pooled features subtracted (source signal removed)
  both         balanced + demeaned
Audits (fixed): per-source AUROC, within-same-tool AUROC computed per (fold, tool) then averaged,
  length+step baseline with scaler inside folds, label shuffle WITHIN SOURCE (3 permutations; expect ~0.5).
Reads datasets only (no writes outside artifacts/risk-loop/argus-v1/source-balanced-cv/).

  .venv/bin/python experiments/risk_loop/argus_v1_source_balanced_cv.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402
from experiments.risk_loop.argus_v1_structure_cv import load_all  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/argus-v1/source-balanced-cv"
VARIANTS = {"base": (False, False), "balanced": (True, False), "demeaned": (False, True), "both": (True, True)}


def make_fp(X, src, balance, demean, pca_k=10, C=1.0):
    def fp(Xa, ya, Xb):
        ia, ib = Xa[:, 0].astype(int), Xb[:, 0].astype(int)
        A, B = X[ia], X[ib]
        if demean:
            mu = {s: A[src[ia] == s].mean(0) for s in set(src[ia])}
            g = A.mean(0)
            A = A - np.stack([mu[s] for s in src[ia]])
            B = B - np.stack([mu.get(s, g) for s in src[ib]])
        sc = StandardScaler().fit(A)
        p = PCA(n_components=pca_k, random_state=0).fit(sc.transform(A))
        Z = p.transform(sc.transform(A))
        if balance:
            cell = np.array([f"{s}|{c}" for s, c in zip(src[ia], ya)])
            n = {k: (cell == k).sum() for k in set(cell)}
            w = np.array([1.0 / n[k] for k in cell]); w *= len(w) / w.sum()
            lr = LogisticRegression(C=C, max_iter=3000).fit(Z, ya, sample_weight=w)
        else:
            lr = LogisticRegression(C=C, class_weight="balanced", max_iter=3000).fit(Z, ya)
        return lr.decision_function(p.transform(sc.transform(B)))
    return fp


def fold_ids(y, fam, dup):
    fid = np.empty(len(y), dtype=int)
    for i, (_, b) in enumerate(h.grouped_folds(y, fam, 5, dup)):
        fid[b] = i
    return fid


def within_tool(oof, y, tool, fid):
    vals = []
    for f in set(fid):
        for t in set(tool[(fid == f) & (y == 1)]):
            m = (fid == f) & (tool == t)
            if 0 < y[m].sum() < m.sum():
                vals.append(h.auroc(oof[m], y[m]))
    return float(np.mean(vals)) if vals else None, len(vals)


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / "run.log", "w")

    def say(*a):
        line = " ".join(str(x) for x in a)
        print(line, flush=True); log.write(line + "\n"); log.flush()

    meta, F, dropped = load_all()
    X = F[(16, "max")]
    y = np.array([m["y"] for m in meta]); fam = np.array([m["family"] for m in meta])
    plen = np.array([m["plen"] for m in meta], float); step = np.array([m["step"] for m in meta], float)
    dup = np.array([m["dup"] for m in meta]); tool = np.array([m["tool"] for m in meta])
    src = np.array([m["src"] for m in meta])
    ids = np.arange(len(y), dtype=np.float64)[:, None]
    fid = fold_ids(y, fam, dup)
    say("data rows", len(y), "pos", int(y.sum()), {s: (int((src == s).sum()), int(y[src == s].sum())) for s in ("nov", "oct")})

    def lsfp(Xa, ya, Xb):
        sc = StandardScaler().fit(Xa)
        return LogisticRegression(class_weight="balanced", max_iter=3000).fit(sc.transform(Xa), ya).decision_function(sc.transform(Xb))
    ls = h.grouped_cv(lsfp, np.column_stack([np.log(plen), step]), y, fam, 5, strata=plen, dup_groups=dup)
    ls_oof = np.asarray(ls["oof"])
    base = {"length+step": {"auroc": ls["auroc"], "stratified_auroc": ls["stratified_auroc"],
                            "by_source": {s: h.auroc(ls_oof[src == s], y[src == s]) for s in ("nov", "oct")}},
            "source_indicator_auroc(oct=1)": h.auroc((src == "oct").astype(float), y)}
    say("baselines", json.dumps(base, default=float))
    res = {}
    rng = np.random.default_rng(0)
    for name, (bal, dem) in VARIANTS.items():
        fp = make_fp(X, src, bal, dem)
        r = h.grouped_cv(fp, ids, y, fam, 5, strata=plen, dup_groups=dup)
        oof = np.asarray(r["oof"])
        wt, nwt = within_tool(oof, y, tool, fid)
        shuf = []
        for _ in range(3):
            ys = y.copy()
            for s in ("nov", "oct"):
                m = np.nonzero(src == s)[0]
                ys[m] = rng.permutation(y[m])
            shuf.append(h.grouped_cv(fp, ids, ys, fam, 5, strata=plen, dup_groups=dup)["auroc"])
        e = {"auroc_pooled": r["auroc"], "stratified_auroc": r["stratified_auroc"], "ap": r["ap"],
             "auroc_by_source": {s: h.auroc(oof[src == s], y[src == s]) for s in ("nov", "oct")},
             "within_same_tool_per_fold_mean": wt, "within_same_tool_cells": nwt,
             "source_auc_of_score(oct=1)": h.auroc(oof, (src == "oct").astype(int)),
             "label_shuffle_within_source_auroc": shuf, "fold_auroc": [f["auroc"] for f in r["folds"]]}
        res[name] = e
        say(name, json.dumps(e, default=float))
    rep = {"status": "TRAIN_ONLY_SOURCE_SHORTCUT_CV", "model": "Argus-V1 default (L16, max, PCA-10, C=1)",
           "validation_touched": False, "test_touched": False, "gpu": False, "harness_sha256": h.harness_sha256(),
           "october_label_rule": "option 가 (report/stop/submit positives dropped)", "october_dropped": dropped,
           "baselines": base, "variants": res, "seconds": round(time.time() - t0, 1)}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float))
    say("done", rep["seconds"], "s")


if __name__ == "__main__":
    main()
