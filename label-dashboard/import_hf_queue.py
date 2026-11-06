#!/usr/bin/env python3
"""Prepare a read-only source snapshot for the November Hugging Face label queue.

Reads only the HF files shards referenced by the selected rollout units. Tensor/activation
shards are never opened. First run without --publish-hf to inspect the export count.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from hf_select import HubSelector  # noqa: E402

DEFAULT_REPO = "hakuuung/gpt-oss-20b-hidden-state-raw-data"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--since-batch", default="20261102", help="YYYYMMDD batch prefix, inclusive")
    parser.add_argument("--out", type=Path, default=Path("november_queue.jsonl"))
    parser.add_argument("--preview-out", type=Path, help="write a lightweight, real-HF queue preview JSON (no task payloads)")
    parser.add_argument("--publish-hf", action="store_true", help="add the generated queue snapshot as a separate HF sidecar")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selector = HubSelector(repo=args.repo, log=lambda message: print(message, file=sys.stderr))
    manifest = selector.manifest()
    all_units = selector.units(source="scenario_rollout")
    selected = {
        unit_id: unit
        for unit_id, unit in all_units.items()
        if str(unit.get("batch", ""))[:8] >= args.since_batch
    }
    if not selected:
        raise SystemExit(f"No rollout units found from batch date {args.since_batch}.")
    print(f"[plan] revision={selector.revision or 'main'} units={len(selected)} since={args.since_batch}")

    unit_ids = list(selected)
    rows = selector.select(
        units=unit_ids,
        table="files",
        where=lambda row: row.get("path") == "rollout/rollout.json"
        or row.get("path") == "rollout/transcript.md"
        or re.fullmatch(r"rollout/steps/step_\d+/step\.json", row.get("path", "")) is not None
        or re.fullmatch(r"rollout/steps/step_\d+/m1-context\.json", row.get("path", "")) is not None,
    )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for (unit_id, path, _array_name), content, _meta in selector.fetch_values(rows):
        grouped[unit_id].append({"path": path, "text": content.decode("utf-8", errors="replace")})

    tasks = []
    for unit_id, unit in selected.items():
        files = sorted(grouped.get(unit_id, []), key=lambda item: item["path"])
        rollout_file = next((item for item in files if item["path"] == "rollout/rollout.json"), None)
        if not rollout_file:
            raise RuntimeError(f"Missing rollout/rollout.json for {unit_id}")
        rollout = json.loads(rollout_file["text"])
        tasks.append({
            "unit_id": unit_id,
            "scenario_id": rollout.get("scenario_id", unit_id.split("-")[0]),
            "condition": rollout.get("condition", "unknown"),
            "seed": int(rollout.get("seed", 0)),
            "batch": unit.get("batch", ""),
            "source_revision": selector.revision or "main",
            "payload": {"rollout": rollout, "files": files},
            "status": "queued",
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"[export] {len(tasks)} tasks -> {args.out}")
    if args.preview_out:
        preview = {
            "source": f"https://huggingface.co/datasets/{args.repo}",
            "revision": selector.revision or "main",
            "since_batch": args.since_batch,
            "total": len(tasks),
            "tasks": [
                {key: task[key] for key in ("unit_id", "scenario_id", "condition", "seed", "batch")}
                | {"status": "queued", "assigned_email": None}
                for task in tasks
            ],
        }
        args.preview_out.parent.mkdir(parents=True, exist_ok=True)
        args.preview_out.write_text(json.dumps(preview, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"[preview] {len(tasks)} actual HF rollout rows -> {args.preview_out}")
    if args.publish_hf:
        subprocess.run(["node", str(ROOT / "label-dashboard" / "publish_hf_queue.js"), str(args.out)], check=True)
        print("[done] HF raw files unchanged; queue snapshot added as a separate sidecar.")
    else:
        print("[dry-run] No HF writes. Review the JSONL export, then use --publish-hf to add the queue sidecar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
