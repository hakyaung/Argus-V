"""D-v2a: harness-compatible view of D-v2 for the action-violation target (iteration 1).

Rows = D-v2 train/validation step samples whose label_kind is '' (normal) or 'action'. report_content
positives are dropped (rejected as a target, LEDGER iter-01). Labels, trajectory_has_violation and
after_first_violation are recomputed for action violations only. Features stay in D-v2/obs_L*.npy;
the column ``d2_sample`` points at the D-v2 sample (its observation rows are listed in D-v2/obs_rows.csv).
No feature array is read here. Test/unsplit rollouts are not part of D-v2 at all.

  .venv/bin/python experiments/risk_loop/build_d_v2a.py
"""
from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "artifacts/risk-loop/datasets/D-v2"
OUT = ROOT / "artifacts/risk-loop/datasets/D-v2a"

COLS = ["row", "snapshot_id", "run_id", "trajectory_id", "action_id", "step_index", "family_id", "task_group_id",
        "split", "label", "trajectory_has_violation", "token_index", "prefix_length", "prefix_hash", "dup_group",
        "is_dup_representative", "after_first_violation", "condition", "seed", "version", "tool", "d2_sample"]


def main() -> None:
    src = [s for s in csv.DictReader(open(SRC / "samples.csv"))
           if s["split"] in ("train", "validation") and s["label_kind"] in ("", "action")]
    by_traj = defaultdict(list)
    for s in src:
        by_traj[s["rollout"]].append(s)
    traj_viol = {t: any(x["label_kind"] == "action" for x in ss) for t, ss in by_traj.items()}
    dup_of: dict[str, int] = {}
    rows = []
    seen_first: dict[str, bool] = defaultdict(bool)
    for s in sorted(src, key=lambda s: (s["rollout"], int(s["step_index"]))):
        t = s["rollout"]
        y = int(s["label_kind"] == "action")
        g = dup_of.setdefault(s["prefix_sha256"], len(dup_of))
        rows.append({
            "row": len(rows), "snapshot_id": f"{t}:{s['step_index']}", "run_id": t, "trajectory_id": t,
            "action_id": f"{t}:{s['step_index']}", "step_index": s["step_index"], "family_id": s["family_id"],
            "task_group_id": s["scenario_id"], "split": s["split"], "label": y,
            "trajectory_has_violation": str(traj_viol[t]).lower(), "token_index": int(s["prefix_length"]) - 1,
            "prefix_length": s["prefix_length"], "prefix_hash": s["prefix_sha256"], "dup_group": g,
            "is_dup_representative": "", "after_first_violation": str(seen_first[t]).lower(),
            "condition": s["condition"], "seed": s["seed"], "version": s["version"], "tool": s["tool"],
            "d2_sample": s["sample"]})
        seen_first[t] = seen_first[t] or bool(y)
    first = set()
    for r in rows:  # representative = first row of each dup group within its split (label conflicts reported below)
        key = (r["split"], r["dup_group"])
        r["is_dup_representative"] = "true" if key not in first else "false"
        first.add(key)
    conflicts = defaultdict(set)
    for r in rows:
        conflicts[(r["split"], r["dup_group"])].add(r["label"])
    n_conf = sum(len(v) > 1 for v in conflicts.values())
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "samples.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader(); w.writerows(rows)
    np.save(OUT / "rows_tv.npy", np.arange(len(rows), dtype=np.int64))
    sha = hashlib.sha256((OUT / "samples.csv").read_bytes()).hexdigest()
    stats = {}
    for sp in ("train", "validation"):
        rr = [r for r in rows if r["split"] == sp]
        stats[sp] = {"rows": len(rr), "positives": sum(r["label"] for r in rr),
                     "positive_families": len({r["family_id"] for r in rr if r["label"]}),
                     "violation_trajectories": len({r["trajectory_id"] for r in rr if r["label"]}),
                     "normal_trajectories": len({r["trajectory_id"] for r in rr if r["trajectory_has_violation"] == "false"}),
                     "dedup_rows": sum(r["is_dup_representative"] == "true" for r in rr)}
    (OUT / "card.md").write_text(
        "# D-v2a (action-violation view of D-v2)\n\n"
        f"Built by `experiments/risk_loop/build_d_v2a.py` from D-v2 samples.csv. samples.csv sha256 `{sha}`.\n"
        "Features: D-v2/obs_L{11,16,23}.npy via `d2_sample` (see experiments/risk_loop/iter01_candidates.py).\n"
        f"Dup groups with conflicting labels: {n_conf}.\n\n"
        + "\n".join(f"- {k}: {v}" for k, v in stats.items()) + "\n")
    print(sha, stats, "conflicting dup groups", n_conf)


if __name__ == "__main__":
    main()
