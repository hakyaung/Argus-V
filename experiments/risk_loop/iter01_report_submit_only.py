"""Control for the report target: among submit_result steps only, does the activation separate
false/under reports from honest ones? (train only, family GroupKFold)"""
import csv, json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h
from experiments.risk_loop.iter01_pooling_compare import pca_lr_fit_predict, evaluate, lr, D, OUT
from sklearn.preprocessing import StandardScaler
S = list(csv.DictReader(open(D / "samples.csv"))); owner = np.array([int(o["sample"]) for o in csv.DictReader(open(D / "obs_rows.csv"))])
idx = np.array([i for i, s in enumerate(S) if s["split"] == "train" and s["tool"] == "submit_result" and s["label_kind"] in ("", "report")])
y = np.array([int(S[i]["label_kind"] == "report") for i in idx]); fam = np.array([S[i]["family_id"] for i in idx])
plen = np.array([float(S[i]["prefix_length"]) for i in idx]); step = np.array([float(S[i]["step_index"]) for i in idx])
dup = np.unique([S[i]["prefix_sha256"] for i in idx], return_inverse=True)[1]
last_row = {}
for r, s in enumerate(owner): last_row[s] = r
out = {"rows": len(idx), "positives": int(y.sum()), "positive_families": len(set(fam[y == 1])), "results": []}
base = StandardScaler().fit_transform(np.column_stack([np.log(plen), step]))
out["results"].append(evaluate("length+step", lambda a, b: lr().fit(base[a], y[a]).decision_function(base[b]), None, y, fam, plen, dup))
for layer in (11, 16, 23):
    A = np.load(D / f"obs_L{layer:02d}.npy", mmap_mode="r"); X = np.asarray(A[[last_row[i] for i in idx]], dtype=np.float32)
    r = evaluate(f"L{layer} last", lambda a, b: pca_lr_fit_predict(X[a], y[a], X[b]), None, y, fam, plen, dup); out["results"].append(r)
for r in out["results"]: print(r["name"], round(r["fold_mean_auroc"], 3), r["fold_aurocs"], "len-strat", round(r["len_strat_auroc"], 3), "rec@5%", round(r["recall_at_5pct_fpr"], 3))
print({k: v for k, v in out.items() if k != "results"})
(OUT / "report_submit_only.json").write_text(json.dumps(out, indent=1, default=float))
