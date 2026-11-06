"""A5 Argus-V1 step 1 (train only, no validation scores): provenance, input/feature-timing check, G0 recompute.

G0 = family GroupKFold-5 (dup groups kept together, harness.grouped_folds), fold x length-tertile stratified AUROC,
threshold max(0.60, length+step strat + 0.03). length+step is run in the A4 setup (global StandardScaler, balanced LR,
as in experiments/risk_loop/argus_v1_train_cv.py) and in harness style (fold scaler, unbalanced LR); length_only
is reported as reference. Fold x tertile AUROC is also recomputed with sklearn.roc_auc_score from the OOF scores.

  CUDA_VISIBLE_DEVICES= .venv/bin/python artifacts/risk-loop/argus-v1/eval/scripts/s1_provenance_g0.py
"""
import csv, json, subprocess, sys, platform, pickle
from pathlib import Path
import numpy as np, scipy, sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[5]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H  # noqa: E402
from experiments.risk_loop import argus_models as am  # noqa: E402

EV = ROOT / "artifacts/risk-loop/argus-v1/eval"
MODEL = ROOT / "artifacts/risk-loop/argus-v1/model/model.pkl"
EXPECT = {"harness": "4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8",
          "model": "a43fd7508b6741a50f9fc715d905ff56d77b7ca8d7ccac953da8245394b14f0f",
          "samples": "ddd8dc9a448cb4af4fdc2e984d33cdc54e7f957279ad087077c2230d48dcbb0f"}


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout


def provenance(ds):
    diff = git("diff", "HEAD")
    import hashlib
    p = {"git_head": git("rev-parse", "HEAD").strip(), "git_status_short": git("status", "--short").splitlines(),
         "uncommitted_diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
         "harness_sha256": H.harness_sha256(), "model_pkl_sha256": H.file_sha256(MODEL),
         "samples_csv_sha256": H.file_sha256(ds.root / "samples.csv"),
         "rows_tv_sha256": H.file_sha256(ds.root / "rows_tv.npy"),
         "argus_models_py_sha256": H.file_sha256(ROOT / "experiments/risk_loop/argus_models.py"),
         "argus_v1_train_py_sha256": H.file_sha256(ROOT / "experiments/risk_loop/argus_v1_train.py"),
         "obs_L16_sha256_from_D-v2_hash.json": json.loads((am.D2 / "hash.json").read_text())["obs_L16.npy"],
         "obs_rows_csv_sha256": H.file_sha256(am.D2 / "obs_rows.csv"),
         "python": sys.version.split()[0], "numpy": np.__version__, "sklearn": sklearn.__version__,
         "scipy": scipy.__version__, "platform": platform.platform(), "gpu": False}
    p["matches"] = {"harness": p["harness_sha256"] == EXPECT["harness"], "model": p["model_pkl_sha256"] == EXPECT["model"],
                    "samples": p["samples_csv_sha256"] == EXPECT["samples"]}
    p["splits"] = {s: sum(1 for r in ds.samples if r["split"] == s) for s in ("train", "validation", "test")}
    return p


def timing_check(ds):
    """Every observation row of every train/val sample must have token_index <= prefix_length-1 (before the action)."""
    d2 = {int(r["sample"]): r for r in csv.DictReader(open(am.D2 / "samples.csv"))}
    obs = list(csv.DictReader(open(am.D2 / "obs_rows.csv")))
    idx = am.obs_index()
    bad, n, kinds = 0, 0, {}
    mism = 0
    for r in ds.train_idx + ds.val_idx:
        s = ds.samples[r]; k = int(s["d2_sample"])
        pl = int(d2[k]["prefix_length"])
        if pl != s["prefix_length"] or d2[k]["split"] != s["split"]:
            mism += 1
        for i in idx[k]:
            n += 1; kinds[obs[i]["kind"].split("+")[0]] = kinds.get(obs[i]["kind"].split("+")[0], 0) + 1
            if int(obs[i]["token_index"]) > pl - 1:
                bad += 1
    return {"obs_rows_checked": n, "obs_after_action_start": bad, "obs_kinds": kinds,
            "d2_prefix_length_or_split_mismatch": mism, "pass": bad == 0 and mism == 0}


def own_strat(y, s, strata):
    num = den = 0.0
    for k in np.unique(strata):
        m = strata == k; p = int(y[m].sum()); n = int(m.sum()) - p
        if p and n:
            num += roc_auc_score(y[m], s[m]) * p * n; den += p * n
    return num / den


def main():
    ds = H.load("D-v2r")
    out = {"provenance": provenance(ds)}
    with open(MODEL, "rb") as f:
        m = pickle.load(f)
    out["model_object"] = {"keys": sorted(m.keys()), "layer": m["layer"], "cfg": {k: v for k, v in m["cfg"].items()},
                           "pca_components": int(m["pca"].n_components_), "lr_coef_n": int(m["lr"].coef_.size)}
    out["inputs_check"] = H.check_inputs(["activation:L16"])
    out["feature_timing"] = timing_check(ds)
    print(json.dumps(out, indent=1, default=str), flush=True)

    tr = list(ds.train_idx)
    y, fam, plen, step, dup = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.step_index(tr), ds.dup_groups(tr)
    folds = H.grouped_folds(y, fam, 5, dup)
    fold_id = np.empty(len(y), int)
    for i, (_, b) in enumerate(folds):
        fold_id[b] = i
    tert = H.tertile_bins(plen)
    g0 = {"train_rows": len(tr), "train_positives": int(y.sum()),
          "folds": [{"fold": i, "n": int(len(b)), "positives": int(y[b].sum()),
                     "positive_families": sorted(set(fam[b][y[b] == 1].tolist()))} for i, (_, b) in enumerate(folds)]}

    def lr_bal(a, b, c):
        return LogisticRegression(class_weight="balanced", max_iter=3000).fit(a, b).decision_function(c)

    def lr_plain(a, b, c):
        sc = StandardScaler().fit(a)
        return LogisticRegression(max_iter=1000).fit(sc.transform(a), b).decision_function(sc.transform(c))

    base = np.column_stack([np.log(plen), step])
    runs = {"length+step(A4: global scaler, balanced)": (lr_bal, StandardScaler().fit_transform(base)),
            "length+step(harness-style: fold scaler, unbalanced)": (lr_plain, base),
            "length_only(fold scaler, unbalanced)": (lr_plain, np.log(plen)[:, None]),
            "Argus-V1": (am.cv_fit_predict("Argus-V1", ds), np.asarray(tr, float)[:, None])}
    for name, (fp, X) in runs.items():
        r = H.grouped_cv(fp, X, y, fam, 5, strata=plen, dup_groups=dup)
        oof = r["oof"]
        g0[name] = {"cv_auroc": r["auroc"], "fold_x_tertile": r["stratified_auroc"],
                    "pooled_tertile_biased": r["stratified_auroc_pooled_biased"],
                    "own_sklearn_fold_x_tertile": own_strat(y, oof, fold_id * 3 + tert),
                    "own_sklearn_fold_strat": own_strat(y, oof, fold_id),
                    "per_fold_auroc": [f["auroc"] for f in r["folds"]]}
        if name == "Argus-V1":
            np.save(EV / "g0_oof_Argus-V1.npy", oof)
        print(name, json.dumps(g0[name], default=float), flush=True)
    thr = max(0.60, g0["length+step(A4: global scaler, balanced)"]["fold_x_tertile"] + 0.03)
    thr_h = max(0.60, g0["length+step(harness-style: fold scaler, unbalanced)"]["fold_x_tertile"] + 0.03)
    v = g0["Argus-V1"]["fold_x_tertile"]
    a4 = json.loads((ROOT / "artifacts/risk-loop/argus-v1/model/train_report.json").read_text())
    g0["verdict"] = {"threshold": thr, "threshold_harness_style_baseline": thr_h, "argus_v1": v,
                     "pass": bool(v >= thr), "pass_vs_harness_style": bool(v >= thr_h),
                     "a4_reported_stratified": a4["cv"]["stratified_auroc"],
                     "abs_diff_vs_a4": abs(a4["cv"]["stratified_auroc"] - v),
                     "max_metadata_reference": max(g0["length_only(fold scaler, unbalanced)"]["fold_x_tertile"],
                                                   g0["length+step(A4: global scaler, balanced)"]["fold_x_tertile"],
                                                   g0["length+step(harness-style: fold scaler, unbalanced)"]["fold_x_tertile"])}
    out["G0"] = g0
    print(json.dumps(g0["verdict"], default=float))
    (EV / "s1_provenance_g0.json").write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
