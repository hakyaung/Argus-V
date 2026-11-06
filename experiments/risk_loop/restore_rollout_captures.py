"""Bulk-restore deleted ``steps/*/capture`` folders of finished rollouts from the HF raw-data dataset.

Each shard is downloaded once and every unit in it is restored, then the shard is removed.
Existing local files are never overwritten (restore_raw_unit_from_hf skips them), so this only
refills captures that hf_space_keeper deleted. Read-only for the Hub.

  .venv/bin/python experiments/risk_loop/restore_rollout_captures.py --roots artifacts/rollouts-v2 artifacts/rollouts-v1h2
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import restore_raw_unit_from_hf as rr  # noqa: E402


def wanted_units(roots: list[Path]) -> list[str]:
    out = []
    for root in roots:
        for record in sorted(root.glob("*/rollout.json")):
            folder = record.parent
            data = json.loads(record.read_text(encoding="utf-8"))
            if "report" not in data:
                continue
            steps = [p for p in folder.glob("steps/step_*") if p.is_dir()]
            if steps and not all((p / "capture").is_dir() for p in steps):
                out.append(folder.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roots", nargs="+", type=Path, required=True)
    ap.add_argument("--repo", default=rr.DEFAULT_REPO)
    ap.add_argument("--revision", default="main")
    ap.add_argument("--min-free-gb", type=float, default=30.0)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    from huggingface_hub import hf_hub_download

    units = wanted_units([ROOT / r for r in args.roots])[: args.limit]
    work = Path(tempfile.mkdtemp(prefix="bulk-restore-", dir=ROOT / "artifacts"))
    try:
        manifest = json.loads(Path(hf_hub_download(args.repo, rr.MANIFEST, repo_type="dataset",
                                                   revision=args.revision, local_dir=work)).read_text("utf-8"))
        by_shards = collections.defaultdict(list)
        for unit in units:
            _, entry = rr.find_unit(manifest, unit, "scenario_rollout")
            by_shards[tuple(entry["shards"])].append(unit)
        print(f"[plan] {len(units)} unit(s) need captures, {len(by_shards)} shard group(s)", flush=True)
        cache: dict[str, Path] = {}

        def fetch(path: str) -> Path:
            if path not in cache:
                cache[path] = Path(hf_hub_download(args.repo, path, repo_type="dataset", revision=args.revision,
                                                   local_dir=work))
            return cache[path]

        done = failed = 0
        t0 = time.time()
        for group, members in by_shards.items():
            for unit in members:
                try:
                    res = rr.restore_unit(manifest, unit, fetch, ROOT, source="scenario_rollout",
                                          min_free_gb=args.min_free_gb, log=lambda *_: None)
                    done += 1
                    if res["written_files"] == 0:
                        print(f"[warn] {unit}: nothing written", flush=True)
                except Exception as error:  # keep going, report at the end
                    failed += 1
                    print(f"[fail] {unit}: {error}", flush=True)
            for shard in group:
                local = cache.pop(shard, None)
                if local:
                    local.unlink(missing_ok=True)
            print(f"[progress] {done + failed}/{len(units)} restored={done} failed={failed} "
                  f"{(time.time() - t0) / 60:.1f} min", flush=True)
        print(f"[done] restored={done} failed={failed}", flush=True)
        return 1 if failed else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
