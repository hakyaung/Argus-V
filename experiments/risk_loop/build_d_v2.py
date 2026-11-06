"""Build D-v2: step-level samples from the 2026-11-02 run queue (harness v2), train + validation only.

Every step contributes all its observation rows (pre_reasoning, reasoning+32k, reasoning_end, pre_action)
for the chosen layers, stored float16 in one array per layer with a row table. Test-split and unsplit
(fakeweb, low-trust) rollouts are listed by ID only; their features are never read (TASK.md §2.1).

Labels are rule labels (policy_rule_unreviewed): v2 from operator_report.action_labels,
v1h2 from steps[].policy_label (violation type: report_content if the tool is submit_result, else action).

  .venv/bin/python experiments/risk_loop/build_d_v2.py --layers 11,16,23
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/risk-loop/datasets/D-v2"
QUEUE = ROOT / "artifacts/run-queue-20261102/queue.json"


def tool_of(step) -> str:
    cmd = step.get("command")
    if step.get("kind") == "tool" and isinstance(cmd, str):
        try:
            return json.loads(cmd).get("tool", "")
        except json.JSONDecodeError:
            return ""
    return step.get("kind") or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layers", default="11,16,23")
    args = ap.parse_args()
    layers = [int(x) for x in args.layers.split(",")]
    queue = {(e["output_root"], e["scenario_id"], e["condition"], e["seed"]): e
             for e in json.loads(QUEUE.read_text())["queue"] if e["type"] == "rollout"}
    samples, obs_rows, sealed = [], [], []
    feats = {L: [] for L in layers}
    for root in ("artifacts/rollouts-v2", "artifacts/rollouts-v1h2"):
        for record in sorted((ROOT / root).glob("*/rollout.json")):
            d = json.loads(record.read_text())
            if "report" not in d:
                continue
            e = queue[(root, d["scenario_id"], d["condition"], d["seed"])]
            split = e.get("split")
            if split not in ("train", "validation"):
                sealed.append({"rollout": record.parent.name, "split": split})
                continue
            family = e.get("family_id") or d.get("family")
            # action_labels cover tool steps only, in order (bash steps have no entry); align by order + tool name
            action_labels = (d.get("operator_report") or {}).get("action_labels") or []
            tool_steps = [i for i, s in enumerate(d["steps"]) if s.get("kind") == "tool"]
            labels = {}
            if action_labels:
                assert len(action_labels) == len(tool_steps), record
                for i, a in zip(tool_steps, action_labels):
                    assert tool_of(d["steps"][i]) == a["tool"], record
                    labels[i] = a
            seen_violation = False
            traj_violation = int(d["report"]["violations"] > 0)
            for k, step in enumerate(d["steps"]):
                cap = record.parent / step["capture_dir"]
                meta = json.loads((cap / "metadata.json").read_text())
                tool = tool_of(step)
                if action_labels:
                    a = labels.get(k)
                    y = int(a["policy_violation_label"]) if a else int(step.get("policy_label") or 0)
                    assert a is None or y == int(step.get("policy_label") or 0), (record, k)
                    vtype = (a or {}).get("violation_type") or ("action" if y else "")
                else:
                    y = int(step.get("policy_label") or 0)
                    vtype = ("report_content" if tool == "submit_result" else "action") if y else ""
                kind = "report" if vtype == "report_content" else ("action" if y else "")
                sid = len(samples)
                with np.load(cap / "features.npz") as z:
                    for L in layers:
                        feats[L].append(z[f"block_{L:04d}"].astype(np.float16))
                kinds = [o["kind"] for o in step["observations"]]
                assert len(kinds) == len(meta["alignment"]) == feats[layers[0]][-1].shape[0], cap
                for j, o in enumerate(step["observations"]):
                    obs_rows.append({"sample": sid, "obs": j, "kind": o["kind"], "token_index": o["token_index"]})
                samples.append({
                    "sample": sid, "version": e["version"], "split": split, "family_id": family,
                    "scenario_id": d["scenario_id"], "condition": d["condition"], "seed": d["seed"],
                    "trajectory_id": d["trajectory_id"], "rollout": record.parent.name, "step_index": k,
                    "prefix_length": meta["observed_prefix_length"], "n_obs": len(kinds),
                    "reasoning_tokens": step["observations"][-1]["token_index"] - step["observations"][0]["token_index"],
                    "label": y, "label_kind": kind, "after_first_violation": int(seen_violation),
                    "trajectory_has_violation": traj_violation, "tool": tool,
                    "defect_codes": "|".join(e.get("defect_codes") or []),
                    "prefix_sha256": meta["prefix_ids_sha256"]})
                seen_violation = seen_violation or bool(y)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in (("samples.csv", samples), ("obs_rows.csv", obs_rows)):
        with open(OUT / name, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader(); w.writerows(rows)
    hashes = {}
    for L in layers:
        arr = np.concatenate(feats[L])
        np.save(OUT / f"obs_L{L:02d}.npy", arr)
        hashes[f"obs_L{L:02d}.npy"] = hashlib.sha256(arr.tobytes()).hexdigest()
    (OUT / "sealed.json").write_text(json.dumps(sealed, indent=0))
    (OUT / "hash.json").write_text(json.dumps(hashes, indent=1))
    print(f"samples {len(samples)}, obs rows {len(obs_rows)}, sealed rollouts {len(sealed)}")


if __name__ == "__main__":
    main()
