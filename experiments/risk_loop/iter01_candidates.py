"""Iteration 1 candidates (A4). Action-violation target on D-v2a; features from D-v2 observation rows.

  C-01a  L11 mean over all observation rows of the step   -> StandardScaler + PCA-10 + LogReg(balanced)
  C-01b  L11 MIL: instance LogReg on the last 4 rows (bag label copied), bag score = mean of instance scores
  C-01c  L23 MIL: instance LogReg on all rows, bag score = max of instance scores

Inputs: activation only (observation rows of the current prefix). No length/step/condition/seed/task inputs.

Public API (used by A5; the module never touches validation by itself):
  bags(ds, rows, layer)          -> list of (n_obs_i, 2880) float32 arrays, one per D-v2a row (test rows refused by ds)
  fit(cid, ds, rows)             -> fitted model dict
  score(model, ds, rows)         -> np.ndarray of risk scores
  fit_predict_for_audit(cid, ds) -> (fit_predict, featurizer) for harness.audits / label_shuffle_audit

  .venv/bin/python experiments/risk_loop/iter01_candidates.py   # train-only: G0 CV, fit on all train, save models
"""
from __future__ import annotations

import csv
import hashlib
import json
import pickle
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402

D2 = ROOT / "artifacts/risk-loop/datasets/D-v2"
OUT = ROOT / "artifacts/risk-loop/iter-01/candidates"
K = 10
CANDIDATES = {
    "C-01a": {"layer": 11, "kind": "pooled_mean", "rows": "all", "pool": None,
              "desc": "L11 mean over all observation rows; StandardScaler + PCA-10 + LogReg(C=1, balanced)"},
    "C-01b": {"layer": 11, "kind": "mil", "rows": "last4", "pool": "mean",
              "desc": "L11 MIL on last 4 observation rows; instance StandardScaler + PCA-10 + LogReg(C=1, balanced); bag = mean"},
    "C-01c": {"layer": 23, "kind": "mil", "rows": "all", "pool": "max",
              "desc": "L23 MIL on all observation rows; instance StandardScaler + PCA-10 + LogReg(C=1, balanced); bag = max"},
}


@lru_cache(maxsize=1)
def _obs_index() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for i, o in enumerate(csv.DictReader(open(D2 / "obs_rows.csv"))):
        out.setdefault(int(o["sample"]), []).append(i)
    return out


@lru_cache(maxsize=4)
def _obs(layer: int) -> np.ndarray:
    return np.load(D2 / f"obs_L{layer:02d}.npy", mmap_mode="r")


def bags(ds: h.Dataset, rows, layer: int) -> list[np.ndarray]:
    rows = ds._check_tv(rows)
    idx = _obs_index()
    A = _obs(layer)
    return [np.asarray(A[idx[int(ds.samples[r]["d2_sample"])]], dtype=np.float32) for r in rows]


def _lr():
    return LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000)


def _sel(bag: np.ndarray, which: str) -> np.ndarray:
    return bag[-4:] if which == "last4" else bag


def _fit_bags(cfg, B, y):
    if cfg["kind"] == "pooled_mean":
        X = np.stack([b.mean(0) for b in B])
        yy = y
    else:
        parts = [_sel(b, cfg["rows"]) for b in B]
        X = np.concatenate(parts)
        yy = np.concatenate([[yi] * len(p) for yi, p in zip(y, parts)])
    sc = StandardScaler().fit(X)
    p = PCA(n_components=K, random_state=0).fit(sc.transform(X))
    m = _lr().fit(p.transform(sc.transform(X)), yy)
    return {"cfg": cfg, "scaler": sc, "pca": p, "lr": m}


def _score_bags(model, B) -> np.ndarray:
    cfg, sc, p, m = model["cfg"], model["scaler"], model["pca"], model["lr"]
    f = lambda X: m.decision_function(p.transform(sc.transform(X)))
    if cfg["kind"] == "pooled_mean":
        return f(np.stack([b.mean(0) for b in B]))
    pool = np.max if cfg["pool"] == "max" else np.mean
    return np.array([pool(f(_sel(b, cfg["rows"]))) for b in B])


def fit(cid: str, ds: h.Dataset, rows) -> dict:
    cfg = CANDIDATES[cid]
    return _fit_bags(cfg, bags(ds, rows, cfg["layer"]), ds.y(rows))


def score(model: dict, ds: h.Dataset, rows) -> np.ndarray:
    return _score_bags(model, bags(ds, rows, model["cfg"]["layer"]))


def fit_predict_for_audit(cid: str, ds: h.Dataset):
    """X passed to fit_predict is a column of D-v2a row ids; bags are looked up inside."""
    cfg = CANDIDATES[cid]

    def featurizer(ds_, rows):
        return np.asarray(rows, dtype=np.float64)[:, None]

    def fit_predict(Xa, ya, Xb):
        ra, rb = Xa[:, 0].astype(int).tolist(), Xb[:, 0].astype(int).tolist()
        model = _fit_bags(cfg, bags(ds, ra, cfg["layer"]), np.asarray(ya))
        return _score_bags(model, bags(ds, rb, cfg["layer"]))

    return fit_predict, featurizer


def main() -> None:
    ds = h.load("D-v2a")
    tr = list(ds.train_idx)
    y = ds.y(tr)
    fam, plen, step, dup = ds.groups(tr), ds.prefix_length(tr), ds.step_index(tr), ds.dup_groups(tr)
    ids = np.asarray(tr, dtype=np.float64)[:, None]
    base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
    ls = h.grouped_cv(lambda Xa, ya, Xb: _lr().fit(Xa, ya).decision_function(Xb), base, y, fam, 5,
                      strata=plen, dup_groups=dup)
    report = {"dataset": "D-v2a", "harness_sha256": h.harness_sha256(),
              "samples_sha256": h.file_sha256(ds.root / "samples.csv"), "train_rows": len(tr),
              "train_positives": int(y.sum()), "length+step_cv": {k: ls[k] for k in ("auroc", "stratified_auroc")},
              "candidates": {}}
    thr = max(0.60, ls["stratified_auroc"] + 0.03)
    OUT.mkdir(parents=True, exist_ok=True)
    for cid, cfg in CANDIDATES.items():
        fp, _ = fit_predict_for_audit(cid, ds)
        r = h.grouped_cv(fp, ids, y, fam, 5, strata=plen, dup_groups=dup)
        g0 = bool(r["stratified_auroc"] is not None and r["stratified_auroc"] >= thr)
        model = fit(cid, ds, tr)
        in_sample = h.auroc(score(model, ds, tr), y)
        d = OUT / cid
        d.mkdir(exist_ok=True)
        with open(d / "model.pkl", "wb") as fh:
            pickle.dump(model, fh)
        entry = {"desc": cfg["desc"], "inputs": [f"activation:L{cfg['layer']}"],
                 "cv_auroc": r["auroc"], "cv_stratified_auroc": r["stratified_auroc"],
                 "cv_ap": h.average_precision(r["oof"], y) if "oof" in r else None,
                 "G0_threshold": thr, "G0_pass": g0, "train_in_sample_auroc": in_sample,
                 "model_sha256": hashlib.sha256((d / "model.pkl").read_bytes()).hexdigest(),
                 "input_check": h.check_inputs([f"activation:L{cfg['layer']}"])}
        (d / "cv_report.json").write_text(json.dumps(entry, indent=1, default=float))
        (d / "card.md").write_text(
            f"# {cid} — Iteration 1 (A4)\n\n- {cfg['desc']}\n- Hypothesis: H-06 (sequence pooling); selected on D-v2 train CV "
            "(iter01_pooling_compare.py, 24 feature×layer configs; selection effect not corrected)\n"
            f"- Data: D-v2a train {len(tr)} rows / {int(y.sum())} action positives; fitted on all train rows\n"
            f"- Train CV (family GroupKFold 5, harness): AUROC {r['auroc']:.3f}, fold×tertile stratified "
            f"{r['stratified_auroc']:.3f}; length+step stratified {ls['stratified_auroc']:.3f}; G0 threshold {thr:.3f} -> "
            f"{'PASS' if g0 else 'FAIL'}\n- Train in-sample AUROC {in_sample:.3f}\n"
            f"- Model: `model.pkl` sha256 {entry['model_sha256'][:16]}…; scoring: `iter01_candidates.score`\n"
            "- Validation: not computed by A4.\n")
        report["candidates"][cid] = entry
        print(cid, json.dumps({k: entry[k] for k in ("cv_auroc", "cv_stratified_auroc", "G0_pass", "train_in_sample_auroc")},
                              default=float), flush=True)
    report["length+step_cv"]["G0_threshold"] = thr
    (OUT / "summary.json").write_text(json.dumps(report, indent=1, default=float))


if __name__ == "__main__":
    main()
