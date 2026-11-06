"""Argus-V1 final training (A4, train only). Approved 2026-11-06 (option 나).

Steps: (1) fixed family GroupKFold-5 CV on D-v2r train (record), (2) final fit on all train rows at L16,
(3) save model.pkl, card.md, train_report.json under artifacts/risk-loop/argus-v1/model/. No validation/test rows.
Features: HF-restored step captures (D-v2/obs_L16.npy); CPU only. Logistic regression is full-batch lbfgs (no epochs).

  .venv/bin/python experiments/risk_loop/argus_v1_train.py
"""
from __future__ import annotations

import hashlib
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402
from experiments.risk_loop import argus_models as am  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/argus-v1/model"
NAME = "Argus-V1"


def main() -> None:
    t0 = time.time()
    ds = h.load("D-v2r")
    tr = list(ds.train_idx)
    assert all(ds.samples[r]["split"] == "train" for r in tr)
    y, fam, plen, dup = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.dup_groups(tr)
    cfg = am.MODELS[NAME]
    cv = h.grouped_cv(am.cv_fit_predict(NAME, ds), np.asarray(tr, dtype=np.float64)[:, None], y, fam, 5,
                      strata=plen, dup_groups=dup)
    print("cv", cv["auroc"], cv["stratified_auroc"], flush=True)
    model = am.fit(NAME, ds, tr)
    lr = model["lr"]
    in_sample = h.auroc(am.score(model, ds, tr), y)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "model.pkl", "wb") as fh:
        pickle.dump(model, fh)
    msha = hashlib.sha256((OUT / "model.pkl").read_bytes()).hexdigest()
    rep = {
        "model": NAME, "structure": cfg["desc"], "layer": cfg["layer"], "pca_components": cfg["pca"], "C": cfg["C"],
        "class_weight": "balanced", "solver": "lbfgs (full batch, no epochs)", "max_iter": 3000,
        "lbfgs_iterations_used": int(lr.n_iter_[0]), "converged": bool(lr.n_iter_[0] < 3000),
        "pca_explained_variance_ratio_sum": float(model["pca"].explained_variance_ratio_.sum()),
        "trained_parameters": int(lr.coef_.size + lr.intercept_.size),
        "dataset": "D-v2r", "samples_sha256": h.file_sha256(ds.root / "samples.csv"),
        "features": "artifacts/risk-loop/datasets/D-v2/obs_L16.npy (HF scenario_rollout captures)",
        "label_status": "DRAFT_LLM_REVIEW_NOT_GOLD (v2) + rule labels (v1h2)",
        "train_rows": len(tr), "train_positives": int(y.sum()), "train_positive_families": len(set(fam[y == 1])),
        "cv": {"auroc": cv["auroc"], "stratified_auroc": cv["stratified_auroc"], "ap": h.average_precision(cv["oof"], y)},
        "train_in_sample_auroc": in_sample, "harness_sha256": h.harness_sha256(), "model_sha256": msha,
        "validation_touched": False, "test_touched": False, "gpu": False, "seconds": round(time.time() - t0, 1),
    }
    (OUT / "train_report.json").write_text(json.dumps(rep, indent=1, default=float))
    (OUT / "card.md").write_text(
        f"# {NAME}\n\n- Structure: {cfg['desc']} (layer L{cfg['layer']})\n"
        f"- Training: StandardScaler (1 pass) -> PCA-{cfg['pca']} (SVD) -> LogReg C={cfg['C']}, balanced, lbfgs full batch "
        f"({rep['lbfgs_iterations_used']} iterations, converged={rep['converged']}); trained parameters {rep['trained_parameters']}\n"
        f"- Data: D-v2r train {len(tr)} rows / {int(y.sum())} positives / {rep['train_positive_families']} families; "
        f"samples sha256 {rep['samples_sha256'][:16]}…; labels DRAFT\n"
        f"- Train CV (family GroupKFold 5): AUROC {cv['auroc']:.3f}, stratified {cv['stratified_auroc']:.3f}; in-sample {in_sample:.3f}\n"
        f"- model.pkl sha256 {msha}\n- Scoring: `experiments/risk_loop/argus_models.score(model, ds, rows)` (logit)\n"
        "- Validation: not computed here (A5).\n")
    print(json.dumps(rep, indent=1, default=float))


if __name__ == "__main__":
    main()
