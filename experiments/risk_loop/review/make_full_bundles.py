"""Full v2 review, pass 1: one blind bundle per scenario (train/validation only), shared headers once.

Each bundle: {"bundle": "S###", "headers": {"H1": {task, policy, tools, files, workspace_rules}, ...},
              "trajectories": [{"traj": "Q####", "header": "H1", "steps": [...]}, ...]}
Trajectories already reviewed in the pilot are skipped. Anonymous IDs; mapping in mapping.json (not for reviewers).

  .venv/bin/python experiments/risk_loop/review/make_full_bundles.py --out artifacts/label-review-v2/full-01 \
      --exclude artifacts/label-review-v2/pilot-01/selection.json
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_review_packets import QUEUE, ROOT, packet  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--exclude", type=Path, nargs="*", default=[])
    ap.add_argument("--seed", type=int, default=20261106)
    args = ap.parse_args()
    q = {(e["scenario_id"], e["condition"], e["seed"]): e
         for e in json.loads(QUEUE.read_text())["queue"] if e["type"] == "rollout" and e["output_root"] == "artifacts/rollouts-v2"}
    done = set()
    for ex in args.exclude:
        done |= {r["rollout"] for r in json.loads(ex.read_text())["items"]}
    by_s: dict[str, list] = {}
    for f in sorted((ROOT / "artifacts/rollouts-v2").glob("*/rollout.json")):
        d = json.loads(f.read_text())
        if "report" not in d:
            continue
        e = q[(d["scenario_id"], d["condition"], d["seed"])]
        if e.get("split") not in ("train", "validation") or f.parent.name in done:
            continue
        by_s.setdefault(d["scenario_id"], []).append((f.parent.name, e, packet(d)))
    rng = random.Random(args.seed)
    scen = sorted(by_s)
    rng.shuffle(scen)
    (args.out / "bundles").mkdir(parents=True, exist_ok=True)
    mapping, qn = {"bundles": {}, "trajectories": {}}, 0
    for bi, sid in enumerate(scen):
        items = by_s[sid]
        rng.shuffle(items)
        headers, hid_of, trajs = {}, {}, []
        for name, e, p in items:
            head = {k: p[k] for k in ("task", "policy", "tools", "files", "workspace_rules")}
            key = json.dumps(head, ensure_ascii=False, sort_keys=True)
            if key not in hid_of:
                hid_of[key] = f"H{len(hid_of) + 1}"
                headers[hid_of[key]] = head
            qid = f"Q{qn:04d}"
            qn += 1
            trajs.append({"traj": qid, "header": hid_of[key], "steps": p["steps"]})
            mapping["trajectories"][qid] = {"rollout": name, "bundle": f"S{bi:03d}", "split": e["split"],
                                            "family_id": e.get("family_id"), "scenario_id": sid}
        mapping["bundles"][f"S{bi:03d}"] = {"scenario_id": sid, "n": len(trajs)}
        (args.out / "bundles" / f"S{bi:03d}.json").write_text(
            json.dumps({"bundle": f"S{bi:03d}", "headers": headers, "trajectories": trajs}, ensure_ascii=False, indent=0))
    (args.out / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1))
    print(len(scen), "bundles,", qn, "trajectories")


if __name__ == "__main__":
    main()
