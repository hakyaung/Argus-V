"""Argus-V1 train-only evaluation on D-v2r (review DRAFT labels). No validation rows are touched.

Reports per layer (L11/L16/L23; L16 is the registered default): family GroupKFold-5 CV AUROC and fold x length-tertile
stratified AUROC, length+step and domain-tool baselines, within-domain-tool and within-same-tool AUROC, and the
correlation of the score with the number of observation rows (max pooling length check).

  .venv/bin/python experiments/risk_loop/argus_v1_train_cv.py
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
from experiments.risk_loop import argus_models as am  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/argus-v1/train-cv"


def run(ds, tr, tag):
    y, fam, plen, step, dup = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.step_index(tr), ds.dup_groups(tr)
    tool = np.array([ds.samples[r]["tool"] for r in tr])
    dom = np.array([am.is_domain_tool(t) for t in tool])
    nobs = np.array([len(am.obs_index()[int(ds.samples[r]["d2_sample"])]) for r in tr])
    base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
    ls = h.grouped_cv(lambda a, b, c: LogisticRegression(class_weight="balanced", max_iter=3000).fit(a, b).decision_function(c),
                      base, y, fam, 5, strata=plen, dup_groups=dup)
    thr = max(0.60, ls["stratified_auroc"] + 0.03)
    res = {"rows": len(tr), "positives": int(y.sum()),
           "baselines": {"length+step": {"auroc": ls["auroc"], "stratified_auroc": ls["stratified_auroc"]},
                         "domain_tool_indicator_auroc": h.auroc(dom.astype(float), y)},
           "G0_threshold": thr, "layers": {}}
    ids = np.asarray(tr, dtype=np.float64)[:, None]
    for L in (11, 16, 23):
        r = h.grouped_cv(am.cv_fit_predict("Argus-V1", ds, L), ids, y, fam, 5, strata=plen, dup_groups=dup)
        oof = np.asarray(r["oof"])
        wt = [h.auroc(oof[tool == t], y[tool == t]) for t in sorted(set(tool[y == 1]))
              if 0 < y[tool == t].sum() < (tool == t).sum()]
        e = {"auroc": r["auroc"], "stratified_auroc": r["stratified_auroc"], "ap": h.average_precision(oof, y),
             "G0_pass": bool(r["stratified_auroc"] >= thr),
             "within_domain_tool_auroc": h.auroc(oof[dom], y[dom]),
             "within_same_tool_mean_auroc": float(np.mean(wt)), "within_same_tool_n_tools": len(wt),
             "score_corr_n_obs": float(np.corrcoef(oof, nobs)[0, 1]), "default": L == am.MODELS["Argus-V1"]["layer"]}
        res["layers"][f"L{L}"] = e
        print(tag, f"L{L}", json.dumps(e, default=float), flush=True)
    return res


def main():
    ds = h.load("D-v2r")
    tr = list(ds.train_idx)
    rep = {"model": "Argus-V1", "structure": am.MODELS["Argus-V1"]["desc"], "dataset": "D-v2r",
           "label_status": "DRAFT_LLM_REVIEW_NOT_GOLD", "harness_sha256": h.harness_sha256(),
           "samples_sha256": h.file_sha256(ds.root / "samples.csv"), "validation_touched": False,
           "all_train": run(ds, tr, "all"),
           "v2_only_train": run(ds, [r for r in tr if ds.samples[r]["version"] == "v2"], "v2only")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float))


if __name__ == "__main__":
    main()
