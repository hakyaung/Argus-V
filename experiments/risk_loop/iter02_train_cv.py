"""Iteration 2 (A4, train only): re-run fixed iteration-1 pooling configs on D-v2r (review DRAFT labels,
substantive action target). No validation rows are touched.

Configs (fixed in advance, no new search): C-01a/b/c from iter01_candidates, plus pooled mean at L16 / L23.
Baseline: length+step LogReg. G0 threshold = max(0.60, length+step stratified + 0.03).
Also a v2-only sensitivity (v1 rule-labelled rows removed).

  .venv/bin/python experiments/risk_loop/iter02_train_cv.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402
from experiments.risk_loop import iter01_candidates as c1  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/iter-02/train-cv"
CONFIGS = dict(c1.CANDIDATES)
CONFIGS["C-02-L16mean"] = {"layer": 16, "kind": "pooled_mean", "rows": "all", "pool": None, "desc": "L16 mean over all observation rows"}
CONFIGS["C-02-L23mean"] = {"layer": 23, "kind": "pooled_mean", "rows": "all", "pool": None, "desc": "L23 mean over all observation rows"}


def run(ds, tr, tag):
    y = ds.y(tr)
    fam, plen, step, dup = ds.groups(tr), ds.prefix_length(tr), ds.step_index(tr), ds.dup_groups(tr)
    base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
    ls = h.grouped_cv(lambda Xa, ya, Xb: c1._lr().fit(Xa, ya).decision_function(Xb), base, y, fam, 5,
                      strata=plen, dup_groups=dup)
    thr = max(0.60, ls["stratified_auroc"] + 0.03)
    ids = np.asarray(tr, dtype=np.float64)[:, None]
    res = {"rows": len(tr), "positives": int(y.sum()), "positive_families": len(set(np.asarray(fam)[y == 1])),
           "length+step": {"auroc": ls["auroc"], "stratified_auroc": ls["stratified_auroc"]}, "G0_threshold": thr,
           "configs": {}}
    print(tag, json.dumps({k: res[k] for k in ("rows", "positives", "positive_families", "length+step")}, default=float), flush=True)
    for cid, cfg in CONFIGS.items():
        def fp(Xa, ya, Xb, cfg=cfg):
            ra, rb = Xa[:, 0].astype(int).tolist(), Xb[:, 0].astype(int).tolist()
            m = c1._fit_bags(cfg, c1.bags(ds, ra, cfg["layer"]), np.asarray(ya))
            return c1._score_bags(m, c1.bags(ds, rb, cfg["layer"]))
        r = h.grouped_cv(fp, ids, y, fam, 5, strata=plen, dup_groups=dup)
        oof = np.asarray(r["oof"])
        per_fam = {}
        for f in sorted(set(np.asarray(fam)[y == 1])):  # family-held-out positives vs all negatives (same OOF scores)
            m = (np.asarray(fam) == f) | (y == 0)
            per_fam[str(f)] = h.auroc(oof[m], y[m])
        e = {"desc": cfg["desc"], "auroc": r["auroc"], "stratified_auroc": r["stratified_auroc"],
             "ap": h.average_precision(oof, y), "G0_pass": bool(r["stratified_auroc"] is not None and r["stratified_auroc"] >= thr),
             "per_positive_family_auroc": per_fam}
        res["configs"][cid] = e
        print(tag, cid, json.dumps({k: e[k] for k in ("auroc", "stratified_auroc", "ap", "G0_pass")}, default=float), flush=True)
    return res


def main() -> None:
    ds = h.load("D-v2r")
    tr = list(ds.train_idx)
    v2 = [r for r in tr if ds.samples[r]["version"] == "v2"]
    rep = {"dataset": "D-v2r", "label_status": "DRAFT_LLM_REVIEW_NOT_GOLD", "harness_sha256": h.harness_sha256(),
           "samples_sha256": h.file_sha256(ds.root / "samples.csv"), "validation_touched": False,
           "all_train": run(ds, tr, "all"), "v2_only_train": run(ds, v2, "v2only")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float))


if __name__ == "__main__":
    main()
