"""A5 iter-01 step 4: harness §5 audits per candidate, reusing the saved validation scores (no new look).
Also: G4 vs length+step baseline (TASK v1 G4; the frozen harness only tests detection > 0)."""
import json, sys
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[6]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H
from experiments.risk_loop import iter01_candidates as C
AUD = ROOT / "artifacts/risk-loop/iter-01/eval/a5-audit"
ds = H.load("D-v2a"); val = list(ds.val_idx)
assert np.load(AUD / "val_rows.npy").tolist() == val
for cid in sys.argv[1:]:
    sv = np.load(AUD / f"val_scores_{cid}.npy")
    fp, featurizer = C.fit_predict_for_audit(cid, ds)
    a = H.audits(fp, ds, featurizer, input_names=[f"activation:L{C.CANDIDATES[cid]['layer']}"],
                 scores_by_row=dict(zip(val, sv.tolist())), n_shuffle=20, seed=0, bootstrap=2000)
    (AUD / f"audits_{cid}.json").write_text(json.dumps(a, indent=1, default=float))
    print(cid, json.dumps({"shuffle_mean": a["label_shuffle"]["mean"], "pass": a["pass"], "len_corr": a["length_correlation"],
                           "leak": a["leakage"]}, default=float), flush=True)
# baseline budgets (length+step / length_only), same harness convention
y = ds.y(val); traj = ds.trajectories(val); step = ds.step_index(val); pl = ds.prefix_length(val)
tv = np.asarray([bool(ds.samples[r]["trajectory_has_violation"]) for r in val])
base = H.baseline_scores(ds, val)
out = {k: [H.budget_metrics(v, y, traj, tv, step, pl, b) for b in (0.05, 0.10, 0.20)] for k, v in base.items()}
(AUD / "baseline_budgets.json").write_text(json.dumps(out, indent=1, default=float))
print({k: [(b["first_violation_detected"], b["violation_trajectories"], b["normal_trajectory_fpr"]) for b in v] for k, v in out.items()})
