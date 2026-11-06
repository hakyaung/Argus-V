"""A5 iter-01 step 2: G0 (train only). Re-runs family GroupKFold CV for C-01a/b/c and length+step on D-v2a train,
recomputes fold x tertile stratified AUROC with sklearn from the OOF scores. No validation rows touched."""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
ROOT = Path(__file__).resolve().parents[6]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H
from experiments.risk_loop import iter01_candidates as C
OUT = Path(sys.argv[1])
ds = H.load("D-v2a"); tr = list(ds.train_idx)
y, fam, plen, step, dup = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.step_index(tr), ds.dup_groups(tr)
folds = H.grouped_folds(y, fam, 5, dup)
fold_id = np.empty(len(y), int)
for i, (_, b) in enumerate(folds): fold_id[b] = i
lv = np.log(plen); tert = np.digitize(lv, np.quantile(lv, [1/3, 2/3]))
def own_strat(s, strata):
    num = den = 0.0
    for k in np.unique(strata):
        m = strata == k; p = int(y[m].sum()); n = int(m.sum()) - p
        if p and n: num += roc_auc_score(y[m], s[m]) * p * n; den += p * n
    return num / den
def own_fold_auc(s):
    num = den = 0.0
    for k in range(len(folds)):
        m = fold_id == k; p = int(y[m].sum()); n = int(m.sum()) - p
        if p and n: num += roc_auc_score(y[m], s[m]) * p * n; den += p * n
    return num / den
res = {"train_rows": len(tr), "train_positives": int(y.sum()),
       "folds": [{"fold": i, "n": int(len(b)), "positives": int(y[b].sum()), "families": sorted(set(fam[b].tolist()))} for i, (_, b) in enumerate(folds)]}
def lr_bal(Xa, ya, Xb): return LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000).fit(Xa, ya).decision_function(Xb)
def lr_plain(Xa, ya, Xb):
    sc = StandardScaler().fit(Xa); return LogisticRegression(max_iter=1000).fit(sc.transform(Xa), ya).decision_function(sc.transform(Xb))
base = np.column_stack([np.log(plen), step])
runs = {"length+step(A4: global scaler, balanced)": (lr_bal, StandardScaler().fit_transform(base)),
        "length+step(harness-style: fold scaler, unbalanced)": (lr_plain, base),
        "length_only(fold scaler, unbalanced)": (lr_plain, np.log(plen)[:, None])}
ids = np.asarray(tr, float)[:, None]
for cid in ("C-01a", "C-01b", "C-01c"):
    runs[cid] = (C.fit_predict_for_audit(cid, ds)[0], ids)
for name, (fp, X) in runs.items():
    r = H.grouped_cv(fp, X, y, fam, 5, strata=plen, dup_groups=dup)
    oof = r["oof"]
    res[name] = {"harness_cv_auroc": r["auroc"], "harness_fold_x_tertile": r["stratified_auroc"],
                 "harness_pooled_biased": r["stratified_auroc_pooled_biased"],
                 "own_pooled_auroc": roc_auc_score(y, oof), "own_fold_x_tertile": own_strat(oof, fold_id * 3 + tert),
                 "own_fold_stratified": own_fold_auc(oof), "per_fold_auroc": [f["auroc"] for f in r["folds"]]}
    np.save(OUT.parent / f"g0_oof_{name.split('(')[0].replace('+', '_')}{'_A4' if 'A4' in name else ''}.npy", oof)
    print(name, json.dumps(res[name], default=float), flush=True)
thr = max(0.60, res["length+step(A4: global scaler, balanced)"]["own_fold_x_tertile"] + 0.03)
thr_h = max(0.60, res["length+step(harness-style: fold scaler, unbalanced)"]["own_fold_x_tertile"] + 0.03)
for cid in ("C-01a", "C-01b", "C-01c"):
    v = res[cid]["own_fold_x_tertile"]
    res[cid]["G0_threshold"] = thr; res[cid]["G0_pass"] = bool(v >= thr)
    res[cid]["G0_pass_vs_harness_style_baseline"] = bool(v >= thr_h)
    a4 = json.loads((ROOT / f"artifacts/risk-loop/iter-01/candidates/{cid}/cv_report.json").read_text())
    res[cid]["a4_cv_stratified_auroc"] = a4["cv_stratified_auroc"]; res[cid]["abs_diff_vs_a4"] = abs(a4["cv_stratified_auroc"] - v)
OUT.write_text(json.dumps(res, indent=1, default=float))
