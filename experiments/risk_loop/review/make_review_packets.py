"""Blind review packets for v2 action-violation review (pilot).

Selection (train/validation only; test split never opened):
  targeted  T1 rule action positives (confirm)    T2 trajectories with bash steps (no rule judgement)
            T3 obstacle hit (BCK-reachable NOT_RUN) and business_completed, no rule action positive
  random    R  uniform over all remaining train/validation trajectories (estimates the rule miss rate)
Packets hide every label-bearing field: policy_label, decision, oracle_reason, effect/static findings,
operator_report, scenario_brief title/DP, report. Reviewers see task, policy, tools, files, rules and
each step's command + tool result. The pool membership is kept in selection.json (not given to reviewers).

  .venv/bin/python experiments/risk_loop/review/make_review_packets.py --out artifacts/label-review-v2/pilot-01
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QUEUE = ROOT / "artifacts/run-queue-20261102/queue.json"
CAP = 2500


def trunc(x, n=CAP):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + f"...[truncated {len(s) - n} chars]"


def packet(d: dict) -> dict:
    pc = d.get("public_context") or {}
    steps = []
    for s in d["steps"]:
        result = s.get("tool_result") or s.get("execution")
        if s.get("kind") == "bash" and isinstance(s.get("execution"), dict):
            ex = s["execution"]
            logs = Path(ex.get("logs") or "")
            io = {}
            for name in ("stdout", "stderr"):
                f = logs / f"{name}.log"
                io[name] = f.read_text(errors="replace") if f.is_file() else None
            result = {"exit_code": ex.get("exit_code"), "status": ex.get("status"),
                      "stdout": trunc(io["stdout"] or "", CAP), "stderr": trunc(io["stderr"] or "", 800)}
        steps.append({"step_index": int(s["step_index"]), "kind": s.get("kind"),
                      "command": trunc(s.get("command")), "result": trunc(result)})
    return {"rollout": None, "task": d.get("task"), "policy": pc.get("policy") or d.get("trusted_policy"),
            "tools": pc.get("tools"), "files": pc.get("files"),
            "workspace_rules": {k: (d.get("rules") or {}).get(k) for k in ("writable", "forbidden", "forbid_network")},
            "steps": steps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", default="T1=12,T2=10,T3=13,R=25")
    ap.add_argument("--seed", type=int, default=20261105)
    ap.add_argument("--exclude", type=Path, nargs="*", default=[], help="selection.json files already reviewed")
    args = ap.parse_args()
    want = {k: int(v) for k, v in (p.split("=") for p in args.n.split(","))}
    q = {(e["scenario_id"], e["condition"], e["seed"]): e
         for e in json.loads(QUEUE.read_text())["queue"] if e["type"] == "rollout" and e["output_root"] == "artifacts/rollouts-v2"}
    done = set()
    for ex in args.exclude:
        done |= {r["rollout"] for r in json.loads(ex.read_text())["items"]}
    pools = {"T1": [], "T2": [], "T3": [], "R": []}
    info = {}
    for f in sorted((ROOT / "artifacts/rollouts-v2").glob("*/rollout.json")):
        d = json.loads(f.read_text())
        if "report" not in d:
            continue
        e = q[(d["scenario_id"], d["condition"], d["seed"])]
        if e.get("split") not in ("train", "validation"):
            continue
        name = f.parent.name
        if name in done:
            continue
        o = d.get("operator_report") or {}
        al = o.get("action_labels") or []
        act = any(a.get("policy_violation_label") and a.get("violation_type") != "report_content" for a in al)
        bash = any(s.get("kind") == "bash" for s in d["steps"])
        hit = any(s.get("oracle_reason") == "business_check:BCK-reachable" for s in d["steps"])
        info[name] = {"split": e["split"], "family_id": e.get("family_id"), "scenario_id": d["scenario_id"],
                      "condition": d["condition"], "rule_action_positive": act, "has_bash": bash,
                      "obstacle_hit": hit, "business_completed": o.get("business_completed")}
        if act:
            pools["T1"].append(name)
        elif bash:
            pools["T2"].append(name)
        elif hit and o.get("business_completed"):
            pools["T3"].append(name)
        else:
            pools["R"].append(name)
    rng = random.Random(args.seed)
    items = []
    for k in ("T1", "T2", "T3", "R"):
        pick = rng.sample(pools[k], min(want.get(k, 0), len(pools[k])))
        items += [{"rollout": n, "pool": k, "pool_size": len(pools[k]), **info[n]} for n in pick]
    rng.shuffle(items)
    out = args.out
    (out / "packets").mkdir(parents=True, exist_ok=True)
    for i, it in enumerate(items):
        it["packet_id"] = f"P{i:03d}"
        d = json.loads((ROOT / "artifacts/rollouts-v2" / it["rollout"] / "rollout.json").read_text())
        p = packet(d)
        p["rollout"] = it["packet_id"]
        (out / "packets" / f"{it['packet_id']}.json").write_text(json.dumps(p, ensure_ascii=False, indent=1))
    (out / "selection.json").write_text(json.dumps({"seed": args.seed, "pool_sizes": {k: len(v) for k, v in pools.items()},
                                                    "items": items}, ensure_ascii=False, indent=1))
    print({k: len(v) for k, v in pools.items()}, len(items))


if __name__ == "__main__":
    main()
