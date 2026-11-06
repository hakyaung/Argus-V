"""Argus-V1 structure fixing on TRAIN ONLY (October labelled data + November scenario data). No validation/test.

Data (train split only):
  November scenario data  D-v2r train rows (review DRAFT labels for v2, rule labels for re-runs), features D-v2/obs_L*.npy
  October labelled data   reviewed labels (artifacts/risk-loop/datasets/D-v1/samples.csv, split == train only),
                          features read from each step's capture/features.npz (all observation rows).
                          Label rule (option 가, aligned with November target): positives on report/stop tools
                          (write_report, stop, submit_result) are DROPPED (neither positive nor negative).
Compared (family GroupKFold-5, dup groups kept within a fold, length-tertile stratified):
  A. Argus-V1 grid (max pooling): layer {11,16,23} x PCA {5,10,20} x C {0.1,1}  -> 18 configs
  B. structural alternatives at L16/PCA-10/C=1 (adoption would make Argus-V2): mean pooling, max+mean concat,
     tool-effect removal (per-tool mean subtracted, fitted inside the training fold)
Fixed shortcut checks: length+step baseline, domain-tool indicator, tool-identity (fold-fitted positive rate per tool),
  October/November source indicator, score vs n_obs correlation, label shuffle (3 permutations within family).
Pre-registered selection for Argus-V1: max within-same-tool mean AUROC among grid configs that pass
  stratified AUROC >= max(0.60, length+step stratified + 0.03); ties -> stratified AUROC.

  .venv/bin/python experiments/risk_loop/argus_v1_structure_cv.py
"""
from __future__ import annotations

import csv
import json
import os
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
from experiments.risk_loop import argus_models as am  # noqa: E402

OUT = ROOT / "artifacts/risk-loop/argus-v1/structure-cv"
OCT = ROOT / "artifacts/risk-loop/datasets/D-v1/samples.csv"
LAYERS = (11, 16, 23)
REPORT_TOOLS = {"write_report", "stop", "submit_result"}


def tool_of_step(step: dict) -> str:
    cmd = step.get("command")
    if step.get("kind") == "tool" and isinstance(cmd, str):
        try:
            return json.loads(cmd).get("tool", "")
        except json.JSONDecodeError:
            return ""
    return step.get("kind") or ""


def load_all():
    meta, feats = [], {(L, p): [] for L in LAYERS for p in ("max", "mean")}
    # November scenario data (train)
    ds = h.load("D-v2r")
    tr = list(ds.train_idx)
    assert all(ds.samples[r]["split"] == "train" for r in tr)
    for L in LAYERS:
        for b in am.bags(ds, tr, L):
            feats[(L, "max")].append(b.max(0)); feats[(L, "mean")].append(b.mean(0))
    idx = am.obs_index()
    for r in tr:
        s = ds.samples[r]
        meta.append({"src": "nov", "family": s["family_id"], "y": int(s["label"]), "plen": int(s["prefix_length"]),
                     "step": int(s["step_index"]), "dup": "h:" + s["prefix_hash"], "tool": s["tool"],
                     "traj": s["trajectory_id"], "n_obs": len(idx[int(s["d2_sample"])])})
    # October labelled data (train only; test/validation rows never opened)
    dropped = 0
    for s in csv.DictReader(open(OCT)):
        if s["split"] != "train":
            continue
        cap = s["capture_dir"]
        step = json.load(open(os.path.join(os.path.dirname(cap), "step.json")))
        tool = tool_of_step(step)
        y = int(s["label"])
        if y == 1 and tool in REPORT_TOOLS:
            dropped += 1
            continue
        with np.load(os.path.join(cap, "features.npz")) as z:
            for L in LAYERS:
                b = z[f"block_{L:04d}"].astype(np.float32)
                feats[(L, "max")].append(b.max(0)); feats[(L, "mean")].append(b.mean(0))
            n_obs = b.shape[0]
        meta.append({"src": "oct", "family": s["family_id"], "y": y, "plen": int(s["prefix_length"]),
                     "step": int(s["step_index"]), "dup": "h:" + s["prefix_hash"], "tool": tool,
                     "traj": s["trajectory_id"], "n_obs": n_obs})
    F = {k: np.stack(v).astype(np.float32) for k, v in feats.items()}
    return meta, F, dropped


def make_fp(Xfull, pca_k, C, tool=None):
    def fp(Xa, ya, Xb):
        ia, ib = Xa[:, 0].astype(int), Xb[:, 0].astype(int)
        A, B = Xfull[ia], Xfull[ib]
        if tool is not None:  # tool-effect removal fitted on the training fold only
            mu = {t: A[tool[ia] == t].mean(0) for t in set(tool[ia])}
            g = A.mean(0)
            A = A - np.stack([mu[t] for t in tool[ia]])
            B = B - np.stack([mu.get(t, g) for t in tool[ib]])
        sc = StandardScaler().fit(A)
        p = PCA(n_components=pca_k, random_state=0).fit(sc.transform(A))
        lr = LogisticRegression(C=C, class_weight="balanced", max_iter=3000).fit(p.transform(sc.transform(A)), ya)
        return lr.decision_function(p.transform(sc.transform(B)))
    return fp


def metrics(oof, y, tool, n_obs, src):
    wt = [h.auroc(oof[tool == t], y[tool == t]) for t in sorted(set(tool[y == 1]))
          if 0 < y[tool == t].sum() < (tool == t).sum()]
    by_src = {s: h.auroc(oof[src == s], y[src == s]) for s in ("nov", "oct")}
    return {"within_same_tool_mean": float(np.mean(wt)) if wt else None, "within_same_tool_n": len(wt),
            "score_corr_n_obs": float(np.corrcoef(oof, n_obs)[0, 1]), "auroc_by_source": by_src}


def main():
    t0 = time.time()
    meta, F, dropped = load_all()
    y = np.array([m["y"] for m in meta]); fam = np.array([m["family"] for m in meta])
    plen = np.array([m["plen"] for m in meta], float); step = np.array([m["step"] for m in meta], float)
    dup = np.array([m["dup"] for m in meta]); tool = np.array([m["tool"] for m in meta])
    n_obs = np.array([m["n_obs"] for m in meta], float); src = np.array([m["src"] for m in meta])
    ids = np.arange(len(y), dtype=np.float64)[:, None]
    cv = lambda fp, X=ids: h.grouped_cv(fp, X, y, fam, 5, strata=plen, dup_groups=dup)
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / "run.log", "w")

    def say(*a):
        line = " ".join(str(x) for x in a)
        print(line, flush=True); log.write(line + "\n"); log.flush()

    data = {"rows": len(y), "positives": int(y.sum()),
            "by_source": {s: {"rows": int((src == s).sum()), "positives": int(y[src == s].sum()),
                              "families": len(set(fam[src == s])),
                              "positive_families": len(set(fam[(src == s) & (y == 1)]))} for s in ("nov", "oct")},
            "oct_report_stop_positives_dropped": dropped, "families": len(set(fam))}
    say("data", json.dumps(data))
    # shortcut baselines
    lr = lambda a, b, c: LogisticRegression(class_weight="balanced", max_iter=3000).fit(a, b).decision_function(c)
    ls = cv(lr, StandardScaler().fit_transform(np.column_stack([np.log(plen), step])))

    def tool_rate(Xa, ya, Xb):
        ia, ib = Xa[:, 0].astype(int), Xb[:, 0].astype(int)
        rate = {t: (ya[tool[ia] == t].sum() + 0.5) / ((tool[ia] == t).sum() + 1) for t in set(tool[ia])}
        g = (ya.sum() + 0.5) / (len(ya) + 1)
        return np.array([rate.get(t, g) for t in tool[ib]])
    tr = cv(tool_rate)
    dom = np.array([am.is_domain_tool(t) for t in tool], float)
    thr = max(0.60, ls["stratified_auroc"] + 0.03)
    base = {"length+step": {"auroc": ls["auroc"], "stratified_auroc": ls["stratified_auroc"]},
            "tool_identity_rate": {"auroc": tr["auroc"], "stratified_auroc": tr["stratified_auroc"]},
            "domain_tool_indicator_auroc": h.auroc(dom, y),
            "source_indicator_auroc(oct=1)": h.auroc((src == "oct").astype(float), y),
            "G0_threshold": thr}
    say("baselines", json.dumps(base, default=float))
    res = {"grid": {}, "structural": {}}
    for L in LAYERS:
        for k in (5, 10, 20):
            for C in (0.1, 1.0):
                r = cv(make_fp(F[(L, "max")], k, C))
                oof = np.asarray(r["oof"])
                e = {"layer": L, "pca": k, "C": C, "auroc": r["auroc"], "stratified_auroc": r["stratified_auroc"],
                     "ap": r["ap"], "G0_pass": bool(r["stratified_auroc"] >= thr),
                     "fold_auroc": [f["auroc"] for f in r["folds"]], **metrics(oof, y, tool, n_obs, src)}
                res["grid"][f"L{L}-pca{k}-C{C}"] = e
                say("grid", f"L{L}-pca{k}-C{C}", json.dumps(e, default=float))
    alts = {"mean_pool": (F[(16, "mean")], None),
            "max+mean_concat": (np.concatenate([F[(16, "max")], F[(16, "mean")]], 1), None),
            "max_tool_removed": (F[(16, "max")], tool)}
    for name, (X, tl) in alts.items():
        r = cv(make_fp(X, 10, 1.0, tl))
        oof = np.asarray(r["oof"])
        e = {"layer": 16, "pca": 10, "C": 1.0, "auroc": r["auroc"], "stratified_auroc": r["stratified_auroc"],
             "ap": r["ap"], "G0_pass": bool(r["stratified_auroc"] >= thr),
             "fold_auroc": [f["auroc"] for f in r["folds"]], **metrics(oof, y, tool, n_obs, src)}
        res["structural"][name] = e
        say("structural", name, json.dumps(e, default=float))
    ok = {k: v for k, v in res["grid"].items() if v["G0_pass"] and v["within_same_tool_mean"] is not None}
    pool = ok or res["grid"]
    best = max(pool, key=lambda k: (pool[k]["within_same_tool_mean"] or 0, pool[k]["stratified_auroc"] or 0))
    b = res["grid"][best]
    rng = np.random.default_rng(0)
    shuf = []
    for _ in range(3):
        ys = h.within_group_shuffle(y, fam, rng)
        shuf.append(h.grouped_cv(make_fp(F[(b["layer"], "max")], b["pca"], b["C"]), ids, ys, fam, 5,
                                 strata=plen, dup_groups=dup)["auroc"])
    sel = {"selected": best, "selection_pool": "G0_pass" if ok else "all (no config passed G0)",
           "label_shuffle_auroc": shuf, **b}
    say("selected", json.dumps(sel, default=float))
    rep = {"status": "TRAIN_ONLY_STRUCTURE_CV", "validation_touched": False, "test_touched": False, "gpu": False,
           "label_status": "November v2 DRAFT (LLM review) + re-run rule labels; October reviewed labels",
           "october_label_rule": "option 가: positives on write_report/stop/submit_result dropped",
           "harness_sha256": h.harness_sha256(), "data": data, "baselines": base, **res, "selection": sel,
           "seconds": round(time.time() - t0, 1)}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=float))
    (OUT / "argus_v1_config.json").write_text(json.dumps(
        {"model": "Argus-V1", "pool": "max", "layer": b["layer"], "pca": b["pca"], "C": b["C"],
         "class_weight": "balanced", "solver": "lbfgs", "max_iter": 3000,
         "data": "October labelled data + November scenario data (train)",
         "october_label_rule": rep["october_label_rule"], "selected_by": "argus_v1_structure_cv.py (train CV only)",
         "status": "PROVISIONAL until human labels"}, indent=1))
    say("done", rep["seconds"], "s")


if __name__ == "__main__":
    main()
