import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

pytest.importorskip("pyarrow")
import hf_space_keeper as keeper  # noqa: E402
import upload_raw_data_to_hf as up  # noqa: E402
from restore_raw_unit_from_hf import part_map  # noqa: E402


def make_rollout(root: Path, name: str, finished: bool = True) -> Path:
    folder = root / name
    for step in range(2):
        capture = folder / "steps" / f"step_{step:03d}" / "capture"
        capture.mkdir(parents=True)
        np.savez(capture / "features.npz", block_0000=np.arange(12, dtype=np.float32).reshape(3, 4) + step)
        (capture / "metadata.json").write_text(json.dumps({"step": step}))
        (folder / "steps" / f"step_{step:03d}" / "step.json").write_text(json.dumps({"step_index": step}))
    record = {"report": {"ok": True}, "finished_at": "2026-11-02T00:00:00Z"} if finished else {}
    (folder / "rollout.json").write_text(json.dumps(record))
    old = time.time() - 7200
    for path in folder.rglob("*"):
        os.utime(path, (old, old))
    return folder


def upload_locally(root: Path, staging: Path, unit_ids):
    index = keeper.RawDataIndex(None, root)
    units = index.units(keeper.SOURCE)
    plan = [(keeper.SOURCE, u, units[u], up.signature(units[u], index)) for u in unit_ids]
    args = SimpleNamespace(compression="zstd", shard_mb=64, row_group_mb=8, chunk_mb=1, repo="x/y", rebuild=False)
    staging.mkdir()
    batch = up.convert_batch(plan, index, staging, "B1", args, log=lambda *_: None)
    manifest = {"units": {}, "shards": batch["shards"]}
    for source, items in batch["units"].items():
        for unit_id, unit in items.items():
            manifest["units"].setdefault(source, {})[unit_id] = {k: v for k, v in unit.items() if k != "skipped_paths"}
    tree = {path: shard["bytes"] for path, shard in batch["shards"].items()}
    return manifest, tree


def run_checks(root, staging, manifest, tree):
    local = keeper.local_units(root, 30)
    entries = {u: keeper.check_manifest(u, v, manifest, tree) for u, v in local.items()}
    results = keeper.check_contents(local, entries, manifest, lambda p: staging / p, lambda f: None, 0)
    return local, entries, results


def test_two_checks_pass_then_only_captures_are_deleted(tmp_path):
    root = tmp_path / "rollouts-v2"
    make_rollout(root, "AVv2-F01-X-pilotD-s0-aaaaaaaa")
    make_rollout(root, "AVv2-F01-X-pilotD-s1-bbbbbbbb", finished=False)  # unfinished: never touched
    manifest, tree = upload_locally(root, tmp_path / "staging", ["AVv2-F01-X-pilotD-s0-aaaaaaaa"])
    local, entries, results = run_checks(root, tmp_path / "staging", manifest, tree)
    assert set(local) == {"AVv2-F01-X-pilotD-s0-aaaaaaaa"}
    result = results["AVv2-F01-X-pilotD-s0-aaaaaaaa"]
    assert isinstance(result, dict) and result["rows"] == entries["AVv2-F01-X-pilotD-s0-aaaaaaaa"]["rows"]
    folder = local["AVv2-F01-X-pilotD-s0-aaaaaaaa"]["folder"]
    removed, freed = keeper.delete_captures(root, folder)
    assert removed == 2 and freed > 0
    assert (folder / "rollout.json").is_file() and (folder / "steps/step_000/step.json").is_file()
    assert not list(folder.glob("steps/*/capture"))
    assert keeper.local_units(root, 30) == {}  # cleaned units are not picked up again
    assert list((root / "AVv2-F01-X-pilotD-s1-bbbbbbbb").glob("steps/*/capture"))


def test_check1_fails_when_local_changed_after_upload(tmp_path):
    root = tmp_path / "rollouts-v2"
    folder = make_rollout(root, "U-s0-aaaaaaaa")
    manifest, tree = upload_locally(root, tmp_path / "staging", ["U-s0-aaaaaaaa"])
    (folder / "steps/step_000/step.json").write_text(json.dumps({"step_index": 99}))
    old = time.time() - 7200
    os.utime(folder / "steps/step_000/step.json", (old, old))
    local = keeper.local_units(root, 30)
    with pytest.raises(keeper.CheckError):
        keeper.check_manifest("U-s0-aaaaaaaa", local["U-s0-aaaaaaaa"], manifest, tree)


def test_check1_fails_when_shard_missing_or_wrong_size(tmp_path):
    root = tmp_path / "rollouts-v2"
    make_rollout(root, "U-s0-aaaaaaaa")
    manifest, tree = upload_locally(root, tmp_path / "staging", ["U-s0-aaaaaaaa"])
    local = keeper.local_units(root, 30)
    shard = next(iter(tree))
    with pytest.raises(keeper.CheckError):
        keeper.check_manifest("U-s0-aaaaaaaa", local["U-s0-aaaaaaaa"], manifest, {**tree, shard: tree[shard] + 1})
    with pytest.raises(keeper.CheckError):
        keeper.check_manifest("U-s0-aaaaaaaa", local["U-s0-aaaaaaaa"], {"units": {}, "shards": {}}, tree)


def test_check2_detects_content_difference_with_same_signature(tmp_path):
    root = tmp_path / "rollouts-v2"
    folder = make_rollout(root, "U-s0-aaaaaaaa")
    manifest, tree = upload_locally(root, tmp_path / "staging", ["U-s0-aaaaaaaa"])
    # same size and mtime, different array values: check 1 cannot see it, check 2 must
    target = folder / "steps/step_001/capture/features.npz"
    stat = target.stat()
    np.savez(target, block_0000=np.arange(12, dtype=np.float32).reshape(3, 4) + 100)
    assert target.stat().st_size == stat.st_size
    os.utime(target, (stat.st_mtime, stat.st_mtime))
    _, _, results = run_checks(root, tmp_path / "staging", manifest, tree)
    assert isinstance(results["U-s0-aaaaaaaa"], str) and "differ" in results["U-s0-aaaaaaaa"]


def test_delete_refuses_paths_outside_root(tmp_path):
    root = tmp_path / "rollouts-v2"
    other = make_rollout(tmp_path / "elsewhere", "U-s0-aaaaaaaa")
    with pytest.raises(keeper.CheckError):
        keeper.delete_captures(root, other)


def test_restore_part_map_understands_new_rollout_roots(tmp_path):
    for folder in ("artifacts/rollouts/A-s0-1", "artifacts/rollouts-v2/A-s0-1", "artifacts/rollouts-v1h2/A-s0-1"):
        assert part_map("scenario_rollout", {"folders": [folder]}, tmp_path) == [("rollout/", tmp_path / folder)]
    assert part_map("scenario_rollout", {"folders": ["tensor_data/scenario-A-1"]}, tmp_path)[0][0] == "tensor_data/scenario-A-1/"
