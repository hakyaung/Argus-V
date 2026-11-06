"""A2 Data Curator: build fixed dataset version D-v1 (TASK.md Iteration 0).

Test rows are written with IDs/split/manifest token_index only: no capture of a
test row is opened (no metadata, no features), and test labels / trajectory
flags are never copied. Train/validation features come from
``argus_v.probes.load_probe_snapshot`` (hash-checked C-01 pre_action rows).

Usage (repo root):
    PYTHONPATH=src .venv/bin/python3 experiments/risk_loop/build_d_v1.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from argus_v.probes.data import load_probe_snapshot, read_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "artifacts/probe-reviewed-20261022T123507Z-237dc0/dataset/manifest.json"
OUT = ROOT / "artifacts/risk-loop/datasets/D-v1"
LAYERS = tuple(range(24))
HIDDEN = 2880
COLUMNS = [
    "row", "snapshot_id", "run_id", "trajectory_id", "action_id", "step_index",
    "family_id", "task_group_id", "split", "label", "trajectory_has_violation",
    "token_index", "prefix_length", "prefix_hash", "dup_group", "is_dup_representative",
    "after_first_violation", "condition", "seed", "capture_dir",
]
ROLLOUT = re.compile(r"^(?P<task>AV-S\d+)-(?P<cond>C\d+)-s(?P<seed>\d+)-[0-9a-f]+$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_rollout(capture_dir: str) -> tuple[str, str]:
    for part in Path(capture_dir).parts:
        match = ROLLOUT.match(part)
        if match:
            return match["cond"], match["seed"]
    raise ValueError(f"no rollout folder in {capture_dir}")


def main() -> int:
    if OUT.exists():
        print(f"refusing to overwrite {OUT}", file=sys.stderr)
        return 1
    manifest = read_manifest(MANIFEST)
    records = manifest["samples"]
    n = len(records)
    rows: list[dict] = []
    for i, rec in enumerate(records):
        cond, seed = parse_rollout(rec["capture_dir"])
        is_test = rec["split"] == "test"
        rows.append({
            "row": i,
            "snapshot_id": rec["snapshot_id"],
            "run_id": rec["run_id"],
            "trajectory_id": rec["trajectory_id"],
            "action_id": rec["action_id"],
            "step_index": int(rec["action_id"].rsplit(":", 1)[1]),
            "family_id": rec["family_id"],
            "task_group_id": rec["task_group_id"],
            "split": rec["split"],
            "label": "" if is_test else int(rec["next_action_violation"]),
            "trajectory_has_violation": "" if is_test else str(bool(rec["trajectory_has_violation"])).lower(),
            "token_index": int(rec["token_index"]),
            "prefix_length": int(rec["token_index"]) + 1,
            "prefix_hash": "",
            "dup_group": None,
            "is_dup_representative": "",
            "after_first_violation": "",
            "condition": cond,
            "seed": seed,
            "capture_dir": rec["capture_dir"],
        })

    # Features + prefix hashes for train/validation only (ascending row order).
    tv = [r for r in rows if r["split"] != "test"]
    feats = np.empty((len(LAYERS), len(tv), HIDDEN), dtype=np.float32)
    for k, r in enumerate(tv):
        snap = load_probe_snapshot(
            MANIFEST.parent / r["capture_dir"], layers=LAYERS,
            snapshot_id=r["snapshot_id"], token_index=r["token_index"],
        )
        if (snap.run_id, snap.trajectory_id) != (r["run_id"], r["trajectory_id"]):
            raise ValueError(f"row {r['row']}: run/trajectory differs from capture")
        if snap.spec.hidden_sizes != (HIDDEN,) * len(LAYERS):
            raise ValueError(f"row {r['row']}: hidden sizes {snap.spec.hidden_sizes}")
        feats[:, k, :] = snap.features.reshape(len(LAYERS), HIDDEN)
        r["prefix_hash"] = snap.capture_prefix_ids_sha256
        if (k + 1) % 200 == 0:
            print(f"loaded {k + 1}/{len(tv)}", flush=True)
    if not np.isfinite(feats).all():
        raise ValueError("non-finite features")

    # Duplicate groups: shared id per identical prefix hash; test rows (no hash read) get own id.
    group_of: dict[str, int] = {}
    next_id = 0
    seen_in_split: set[tuple[int, str]] = set()
    for r in rows:
        if r["prefix_hash"]:
            if r["prefix_hash"] not in group_of:
                group_of[r["prefix_hash"]] = next_id
                next_id += 1
            r["dup_group"] = group_of[r["prefix_hash"]]
            key = (r["dup_group"], r["split"])
            r["is_dup_representative"] = "false" if key in seen_in_split else "true"
            seen_in_split.add(key)
        else:
            r["dup_group"] = next_id
            next_id += 1

    # after_first_violation (train/validation only).
    by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in tv:
        by_traj[r["trajectory_id"]].append(r)
    for traj_rows in by_traj.values():
        positive_steps = [r["step_index"] for r in traj_rows if r["label"] == 1]
        first = min(positive_steps) if positive_steps else None
        for r in traj_rows:
            r["after_first_violation"] = str(
                r["label"] == 1 and first is not None and r["step_index"] > first
            ).lower()

    tmp = OUT.with_name(OUT.name + ".tmp")
    if tmp.exists():
        print(f"stale temp dir {tmp}; remove manually", file=sys.stderr)
        return 1
    tmp.mkdir(parents=True)
    with (tmp / "samples.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    rows_tv = np.asarray([r["row"] for r in tv], dtype=np.int64)
    assert np.all(np.diff(rows_tv) > 0)
    np.save(tmp / "rows_tv.npy", rows_tv)
    for layer in LAYERS:
        np.save(tmp / f"features_L{layer:02d}.npy", feats[layer])

    # Card.
    lines = ["# D-v1 dataset card", "",
             f"Source manifest: `{MANIFEST.relative_to(ROOT)}` (sha256 {sha256_file(MANIFEST)})",
             f"labels_revision: {manifest['labels_revision']}, split_revision: {manifest['split_revision']}",
             f"Rows: {n} (train/validation features: {len(tv)} x 24 layers x {HIDDEN}, float32; test: IDs only, no features, labels empty, prefix_hash not read)",
             "Feature row: C-01 pre_action activation at manifest token_index (post_block_pre_final_norm).",
             "condition/seed are AUDIT-ONLY (forbidden model input).", "",
             "| split | rows | positives | families | families w/ pos | trajectories | violation traj | normal traj | dup fraction | after-first-violation pos | median prefix_length (neg / pos) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    stats = {}
    for split in ("train", "validation", "test"):
        srows = [r for r in rows if r["split"] == split]
        fams = {r["family_id"] for r in srows}
        trajs = {r["trajectory_id"] for r in srows}
        if split == "test":
            lines.append(f"| test | {len(srows)} | (sealed) | {len(fams)} | (sealed) | {len(trajs)} | (sealed) | (sealed) | (not computed) | (sealed) | (sealed) |")
            stats[split] = {"rows": len(srows), "families": len(fams), "trajectories": len(trajs)}
            continue
        pos = [r for r in srows if r["label"] == 1]
        neg = [r for r in srows if r["label"] == 0]
        fam_pos = {r["family_id"] for r in pos}
        vtraj = {r["trajectory_id"] for r in srows if r["trajectory_has_violation"] == "true"}
        dup_frac = sum(r["is_dup_representative"] == "false" for r in srows) / len(srows)
        in_dup = Counter(r["prefix_hash"] for r in srows)
        frac_in_shared = sum(in_dup[r["prefix_hash"]] > 1 for r in srows) / len(srows)
        afv = sum(r["after_first_violation"] == "true" for r in srows)
        med_neg = statistics.median(r["prefix_length"] for r in neg)
        med_pos = statistics.median(r["prefix_length"] for r in pos)
        lines.append(f"| {split} | {len(srows)} | {len(pos)} | {len(fams)} | {len(fam_pos)} | {len(trajs)} | {len(vtraj)} | {len(trajs) - len(vtraj)} | {dup_frac:.3f} | {afv} | {med_neg:g} / {med_pos:g} |")
        stats[split] = {
            "rows": len(srows), "positives": len(pos), "families": len(fams),
            "families_with_positives": len(fam_pos), "trajectories": len(trajs),
            "violation_trajectories": len(vtraj), "normal_trajectories": len(trajs) - len(vtraj),
            "dup_fraction_non_representative": round(dup_frac, 4),
            "fraction_rows_in_shared_prefix_groups": round(frac_in_shared, 4),
            "dedup_rows": sum(r["is_dup_representative"] == "true" for r in srows),
            "dedup_positives": sum(r["is_dup_representative"] == "true" and r["label"] == 1 for r in srows),
            "after_first_violation_positives": afv,
            "prefix_length_median_neg": med_neg, "prefix_length_median_pos": med_pos,
            "prefix_length_quartiles_neg": statistics.quantiles([r["prefix_length"] for r in neg], n=4),
            "prefix_length_quartiles_pos": statistics.quantiles([r["prefix_length"] for r in pos], n=4),
            "step_index_median_neg": statistics.median(r["step_index"] for r in neg),
            "step_index_median_pos": statistics.median(r["step_index"] for r in pos),
        }
    cross = defaultdict(set)
    for r in tv:
        cross[r["prefix_hash"]].add(r["split"])
    stats["prefix_hashes_shared_across_train_validation"] = sum(len(s) > 1 for s in cross.values())
    lines += ["", "dup fraction = share of rows that are NOT the first row of their prefix-hash group within the split "
              "(D-v1-dedup = rows with is_dup_representative==true).", "",
              "## Length distribution per label (train/validation)", "",
              "| split | label | q1 | median | q3 |", "|---|---|---|---|---|"]
    for split in ("train", "validation"):
        s = stats[split]
        for lab, q in (("0", s["prefix_length_quartiles_neg"]), ("1", s["prefix_length_quartiles_pos"])):
            lines.append(f"| {split} | {lab} | {q[0]:g} | {q[1]:g} | {q[2]:g} |")
    lines += ["", "## Positives per family (train/validation)", "", "| split | family | rows | positives |", "|---|---|---|---|"]
    for split in ("train", "validation"):
        fam_rows = Counter(r["family_id"] for r in tv if r["split"] == split)
        fam_pos = Counter(r["family_id"] for r in tv if r["split"] == split and r["label"] == 1)
        for fam in sorted(fam_rows):
            lines.append(f"| {split} | {fam} | {fam_rows[fam]} | {fam_pos[fam]} |")
    lines += ["", f"Prefix hashes shared across train/validation: {stats['prefix_hashes_shared_across_train_validation']}",
              "", "```json", json.dumps(stats, indent=1), "```", ""]
    (tmp / "card.md").write_text("\n".join(lines), encoding="utf-8")
    names = ["samples.csv", "rows_tv.npy"] + [f"features_L{l:02d}.npy" for l in LAYERS]
    (tmp / "hash.txt").write_text(
        "".join(f"{sha256_file(tmp / name)}  {name}\n" for name in names), encoding="utf-8")
    tmp.rename(OUT)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
