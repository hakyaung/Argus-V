"""A5 Argus-V1 step 3: harness.audits (TASK §5) using the saved validation scores from step 2 (no new look).
Label shuffle n_shuffle=20 (as in the iter-01 A5 audit), seed 0, bootstrap 2000, inputs ["activation:L16"].

  CUDA_VISIBLE_DEVICES= .venv/bin/python artifacts/risk-loop/argus-v1/eval/scripts/s3_audits.py
"""
import json, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[5]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H  # noqa: E402
from experiments.risk_loop import argus_models as am  # noqa: E402

EV = ROOT / "artifacts/risk-loop/argus-v1/eval"
ds = H.load("D-v2r")
val = np.load(EV / "val_rows.npy").tolist(); s = np.load(EV / "val_scores_Argus-V1.npy")
assert val == list(ds.val_idx)
rep = H.audits(am.cv_fit_predict("Argus-V1", ds), ds, lambda ds_, rows: np.asarray(rows, float)[:, None],
               input_names=["activation:L16"], scores_by_row=dict(zip(val, s.tolist())), n_shuffle=20, seed=0,
               bootstrap=2000)
(EV / "audits_Argus-V1.json").write_text(json.dumps(rep, indent=1, default=float))
print(json.dumps(rep, indent=1, default=float))
