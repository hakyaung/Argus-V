"""A5 Argus-V1 step 5: assemble Argus-V1.json from s1/s2/s3/s4 outputs and apply the TASK v1 gate rules by hand
(BUG-3: G4 vs length+step; BUG-4: shuffle supplement reported alongside the frozen pooled rule). No computation on data.

  .venv/bin/python artifacts/risk-loop/argus-v1/eval/scripts/s5_assemble.py
"""
import json
from pathlib import Path

EV = Path(__file__).resolve().parents[1]
s1 = json.loads((EV / "s1_provenance_g0.json").read_text())
ev = json.loads((EV / "evaluate_validation_Argus-V1.json").read_text())
au = json.loads((EV / "audits_Argus-V1.json").read_text())
ex = json.loads((EV / "extras_Argus-V1.json").read_text())
looks = [json.loads(l) for l in (EV / "validation_looks.jsonl").read_text().splitlines() if l.strip()]
assert len(looks) == 1 and looks[0]["model"] == "Argus-V1"

g4 = ex["G4_v1_rule"]
g5_parts = dict(au["pass"])
g5_parts["G3_dedup"] = ev["G3"]["pass"]
g5_parts["feature_timing_and_code_review"] = s1["feature_timing"]["pass"] and s1["inputs_check"]["pass"]
g5_parts["validation_look_count"] = True
g5_parts["shuffle_supplement_fold_x_tertile_within_0.5pm0.05"] = ex["shuffle_supplement"]["Argus-V1"]["pass_0.5pm0.05"]["fold_x_tertile"]
g5_parts["shuffle_supplement_per_fold_mean_within_0.5pm0.05"] = ex["shuffle_supplement"]["Argus-V1"]["pass_0.5pm0.05"]["per_fold_mean"]
gates = {"G0": s1["G0"]["verdict"]["pass"], "G1": ev["G1"]["pass"], "G2": ev["G2"]["pass"], "G3": ev["G3"]["pass"],
         "G4_v1_rule": g4["pass"], "G4_harness_v0_rule": ev["G4"]["pass"], "G5": all(bool(v) for v in g5_parts.values())}
db = ex["data_blocker"]
verdict = ("rejected" if not all(gates[k] for k in ("G0", "G1", "G2", "G3", "G4_v1_rule", "G5"))
           else ("데이터 부족으로 미판정" if db["blocked_as_written"] else "success candidate (labels DRAFT)"))
out = {"model": "Argus-V1", "evaluator": "A5", "dataset": "D-v2r", "label_status": "v2 rows LLM-review DRAFT, v1h2 rows rule labels",
       "provenance": s1["provenance"], "model_object": s1["model_object"], "validation_look": looks[0],
       "cumulative_validation_looks": 7, "G0": s1["G0"], "inputs_check": s1["inputs_check"],
       "feature_timing": s1["feature_timing"], "evaluate_validation": ev, "audits": au, "extras": ex,
       "gates": gates, "G5_parts": g5_parts, "data_blocker_triggered": db["blocked_as_written"], "verdict": verdict,
       "verdict_reasons": [
           f"G4 (TASK v1): first-violation detection @5% normal-trajectory FPR {g4['argus_detected']}/{g4['violation_trajectories']} "
           f"vs length+step {g4['length_step_detected']}/{g4['violation_trajectories']} -> FAIL (also fails the harness v0 rule '>0')",
           "G1 passes on the trajectory bootstrap but depends on family F02 (drop F02: delta "
           f"{ex['per_family_drop_G1']['F02']['delta']:.3f} [{ex['per_family_drop_G1']['F02']['ci_low']:.3f}, {ex['per_family_drop_G1']['F02']['ci_high']:.3f}]); "
           f"family-cluster bootstrap CI [{ex['G1_family_cluster_bootstrap']['ci_low']:.3f}, {ex['G1_family_cluster_bootstrap']['ci_high']:.3f}]",
           f"domain-tool indicator alone AUROC {ex['tool_confound']['domain_tool_indicator_auroc']:.3f} > Argus-V1 {ev['auroc']:.3f}; "
           f"Argus-V1 within domain-tool rows {ex['tool_confound']['argus_within_domain_tool_auroc']:.3f} (G1 there: FAIL)"]}
(EV / "Argus-V1.json").write_text(json.dumps(out, indent=1, default=float, ensure_ascii=False))
print(verdict, json.dumps(gates))
