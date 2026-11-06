"""A5 iter-01: assemble eval/C-01x.json from saved outputs (no new computation on validation except a
version (v1 vs v2) leakage AUROC from the already-saved scores)."""
import csv, json, sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
ROOT = Path(__file__).resolve().parents[6]
EV = ROOT / "artifacts/risk-loop/iter-01/eval"; AUD = EV / "a5-audit"
S = list(csv.DictReader(open(ROOT / "artifacts/risk-loop/datasets/D-v2a/samples.csv")))
val = np.load(AUD / "val_rows.npy").tolist(); ver = np.array([S[r]["version"] == "v1" for r in val])
g0 = json.loads((AUD / "g0.json").read_text()); ind = json.loads((AUD / "independent.json").read_text())
ver_in = json.loads((AUD / "verify_inputs.json").read_text())
sh = json.loads((AUD / "shuffle_supplement.json").read_text()) if (AUD / "shuffle_supplement.json").exists() else {}
looks = [json.loads(l) for l in open(AUD / "validation_looks.jsonl")]
for cid in ("C-01a", "C-01b", "C-01c"):
    s = np.load(AUD / f"val_scores_{cid}.npy")
    out = {"candidate": cid, "dataset": "D-v2a", "evaluator": "A5 (independent instance)",
           "hashes": {"harness": ver_in["hashes"]["harness"], "samples_csv": ver_in["hashes"]["d_v2a_samples"],
                      "model_pkl": ver_in["hashes"][cid]["model_pkl"], "model_hash_matches_cv_report": ver_in["hashes"][cid]["match"],
                      "iter01_candidates.py": ver_in["hashes"]["iter01_candidates.py"]},
           "validation_look": next(l for l in looks if l["candidate"] == cid),
           "G0": {k: g0[cid][k] for k in ("own_fold_x_tertile", "harness_fold_x_tertile", "a4_cv_stratified_auroc", "G0_threshold", "G0_pass",
                                          "own_fold_stratified", "harness_cv_auroc", "per_fold_auroc")} |
                 {"length+step_fold_x_tertile": g0["length+step(A4: global scaler, balanced)"]["own_fold_x_tertile"],
                  "length_only_fold_x_tertile": g0["length_only(fold scaler, unbalanced)"]["own_fold_x_tertile"]},
           "evaluate_validation": json.loads((AUD / f"evaluate_validation_{cid}.json").read_text()),
           "audits": json.loads((AUD / f"audits_{cid}.json").read_text()),
           "independent": ind["candidates"][cid],
           "independent_baselines": ind["baselines"], "validation_counts": ind["validation"],
           "version_leakage_auroc_v1_vs_v2": float(roc_auc_score(ver, s)),
           "label_shuffle_supplement": {k: v for k, v in sh.get(cid, {}).items() if k != "reps"}}
    (EV / f"{cid}.json").write_text(json.dumps(out, indent=1, default=float))
    print(cid, "version leak", out["version_leakage_auroc_v1_vs_v2"])
