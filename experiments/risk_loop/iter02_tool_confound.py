"""Iteration 2 confound check (train only, D-v2r): is the activation score just 'next action is a domain tool call'?

GENERIC tools = read_file, write_output, submit_result, notify_requester, bash, request_* (grant requests).
Reports: AUROC of the 'domain tool' indicator alone; OOF AUROC of C-01a / C-02-L16mean within domain-tool rows only;
and within-tool AUROC for tools that have both positives and negatives.

  .venv/bin/python experiments/risk_loop/iter02_tool_confound.py
"""
from __future__ import annotations
import json, sys
from collections import Counter
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402
from experiments.risk_loop import iter01_candidates as c1  # noqa: E402
from experiments.risk_loop.iter02_train_cv import CONFIGS  # noqa: E402

GENERIC = {"read_file", "write_output", "submit_result", "notify_requester", "bash"}


def is_domain(t):
    return t not in GENERIC and not t.startswith("request_")


def main():
    ds = h.load("D-v2r")
    out = {}
    for tag, sel in (("all", lambda s: True), ("v2only", lambda s: s["version"] == "v2")):
        tr = [r for r in ds.train_idx if sel(ds.samples[r])]
        y, fam, plen, dup = ds.y(tr), ds.groups(tr), ds.prefix_length(tr), ds.dup_groups(tr)
        tool = np.array([ds.samples[r]["tool"] for r in tr])
        dom = np.array([is_domain(t) for t in tool])
        res = {"domain_rows": int(dom.sum()), "domain_positives": int(y[dom].sum()), "positives": int(y.sum()),
               "domain_indicator_auroc": h.auroc(dom.astype(float), y)}
        ids = np.asarray(tr, dtype=np.float64)[:, None]
        for cid in ("C-01a", "C-02-L16mean"):
            cfg = CONFIGS[cid]
            def fp(Xa, ya, Xb, cfg=cfg):
                m = c1._fit_bags(cfg, c1.bags(ds, Xa[:, 0].astype(int).tolist(), cfg["layer"]), np.asarray(ya))
                return c1._score_bags(m, c1.bags(ds, Xb[:, 0].astype(int).tolist(), cfg["layer"]))
            oof = np.asarray(h.grouped_cv(fp, ids, y, fam, 5, strata=plen, dup_groups=dup)["oof"])
            wt = {}
            for t in sorted(set(tool[y == 1])):
                m = tool == t
                if 0 < y[m].sum() < m.sum():
                    wt[t] = {"n": int(m.sum()), "pos": int(y[m].sum()), "auroc": h.auroc(oof[m], y[m])}
            # domain-only refit: train and score on domain-tool rows only
            d_idx = [r for r, d in zip(tr, dom) if d]
            yd = ds.y(d_idx)
            r2 = h.grouped_cv(fp, np.asarray(d_idx, dtype=np.float64)[:, None], yd, ds.groups(d_idx), 5,
                              strata=ds.prefix_length(d_idx), dup_groups=ds.dup_groups(d_idx))
            res[cid] = {"oof_auroc_within_domain_rows": h.auroc(oof[dom], y[dom]),
                        "refit_domain_only_cv": {"auroc": r2["auroc"], "stratified_auroc": r2["stratified_auroc"]},
                        "within_tool": wt,
                        "within_tool_mean_auroc": float(np.mean([v["auroc"] for v in wt.values()])) if wt else None}
            print(tag, cid, json.dumps({k: v for k, v in res[cid].items() if k != "within_tool"}, default=float), flush=True)
        print(tag, json.dumps({k: res[k] for k in ("domain_rows", "domain_positives", "positives", "domain_indicator_auroc")}, default=float), flush=True)
        out[tag] = res
    p = ROOT / "artifacts/risk-loop/iter-02/train-cv/tool_confound.json"
    p.write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
