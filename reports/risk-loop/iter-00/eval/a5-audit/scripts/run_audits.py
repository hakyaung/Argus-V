import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, "."); sys.path.insert(0, "src")
from experiments.risk_loop import harness as H
from experiments.risk_loop.iter00_reproduce import logreg_fp, jsonable
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
ds = H.load("D-v1"); tr, va = list(ds.train_idx), list(ds.val_idx)
for cid, L in (("C-00b", 23), ("C-00c", 16)):
    sv = logreg_fp(ds.features(L, tr), ds.y(tr), ds.features(L, va))
    # identical to the scores of the counted validation look (same deterministic fit)
    prev = json.load(open(f"artifacts/risk-loop/iter-00/eval/{cid}.json"))
    a = H.audits(logreg_fp, ds, L, input_names=[f"activation:L{L}"], scores_by_row=dict(zip(va, sv.tolist())),
                 n_shuffle=5, seed=0, bootstrap=2000)
    a["val_auroc_recomputed"] = H.auroc(sv, ds.y(va)); a["val_auroc_reported"] = prev["auroc"]
    # extra: train-only OOF length correlation and length-residual stratified CV
    cv = H.grouped_cv(logreg_fp, ds.features(L, tr), ds.y(tr), ds.groups(tr), 5, ds.prefix_length(tr), ds.dup_groups(tr))
    a["train_oof_length_correlation"] = H.length_correlation(cv["oof"], ds.prefix_length(tr))
    p = OUT / f"audit_{cid}.json"
    assert not p.exists()
    p.write_text(json.dumps(jsonable(a), indent=2))
    print(cid, json.dumps(jsonable({k: v for k, v in a.items() if k != "length_residual_G2"}), indent=1)[:3000])
    print("resid G2", {k: a["length_residual_G2"][k] for k in ("stratified_auroc", "ci_low", "ci_high", "pass")})
