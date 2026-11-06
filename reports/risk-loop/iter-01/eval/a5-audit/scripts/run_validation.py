"""A5 iter-01 step 3: exactly ONE validation look per G0-passing candidate (frozen model.pkl, no refit/tuning).
Scores are saved once (refuses to overwrite) and reused by every validation audit and the independent recompute."""
import hashlib, json, pickle, sys, datetime
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parents[6]; sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as H
from experiments.risk_loop import iter01_candidates as C
EV = ROOT / "artifacts/risk-loop/iter-01/eval"; AUD = EV / "a5-audit"
g0 = json.loads((AUD / "g0.json").read_text())
ds = H.load("D-v2a"); val = list(ds.val_idx)
assert H.harness_sha256() == "4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8"
looks = AUD / "validation_looks.jsonl"
for cid in sys.argv[1:]:
    if not g0[cid]["G0_pass"]:
        print(cid, "G0 FAIL -> not scored on validation"); continue
    sp = AUD / f"val_scores_{cid}.npy"
    assert not sp.exists(), f"{cid} already looked at; refusing a second look"
    mp = ROOT / f"artifacts/risk-loop/iter-01/candidates/{cid}/model.pkl"
    msha = hashlib.sha256(mp.read_bytes()).hexdigest()
    assert msha == json.loads((mp.parent / "cv_report.json").read_text())["model_sha256"]
    model = pickle.load(open(mp, "rb"))
    scores = np.asarray(C.score(model, ds, val), dtype=np.float64)
    np.save(sp, scores); np.save(AUD / "val_rows.npy", np.asarray(val))
    ev = H.evaluate_validation(dict(zip(val, scores.tolist())), ds)
    with open(looks, "a") as fh:
        fh.write(json.dumps({"candidate": cid, "time": datetime.datetime.now().isoformat(), "model_sha256": msha,
                             "dataset": "D-v2a", "harness_sha256": ev["harness_sha256"], "look_index_iter01": sum(1 for _ in open(looks)) + 1 if looks.exists() else 1}) + "\n")
    (AUD / f"evaluate_validation_{cid}.json").write_text(json.dumps(ev, indent=1, default=float))
    print(cid, "auroc", ev["auroc"], ev["auroc_ci"], "G1", ev["G1"]["delta"], ev["G1"]["ci_low"], ev["G1"]["ci_high"],
          "G2", ev["G2"]["stratified_auroc"], ev["G2"]["ci_low"], "gates", ev["gates"], flush=True)
