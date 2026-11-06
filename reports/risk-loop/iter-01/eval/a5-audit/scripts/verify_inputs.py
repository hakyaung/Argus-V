"""A5 iter-01 step 1: hashes + D-v2a label contract re-derived from train/validation rollout.json files.
Reads only rollouts that appear in D-v2a (train/validation). Never opens test/unsplit rollouts or features."""
import csv, hashlib, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
OUT = Path(sys.argv[1])
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
res = {"hashes": {}}
res["hashes"]["harness"] = sha(ROOT / "experiments/risk_loop/harness.py")
res["hashes"]["harness_expected"] = "4e9278bc92ff4e808fa4fcacea3ea5313d6f78acda0b11f10bdd5c56e2db44b8"
res["hashes"]["d_v2a_samples"] = sha(ROOT / "artifacts/risk-loop/datasets/D-v2a/samples.csv")
res["hashes"]["d_v2a_samples_expected"] = "63e7cadfbbe1eb4feb2883db7effbcdf8ad6972e55ee2d3c089ff653a6730fa6"
res["hashes"]["d_v2_samples"] = sha(ROOT / "artifacts/risk-loop/datasets/D-v2/samples.csv")
res["hashes"]["iter01_candidates.py"] = sha(ROOT / "experiments/risk_loop/iter01_candidates.py")
res["hashes"]["build_d_v2a.py"] = sha(ROOT / "experiments/risk_loop/build_d_v2a.py")
for c in ("C-01a", "C-01b", "C-01c"):
    d = ROOT / "artifacts/risk-loop/iter-01/candidates" / c
    rep = json.loads((d / "cv_report.json").read_text())
    res["hashes"][c] = {"model_pkl": sha(d / "model.pkl"), "cv_report_model_sha256": rep["model_sha256"],
                        "match": sha(d / "model.pkl") == rep["model_sha256"]}

A = list(csv.DictReader(open(ROOT / "artifacts/risk-loop/datasets/D-v2a/samples.csv")))
S2 = list(csv.DictReader(open(ROOT / "artifacts/risk-loop/datasets/D-v2/samples.csv")))
sealed = {s["rollout"] for s in json.loads((ROOT / "artifacts/risk-loop/datasets/D-v2/sealed.json").read_text())}
res["d_v2a_splits"] = dict(Counter(a["split"] for a in A))
res["sealed_rollouts_in_d_v2a"] = sum(a["run_id"] in sealed for a in A)
res["sealed_rollouts_in_d_v2"] = sum(s["rollout"] in sealed for s in S2)

def tool_of(step):
    if step.get("kind") == "tool" and isinstance(step.get("command"), str):
        try: return json.loads(step["command"]).get("tool", "")
        except json.JSONDecodeError: return ""
    return step.get("kind") or ""

by = defaultdict(list)
for a in A: by[a["run_id"]].append(a)
errs, stats = [], Counter()
report_only_traj = 0
for rid, rows in sorted(by.items()):
    p = next((ROOT / r / rid / "rollout.json" for r in ("artifacts/rollouts-v2", "artifacts/rollouts-v1h2")
              if (ROOT / r / rid / "rollout.json").exists()), None)
    if p is None: errs.append(("missing_rollout", rid)); continue
    d = json.loads(p.read_text())
    steps = d["steps"]
    al = (d.get("operator_report") or {}).get("action_labels") or []
    tool_steps = [i for i, s in enumerate(steps) if s.get("kind") == "tool"]
    exp = {}  # step -> (keep, label)
    if al:
        stats["rollouts_with_action_labels"] += 1
        if len(al) != len(tool_steps): errs.append(("al_len", rid)); continue
        lab = dict(zip(tool_steps, al))
        for i, a in lab.items():
            if tool_of(steps[i]) != a["tool"]: errs.append(("al_tool", rid, i))
            # action_labels carry their own 1-based step_index? record relation
            stats[f"al_step_index_minus_rollout_step={a.get('step_index', -999) - i}"] += 1
        for k, s in enumerate(steps):
            a = lab.get(k)
            y = int(a["policy_violation_label"]) if a else int(s.get("policy_label") or 0)
            vt = (a or {}).get("violation_type") or ("action" if y else "")
            exp[k] = (not (y and vt == "report_content"), int(y and vt != "report_content"))
            if a is None and y: stats["positive_non_tool_step_in_v2"] += 1
    else:
        stats["rollouts_without_action_labels"] += 1
        for k, s in enumerate(steps):
            y = int(s.get("policy_label") or 0)
            rep = y and tool_of(s) == "submit_result"
            exp[k] = (not rep, int(y and not rep))
    got = {int(r["step_index"]): r for r in rows}
    kept = {k for k, (keep, _) in exp.items() if keep}
    if set(got) != kept: errs.append(("kept_steps_mismatch", rid, sorted(set(got) ^ kept)[:5]))
    first = min([k for k in kept if exp[k][1]], default=None)
    for k, r in got.items():
        if k not in exp: continue
        if int(r["label"]) != exp[k][1]: errs.append(("label", rid, k))
        if (r["trajectory_has_violation"] == "true") != (first is not None): errs.append(("tv", rid, k))
        afv = first is not None and k > first
        if (r["after_first_violation"] == "true") != afv: errs.append(("afv", rid, k))
        stats["rows_checked"] += 1
        stats[f"pos_{r['split']}"] += int(r["label"])
    if first is None and any(not keep for keep, _ in exp.values()): report_only_traj += 1
    # operator_report's own first-violation fields vs. ours (v2 only; they may count report_content violations)
    if al:
        own_first = [i for i, a in zip(tool_steps, al) if a.get("is_first_violation")]
        if own_first and first is not None and own_first[0] != first: stats["operator_first_violation_differs(report_content first)"] += 1
res["label_check"] = {"errors": errs[:30], "error_count": len(errs), "stats": dict(stats),
                      "report_only_trajectories_treated_as_normal": report_only_traj}
# afv semantics: rows (incl. negatives) after first action violation
res["after_first_violation_rows"] = {sp: {"all": sum(a["split"] == sp and a["after_first_violation"] == "true" for a in A),
                                          "positives": sum(a["split"] == sp and a["after_first_violation"] == "true" and a["label"] == "1" for a in A)}
                                     for sp in ("train", "validation")}
OUT.write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
