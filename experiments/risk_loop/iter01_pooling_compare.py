"""Iteration 1, step 3: pooling / MIL / anomaly comparison on D-v2 train only (family GroupKFold).

Targets (rows of the other violation kind are dropped, not relabelled):
  action  — tool/target violations (FS-1..3 style, v1 non-submit violations)
  report  — report_content violations (submit_result)
Feature sets per layer (all fitted inside each training fold):
  last         pre_action row
  mean_all     mean over all observation rows of the step
  max_all      per-dimension max over all rows (length-confounded on purpose, for comparison)
  mil_max_all  instance logistic on rows with bag labels, bag score = max over all rows
  mil_max_last4/top2_last4  same, restricted to the last 4 rows (fixed count, controls reasoning length)
  anomaly      Mahalanobis distance of the pre_action row to normal training rows (PCA-20, Ledoit-Wolf)
Classifiers: PCA-10 + logistic (capacity-limited, per iter-01 overfit diagnosis).
Baselines: log length, log length + step.
Validation and test are not read.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.covariance import LedoitWolf
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402

D = ROOT / "artifacts/risk-loop/datasets/D-v2"
OUT = ROOT / "artifacts/risk-loop/iter-01/pooling-compare"
K = 10


def load():
    samples = list(csv.DictReader(open(D / "samples.csv")))
    obs = list(csv.DictReader(open(D / "obs_rows.csv")))
    owner = np.array([int(o["sample"]) for o in obs])
    return samples, owner


def lr():
    return LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000)


def pca_lr_fit_predict(Xa, ya, Xb):
    sc = StandardScaler().fit(Xa)
    p = PCA(n_components=K, random_state=0).fit(sc.transform(Xa))
    m = lr().fit(p.transform(sc.transform(Xa)), ya)
    return m.decision_function(p.transform(sc.transform(Xb)))


def recall_at_fpr(scores, y, fpr=0.05):
    neg = np.sort(scores[y == 0])
    thr = neg[int(np.ceil((1 - fpr) * len(neg))) - 1]
    return float((scores[y == 1] > thr).mean())


def evaluate(name, fit_predict, X, y, fam, plen, dup, per_fold_extra=None):
    folds = h.grouped_folds(y, fam, 5, dup)
    oof = np.full(len(y), np.nan)
    fold_auc, fold_rec = [], []
    fold_id = np.empty(len(y), int)
    for i, (a, b) in enumerate(folds):
        oof[b] = fit_predict(a, b)
        fold_id[b] = i
        if 0 < y[b].sum() < len(b):
            fold_auc.append(h.auroc(oof[b], y[b]))
            fold_rec.append(recall_at_fpr(oof[b], y[b]))
    strat = h.stratified_auroc(oof, y, fold_id * 3 + h.tertile_bins(plen))["auroc"]
    resid = h.length_residual_scores(oof, plen)
    return {"name": name, "fold_mean_auroc": float(np.mean(fold_auc)), "fold_aurocs": [round(v, 3) for v in fold_auc],
            "len_strat_auroc": strat, "ap": h.average_precision(oof, y),
            "recall_at_5pct_fpr": float(np.mean(fold_rec)),
            "corr_loglen": float(np.corrcoef(oof, np.log(plen))[0, 1])}


def main() -> None:
    samples, owner = load()
    split = np.array([s["split"] for s in samples])
    kind = np.array([s["label_kind"] for s in samples])
    report = {"targets": {}}
    for target in ("action", "report"):
        keep = (split == "train") & ((kind == "") | (kind == target))
        idx = np.nonzero(keep)[0]
        y = (kind[idx] == target).astype(int)
        fam = np.array([samples[i]["family_id"] for i in idx])
        plen = np.array([float(samples[i]["prefix_length"]) for i in idx])
        step = np.array([float(samples[i]["step_index"]) for i in idx])
        dup = np.unique([samples[i]["prefix_sha256"] for i in idx], return_inverse=True)[1]
        T = {"rows": int(len(idx)), "positives": int(y.sum()), "families": int(len(set(fam))),
             "positive_families": int(len(set(fam[y == 1]))), "results": {}}
        base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
        T["results"]["baseline"] = [
            evaluate("log_length", lambda a, b: np.log(plen)[b], None, y, fam, plen, dup),
            evaluate("length+step", lambda a, b: lr().fit(base[a], y[a]).decision_function(base[b]), None, y, fam, plen, dup)]
        # row index lists per sample
        pos_in_idx = {s: j for j, s in enumerate(idx)}
        rows_of = [[] for _ in idx]
        for r, s in enumerate(owner):
            j = pos_in_idx.get(s)
            if j is not None:
                rows_of[j].append(r)
        last = np.array([r[-1] for r in rows_of])
        for layer in (11, 16, 23):
            A = np.load(D / f"obs_L{layer:02d}.npy", mmap_mode="r")
            need = sorted({r for rs in rows_of for r in rs})
            remap = {r: k for k, r in enumerate(need)}
            R = np.asarray(A[need], dtype=np.float32)
            rows_local = [[remap[r] for r in rs] for rs in rows_of]
            Xlast = R[[rs[-1] for rs in rows_local]]
            Xmean = np.stack([R[rs].mean(0) for rs in rows_local])
            Xmax = np.stack([R[rs].max(0) for rs in rows_local])
            res = []
            for nm, X in (("last", Xlast), ("mean_all", Xmean), ("max_all", Xmax)):
                res.append(evaluate(nm, lambda a, b, X=X: pca_lr_fit_predict(X[a], y[a], X[b]), None, y, fam, plen, dup))

            def mil(a, b, rows_sel, pool):
                ra = [r for j in a for r in rows_sel[j]]
                ya = np.concatenate([[y[j]] * len(rows_sel[j]) for j in a])
                sc = StandardScaler().fit(R[ra])
                p = PCA(n_components=K, random_state=0).fit(sc.transform(R[ra]))
                m = lr().fit(p.transform(sc.transform(R[ra])), ya)
                out = []
                for j in b:
                    s = m.decision_function(p.transform(sc.transform(R[rows_sel[j]])))
                    out.append(pool(s))
                return np.array(out)

            last4 = [rs[-4:] for rs in rows_local]
            res.append(evaluate("mil_max_all", lambda a, b: mil(a, b, rows_local, np.max), None, y, fam, plen, dup))
            res.append(evaluate("mil_max_last4", lambda a, b: mil(a, b, last4, np.max), None, y, fam, plen, dup))
            res.append(evaluate("mil_top2mean_last4", lambda a, b: mil(a, b, last4, lambda s: np.sort(s)[-2:].mean()),
                                None, y, fam, plen, dup))
            res.append(evaluate("mil_mean_last4", lambda a, b: mil(a, b, last4, np.mean), None, y, fam, plen, dup))

            def anomaly(a, b, X=Xlast):
                na = a[y[a] == 0]
                sc = StandardScaler().fit(X[na])
                p = PCA(n_components=20, random_state=0).fit(sc.transform(X[na]))
                Za = p.transform(sc.transform(X[na]))
                cov = LedoitWolf().fit(Za)
                return cov.mahalanobis(p.transform(sc.transform(X[b])))

            res.append(evaluate("anomaly_mahalanobis_last", anomaly, None, y, fam, plen, dup))
            T["results"][f"L{layer}"] = res
            print(target, layer, json.dumps([(r["name"], round(r["fold_mean_auroc"], 3), round(r["len_strat_auroc"], 3),
                                               round(r["recall_at_5pct_fpr"], 3)) for r in res]), flush=True)
        report["targets"][target] = T
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({t: {k: v for k, v in T.items() if k != "results"} | {"baseline": [(r["name"], round(r["fold_mean_auroc"], 3), round(r["len_strat_auroc"], 3)) for r in T["results"]["baseline"]]}
                      for t, T in report["targets"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
