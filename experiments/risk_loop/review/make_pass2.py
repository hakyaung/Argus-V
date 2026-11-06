"""full-01 pass 2 selection: blind second review of
  A) every trajectory r1 flagged with action_violation=1,
  B) trajectories with a rule action positive that r1 did not flag (rule/r1 disagreement),
  C) a seeded uniform 10% of the remaining trajectories (estimates r1 false negatives).
Bundles are rebuilt per scenario with only the selected trajectories (same Q ids, r1 output not shown).

  .venv/bin/python experiments/risk_loop/review/make_pass2.py artifacts/label-review-v2/full-01
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    base = Path(sys.argv[1])
    mapping = json.loads((base / "mapping.json").read_text())
    r1 = {}
    for f in sorted((base / "reviews/r1").glob("S*.json")):
        for t in json.loads(f.read_text())["trajectories"]:
            r1[t["traj"]] = t
    bundles = {b.stem: json.loads(b.read_text()) for b in sorted((base / "bundles").glob("S*.json"))}
    missing = [q for b in bundles.values() for q in (t["traj"] for t in b["trajectories"]) if q not in r1]
    assert not missing, f"pass 1 incomplete: {len(missing)} trajectories"
    sel = {}
    for q, info in mapping["trajectories"].items():
        d = json.loads((ROOT / "artifacts/rollouts-v2" / info["rollout"] / "rollout.json").read_text())
        al = (d.get("operator_report") or {}).get("action_labels") or []
        rule_act = any(a.get("policy_violation_label") and a.get("violation_type") != "report_content" for a in al)
        r1_act = any(int(fl.get("action_violation") or 0) for fl in r1[q].get("flags", []))
        if r1_act:
            sel[q] = "A_r1_action"
        elif rule_act:
            sel[q] = "B_rule_only"
    rest = sorted(q for q in mapping["trajectories"] if q not in sel)
    rng = random.Random(20261107)
    for q in rng.sample(rest, round(0.10 * len(rest))):
        sel[q] = "C_random"
    out = base / "pass2"
    (out / "bundles").mkdir(parents=True, exist_ok=True)
    (out / "reviews").mkdir(exist_ok=True)
    n = 0
    for name, b in bundles.items():
        keep = [t for t in b["trajectories"] if t["traj"] in sel]
        if not keep:
            continue
        rng.shuffle(keep)
        used = {t["header"] for t in keep}
        (out / "bundles" / f"{name}.json").write_text(json.dumps(
            {"bundle": name, "headers": {k: v for k, v in b["headers"].items() if k in used}, "trajectories": keep},
            ensure_ascii=False, indent=0))
        n += len(keep)
    (out / "selection.json").write_text(json.dumps(sel, indent=0))
    from collections import Counter
    print(Counter(sel.values()), "trajectories", n, "bundles", len(list((out / "bundles").glob("*.json"))))


if __name__ == "__main__":
    main()
