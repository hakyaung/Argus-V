"""Unblind and compare the two blind reviewers with the rule labels (run after all reviews exist).

Outputs <dir>/comparison.json and <dir>/disagreements.json (human queue):
  - inter-reviewer agreement (step level, action_violation and report_violation; Cohen's kappa)
  - consensus (both 1 -> 1, both 0 -> 0, else 'disputed') vs rule label, per pool
  - rule miss-rate estimate from the random pool R, weighted up to the full train/val population
  .venv/bin/python experiments/risk_loop/review/compare_reviews.py artifacts/label-review-v2/pilot-01
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def rule_labels(rollout: str):
    d = json.loads((ROOT / "artifacts/rollouts-v2" / rollout / "rollout.json").read_text())
    tool_steps = [i for i, s in enumerate(d["steps"]) if s.get("kind") == "tool"]
    al = (d.get("operator_report") or {}).get("action_labels") or []
    out = {}
    for i, s in enumerate(d["steps"]):
        out[i] = {"rule": int(s.get("policy_label") or 0), "rule_type": None, "kind": s.get("kind"),
                  "oracle_reason": s.get("oracle_reason")}
    for i, a in zip(tool_steps, al):
        out[i]["rule_type"] = a.get("violation_type")
    return out


def main() -> None:
    base = Path(sys.argv[1])
    sel = json.loads((base / "selection.json").read_text())
    reviewers = sorted(p.name for p in (base / "reviews").iterdir() if p.is_dir())
    assert len(reviewers) == 2, reviewers
    rows, missing = [], []
    for it in sel["items"]:
        pid = it["packet_id"]
        rv = {}
        for r in reviewers:
            f = base / "reviews" / r / f"{pid}.json"
            if not f.exists():
                missing.append(f"{r}/{pid}")
                continue
            rv[r] = {int(s["step_index"]): s for s in json.loads(f.read_text())["steps"]}
        if len(rv) < 2:
            continue
        rl = rule_labels(it["rollout"])
        for k, lab in rl.items():
            a, b = rv[reviewers[0]].get(k), rv[reviewers[1]].get(k)
            if a is None or b is None:
                missing.append(f"{pid}:{k}")
                continue
            rows.append({"packet": pid, "rollout": it["rollout"], "pool": it["pool"], "split": it["split"],
                         "family_id": it["family_id"], "step_index": k, "kind": lab["kind"], "rule": lab["rule"],
                         "rule_type": lab["rule_type"], "oracle_reason": lab["oracle_reason"],
                         "a_act": int(a.get("action_violation") or 0), "b_act": int(b.get("action_violation") or 0),
                         "a_rep": int(a.get("report_violation") or 0), "b_rep": int(b.get("report_violation") or 0),
                         "a": a, "b": b})
    rule_act = lambda r: int(r["rule"] == 1 and r["rule_type"] != "report_content")
    rule_rep = lambda r: int(r["rule"] == 1 and r["rule_type"] == "report_content")
    out = {"reviewers": reviewers, "missing": missing, "steps": len(rows),
           "agreement": {"action_kappa": kappa([(r["a_act"], r["b_act"]) for r in rows]),
                         "action_raw": sum(r["a_act"] == r["b_act"] for r in rows) / max(len(rows), 1),
                         "report_kappa": kappa([(r["a_rep"], r["b_rep"]) for r in rows]),
                         "a_action_pos": sum(r["a_act"] for r in rows), "b_action_pos": sum(r["b_act"] for r in rows)},
           "per_pool": {}}
    disputes = []
    for pool in ("T1", "T2", "T3", "R"):
        pr = [r for r in rows if r["pool"] == pool]
        c = Counter()
        for r in pr:
            cons = r["a_act"] if r["a_act"] == r["b_act"] else "disputed"
            c[f"rule_act={rule_act(r)} review_act={cons}"] += 1
            consr = r["a_rep"] if r["a_rep"] == r["b_rep"] else "disputed"
            c[f"rule_rep={rule_rep(r)} review_rep={consr}"] += 1
            if cons == "disputed" or (cons != rule_act(r)) or consr == "disputed" or consr != rule_rep(r):
                disputes.append({k: r[k] for k in ("packet", "rollout", "pool", "split", "step_index", "kind", "rule",
                                                   "rule_type", "oracle_reason")}
                                | {"review_act": cons, "review_rep": consr,
                                   "a_evidence": r["a"].get("evidence"), "b_evidence": r["b"].get("evidence"),
                                   "a_type": r["a"].get("type"), "b_type": r["b"].get("type")})
        traj = defaultdict(lambda: [0, 0])
        for r in pr:
            traj[r["rollout"]][0] |= int(r["a_act"] == 1 and r["b_act"] == 1 and not rule_act(r))
            traj[r["rollout"]][1] |= rule_act(r)
        out["per_pool"][pool] = {"trajectories": len({r["rollout"] for r in pr}), "steps": len(pr),
                                 "pool_size": next((it["pool_size"] for it in sel["items"] if it["pool"] == pool), 0),
                                 "trajectories_with_new_consensus_action_violation": sum(v[0] for v in traj.values()),
                                 "counts": dict(sorted(c.items()))}
    R = out["per_pool"].get("R", {})
    if R.get("trajectories"):
        rate = R["trajectories_with_new_consensus_action_violation"] / R["trajectories"]
        out["random_pool_miss_rate_traj"] = rate
        out["random_pool_extrapolated_missed_trajectories"] = rate * R["pool_size"]
    (base / "comparison.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    (base / "disagreements.json").write_text(json.dumps(disputes, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in out.items() if k != "missing"} | {"missing": len(missing),
                                                                          "disagreement_rows": len(disputes)},
                     indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
