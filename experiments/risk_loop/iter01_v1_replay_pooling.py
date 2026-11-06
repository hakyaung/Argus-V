"""Iteration 1, step 4 (CPU fallback): pooling comparison on v1 replay features, D-v1-dedup train only.

The v1 reasoning replays kept only summaries after compaction (compact_reasoning_replays.py deleted
block.npy and the full tensors are not on the HF raw-data repo), so a true per-token max is not available
without a GPU re-replay. What is available per action (artifacts/reasoning-replay-*/<snapshot>/pooled-features.npz):
  mean_block_L       mean over every reasoning token (reasoning_span), float64 accumulate
  last_block_L       last prefix token (= pre_action)
  reference_block_L  the original capture's observation rows (pre_reasoning, every 32 tokens?, reasoning_end, pre_action)
Feature sets: last, span_mean (all tokens), ref_mean, ref_max (dimension max over observation rows),
mil_max_ref / mil_mean_last4 (instance logistic, bag pooling). Layers 0, 11 (L00-L11 replay) and 23.
Also re-runs the iter-00 train CV with the re-frozen harness (P-03): L16/L23 pre_action C=0.001 and length+step.
Validation and test are not read.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402
from experiments.risk_loop.iter01_pooling_compare import K, lr, pca_lr_fit_predict, recall_at_fpr  # noqa: E402
from sklearn.decomposition import PCA  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/iter-01/v1-replay-pooling"
REPLAY = {0: "reasoning-replay-L00-L11", 11: "reasoning-replay-L00-L11", 23: "reasoning-replay-L23"}


def evaluate(name, fit_predict, y, fam, plen, dup):
    folds = h.grouped_folds(y, fam, 5, dup)
    oof = np.full(len(y), np.nan)
    fold_id = np.empty(len(y), int)
    fa, fr = [], []
    for i, (a, b) in enumerate(folds):
        oof[b] = fit_predict(a, b)
        fold_id[b] = i
        if 0 < y[b].sum() < len(b):
            fa.append(h.auroc(oof[b], y[b]))
            fr.append(recall_at_fpr(oof[b], y[b]))
    return {"name": name, "fold_mean_auroc": float(np.mean(fa)), "fold_aurocs": [round(v, 3) for v in fa],
            "pooled_auroc": h.auroc(oof, y),
            "len_strat_auroc": h.stratified_auroc(oof, y, fold_id * 3 + h.tertile_bins(plen))["auroc"],
            "ap": h.average_precision(oof, y), "recall_at_5pct_fpr": float(np.mean(fr)),
            "corr_loglen": float(np.corrcoef(oof, np.log(plen))[0, 1])}


def main() -> None:
    ds = h.load("D-v1-dedup")
    rows = list(ds.train_idx)
    snap = ds.column("snapshot_id", rows)
    have = np.array([(ROOT / "artifacts" / REPLAY[23] / s / "pooled-features.npz").exists()
                     and (ROOT / "artifacts" / REPLAY[0] / s / "pooled-features.npz").exists() for s in snap])
    rows = [r for r, k in zip(rows, have) if k]
    snap = ds.column("snapshot_id", rows)
    y = ds.y(rows)
    fam = ds.groups(rows)
    plen = ds.prefix_length(rows).astype(float)
    step = ds.step_index(rows).astype(float)
    dup = ds.dup_groups(rows)
    report = {"rows": len(rows), "missing_replay": int((~have).sum()), "positives": int(y.sum()),
              "families": int(len(set(fam))), "positive_families": int(len(set(fam[y == 1]))), "layers": {}}
    base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
    report["baseline"] = [evaluate("log_length", lambda a, b: np.log(plen)[b], y, fam, plen, dup),
                          evaluate("length+step", lambda a, b: lr().fit(base[a], y[a]).decision_function(base[b]),
                                   y, fam, plen, dup)]
    # P-03: iter-00 reproduction CV with the re-frozen harness
    c001 = lambda Xa, ya, Xb: LogisticRegression(C=1e-3, class_weight="balanced", max_iter=5000).fit(Xa, ya).decision_function(Xb)
    all_tr = list(ds.train_idx)
    p03 = {}
    for nm, X in (("length+step", StandardScaler().fit_transform(np.column_stack(
            [np.log(ds.prefix_length(all_tr).astype(float)), ds.step_index(all_tr).astype(float)]))),
                  ("L16 pre_action C=0.001", StandardScaler().fit_transform(ds.features(16, all_tr).astype(np.float64))),
                  ("L23 pre_action C=0.001", StandardScaler().fit_transform(ds.features(23, all_tr).astype(np.float64)))):
        fp = c001 if nm != "length+step" else (lambda Xa, ya, Xb: lr().fit(Xa, ya).decision_function(Xb))
        r = h.grouped_cv(fp, X, ds.y(all_tr), ds.groups(all_tr), 5, strata=ds.prefix_length(all_tr).astype(float),
                         dup_groups=ds.dup_groups(all_tr))
        p03[nm] = {k: r[k] for k in ("auroc", "stratified_auroc", "stratified_auroc_pooled_biased") if k in r}
    report["p03_iter00_cv_refrozen_harness"] = p03
    print("P-03", json.dumps(p03), flush=True)

    for layer in (0, 11, 23):
        key = f"block_{layer:04d}"
        last, mean, ref = [], [], []
        for s in snap:
            with np.load(ROOT / "artifacts" / REPLAY[layer] / s / "pooled-features.npz") as z:
                last.append(z[f"last_{key}"]); mean.append(z[f"mean_{key}"]); ref.append(z[f"reference_{key}"])
        Xlast, Xmean = np.stack(last).astype(np.float64), np.stack(mean).astype(np.float64)
        Xrmean = np.stack([r.mean(0) for r in ref]).astype(np.float64)
        Xrmax = np.stack([r.max(0) for r in ref]).astype(np.float64)
        nref = [len(r) for r in ref]
        R = np.concatenate(ref).astype(np.float64)
        off = np.concatenate([[0], np.cumsum(nref)])
        rows_all = [list(range(off[j], off[j + 1])) for j in range(len(ref))]
        rows_last4 = [rs[-4:] for rs in rows_all]

        def mil(a, b, sel, pool):
            ra = [r for j in a for r in sel[j]]
            ya = np.concatenate([[y[j]] * len(sel[j]) for j in a])
            sc = StandardScaler().fit(R[ra])
            p = PCA(n_components=K, random_state=0).fit(sc.transform(R[ra]))
            m = lr().fit(p.transform(sc.transform(R[ra])), ya)
            return np.array([pool(m.decision_function(p.transform(sc.transform(R[sel[j]])))) for j in b])

        res = [evaluate(nm, lambda a, b, X=X: pca_lr_fit_predict(X[a], y[a], X[b]), y, fam, plen, dup)
               for nm, X in (("last", Xlast), ("span_mean_all_tokens", Xmean), ("ref_mean", Xrmean), ("ref_max", Xrmax))]
        res.append(evaluate("mil_max_ref", lambda a, b: mil(a, b, rows_all, np.max), y, fam, plen, dup))
        res.append(evaluate("mil_mean_last4", lambda a, b: mil(a, b, rows_last4, np.mean), y, fam, plen, dup))
        report["layers"][f"L{layer}"] = {"ref_rows_per_action": [int(min(nref)), float(np.mean(nref)), int(max(nref))],
                                         "results": res}
        print(f"L{layer}", json.dumps([(r["name"], round(r["fold_mean_auroc"], 3), round(r["len_strat_auroc"], 3),
                                        round(r["recall_at_5pct_fpr"], 3)) for r in res]), flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=float))
    print(json.dumps({k: v for k, v in report.items() if k not in ("layers",)}, default=float)[:1500])


if __name__ == "__main__":
    main()
