"""D-v2r: D-v2 relabelled with the LLM review DRAFT (labels-draft-v1) for the substantive action-violation target.

v2 rows (reviewed):
  label 1  review_action == 1 and action_kind == 'substantive'
  label 0  review_action == 0 (report-only flags stay 0: the target is the action)
  dropped  review_action == 'disputed', or review_action == 1 with kind procedure_skip / output_falsification
           (kept out of the target to avoid the "next action is submit" confound; not negatives either)
v1 rows (not reviewed): original D-v2 label_kind ('action' -> 1, '' -> 0, 'report' -> dropped), as in D-v2a.
trajectory_has_violation is true when the trajectory has ANY action positive or disputed step (any kind),
so 'normal' trajectories contain no reviewed violation at all. after_first_violation follows the target label.
STATUS: DRAFT labels (LLM review, not human-confirmed).

  .venv/bin/python experiments/risk_loop/build_d_v2r.py
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "artifacts/risk-loop/datasets/D-v2"
LAB = ROOT / "artifacts/label-review-v2/labels-draft-v1/step_labels.csv"
OUT = ROOT / "artifacts/risk-loop/datasets/D-v2r"

COLS = ["row", "snapshot_id", "run_id", "trajectory_id", "action_id", "step_index", "family_id", "task_group_id",
        "split", "label", "trajectory_has_violation", "token_index", "prefix_length", "prefix_hash", "dup_group",
        "is_dup_representative", "after_first_violation", "condition", "seed", "version", "tool", "d2_sample",
        "label_source"]


def main() -> None:
    lab = {(r["rollout"], int(r["step_index"])): r for r in csv.DictReader(open(LAB))}
    src = [s for s in csv.DictReader(open(SRC / "samples.csv")) if s["split"] in ("train", "validation")]
    keep, drop = [], Counter()
    traj_any = defaultdict(bool)
    for s in src:
        key = (s["rollout"], int(s["step_index"]))
        if s["version"] == "v2":
            r = lab[key]  # every v2 train/val step is reviewed (KeyError = coverage bug)
            a, k = r["review_action"], r["action_kind"]
            traj_any[s["rollout"]] |= a in ("1", "disputed")
            if a == "disputed" or (a == "1" and k != "substantive"):
                drop[f"v2:{'disputed' if a == 'disputed' else k}"] += 1
                continue
            keep.append((s, int(a == "1"), "review_draft"))
        else:
            traj_any[s["rollout"]] |= s["label_kind"] == "action"
            if s["label_kind"] == "report":
                drop["v1:report"] += 1
                continue
            keep.append((s, int(s["label_kind"] == "action"), "v1_rule"))
    dup_of: dict[str, int] = {}
    rows = []
    seen_first: dict[str, bool] = defaultdict(bool)
    for s, y, srcname in sorted(keep, key=lambda t: (t[0]["rollout"], int(t[0]["step_index"]))):
        t = s["rollout"]
        g = dup_of.setdefault(s["prefix_sha256"], len(dup_of))
        rows.append({
            "row": len(rows), "snapshot_id": f"{t}:{s['step_index']}", "run_id": t, "trajectory_id": t,
            "action_id": f"{t}:{s['step_index']}", "step_index": s["step_index"], "family_id": s["family_id"],
            "task_group_id": s["scenario_id"], "split": s["split"], "label": y,
            "trajectory_has_violation": str(traj_any[t]).lower(), "token_index": int(s["prefix_length"]) - 1,
            "prefix_length": s["prefix_length"], "prefix_hash": s["prefix_sha256"], "dup_group": g,
            "is_dup_representative": "", "after_first_violation": str(seen_first[t]).lower(),
            "condition": s["condition"], "seed": s["seed"], "version": s["version"], "tool": s["tool"],
            "d2_sample": s["sample"], "label_source": srcname})
        seen_first[t] = seen_first[t] or bool(y)
    first = set()
    for r in rows:
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
        pos = [r for r in rr if r["label"]]
        stats[sp] = {"rows": len(rr), "positives": len(pos),
                     "positives_by_version": dict(Counter(r["version"] for r in pos)),
                     "positive_families": sorted({r["family_id"] for r in pos}),
                     "positive_tools": dict(Counter(r["tool"] for r in pos).most_common(8)),
                     "violation_trajectories": len({r["trajectory_id"] for r in pos}),
                     "normal_trajectories": len({r["trajectory_id"] for r in rr if r["trajectory_has_violation"] == "false"}),
                     "dedup_rows": sum(r["is_dup_representative"] == "true" for r in rr)}
    (OUT / "card.md").write_text(
        "# D-v2r (D-v2 relabelled with review DRAFT, substantive action target)\n\n"
        "**STATUS: DRAFT labels — LLM blind review (labels-draft-v1), not human-confirmed.**\n\n"
        f"Built by `experiments/risk_loop/build_d_v2r.py`. samples.csv sha256 `{sha}`.\n"
        f"Labels: `{LAB.relative_to(ROOT)}` sha256 `{hashlib.sha256(LAB.read_bytes()).hexdigest()}`.\n"
        "Features: D-v2/obs_L{11,16,23}.npy via `d2_sample`. v1 rows keep their original rule labels.\n"
        f"Dropped rows: {dict(drop)}. Dup groups with conflicting labels: {n_conf}.\n\n"
        + "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in stats.items()) + "\n")
    print(sha, json.dumps(stats, ensure_ascii=False, indent=1), dict(drop), "conflicting dup groups", n_conf)


if __name__ == "__main__":
    main()
