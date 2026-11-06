"""A5 iter-01 supplement (train only): label-shuffle null with the same harness shuffle (seed 0, 20 reps), reporting
pooled OOF AUROC (what harness.label_shuffle_audit uses), fold-stratified AUROC and fold x tertile stratified AUROC.
Also the same null for the length+step metadata model, to see whether the pooled-AUROC deviation is protocol-wide."""
import json, sys
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
ROOT = Path(__file__).resolve().parents[6]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H
from experiments.risk_loop import iter01_candidates as C
AUD = ROOT / "artifacts/risk-loop/iter-01/eval/a5-audit"
ds = H.load("D-v2a"); tr = list(ds.train_idx)
y, fam, plen, dup, step = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.dup_groups(tr), ds.step_index(tr)
fold_id = np.empty(len(y), int)
for i, (_, b) in enumerate(H.grouped_folds(y, fam, 5, dup)): fold_id[b] = i
tert = H.tertile_bins(plen)
def lsfp(Xa, ya, Xb):
    sc = StandardScaler().fit(Xa); return LogisticRegression(max_iter=1000).fit(sc.transform(Xa), ya).decision_function(sc.transform(Xb))
models = {cid: (C.fit_predict_for_audit(cid, ds)[0], np.asarray(tr, float)[:, None]) for cid in sys.argv[1:]}
models["length+step"] = (lsfp, np.c_[np.log(plen), step])
res = {}
for name, (fp, X) in models.items():
    rng = np.random.default_rng(0); rows = []
    for _ in range(20):
        ys = H.within_group_shuffle(y, fam, rng)
        r = H.grouped_cv(fp, X, ys, fam, 5, None, dup); oof = r["oof"]
        rows.append({"pooled": r["auroc"], "fold_strat": H.stratified_auroc(oof, ys, fold_id)["auroc"],
                     "fold_x_tertile": H.stratified_auroc(oof, ys, fold_id * 3 + tert)["auroc"]})
    res[name] = {k: {"mean": float(np.mean([r[k] for r in rows])), "sd": float(np.std([r[k] for r in rows]))} for k in rows[0]}
    res[name]["reps"] = rows
    print(name, {k: res[name][k] for k in ("pooled", "fold_strat", "fold_x_tertile")}, flush=True)
(AUD / "shuffle_supplement.json").write_text(json.dumps(res, indent=1, default=float))
