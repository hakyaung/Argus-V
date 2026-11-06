"""A5 Argus-V1 step 2: THE single validation look (cumulative project look #7).

Loads the frozen model.pkl (sha256 checked), scores every D-v2r validation row once with argus_models.score, saves the
scores (val_scores_Argus-V1.npy + val_rows.npy), appends one line to validation_looks.jsonl, then runs
harness.evaluate_validation. Refuses to run if a look for Argus-V1 is already logged. No refit, no tuning.
All later steps reuse the saved scores.

  CUDA_VISIBLE_DEVICES= .venv/bin/python artifacts/risk-loop/argus-v1/eval/scripts/s2_validation_look.py
"""
import datetime, json, pickle, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[5]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H  # noqa: E402
from experiments.risk_loop import argus_models as am  # noqa: E402

EV = ROOT / "artifacts/risk-loop/argus-v1/eval"
MODEL = ROOT / "artifacts/risk-loop/argus-v1/model/model.pkl"
LOG = EV / "validation_looks.jsonl"
MODEL_SHA = "a43fd7508b6741a50f9fc715d905ff56d77b7ca8d7ccac953da8245394b14f0f"
HARNESS_SHA = "4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8"


def main():
    if LOG.exists() and any(json.loads(l).get("model") == "Argus-V1" for l in LOG.read_text().splitlines() if l.strip()):
        raise SystemExit("Argus-V1 validation look already logged; refusing a second look")
    assert H.harness_sha256() == HARNESS_SHA, "harness hash mismatch"
    assert H.file_sha256(MODEL) == MODEL_SHA, "model.pkl hash mismatch"
    ds = H.load("D-v2r")
    val = [s["row"] for s in ds.samples if s["split"] == "validation"]
    assert val == list(ds.val_idx)
    with open(MODEL, "rb") as f:
        model = pickle.load(f)
    s = np.asarray(am.score(model, ds, val), dtype=np.float64)
    np.save(EV / "val_rows.npy", np.asarray(val, np.int64))
    np.save(EV / "val_scores_Argus-V1.npy", s)
    rec = {"model": "Argus-V1", "time": datetime.datetime.now().isoformat(), "model_sha256": MODEL_SHA,
           "dataset": "D-v2r", "samples_sha256": H.file_sha256(ds.root / "samples.csv"), "harness_sha256": HARNESS_SHA,
           "n_rows_scored": len(val), "scores_sha256": H.file_sha256(EV / "val_scores_Argus-V1.npy"),
           "cumulative_project_look": 7, "looks_this_model": 1, "evaluator": "A5"}
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
    rep = H.evaluate_validation(dict(zip(val, s.tolist())), ds)
    (EV / "evaluate_validation_Argus-V1.json").write_text(json.dumps(rep, indent=1, default=float))
    print(json.dumps({k: rep[k] for k in ("n", "positives", "auroc", "auroc_ci", "G1", "gates")}, indent=1, default=float))
    print(json.dumps(rep["G2"], default=float)); print(json.dumps(rep["G4"], default=float))
    print(json.dumps(rep["budgets"], default=float)); print(json.dumps(rep["baselines"], default=float))


if __name__ == "__main__":
    main()
