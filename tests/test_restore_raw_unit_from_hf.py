"""scripts/restore_raw_unit_from_hf.py: rebuild a unit from shards made by upload_raw_data_to_hf.py (no network)."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyarrow.parquet")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import restore_raw_unit_from_hf as restore  # noqa: E402
import upload_raw_data_to_hf as upload  # noqa: E402

RUN, ROLLOUT = "run-20261016T000000Z-11112222", "AV-S01-C2-s0-abcd1234"


@pytest.fixture
def batch(tmp_path):
    """Local raw data converted by the uploader (dry run, shards kept); returns (source root, batch, staging dir)."""
    src = tmp_path / "src"
    tensors, rollouts = src / "tensor_data", src / "artifacts" / "rollouts"
    hook = tensors / RUN / "step_001" / "hook_inspection"
    (hook / "tensors").mkdir(parents=True)
    np.save(hook / "tensors" / "0000000.npy", np.arange(12, dtype=">f4").reshape(3, 4))
    big = np.random.default_rng(0).standard_normal((3, 1024, 200)).astype(np.float32)  # ~2.3 MiB: 3 chunks of 1 MiB
    np.save(hook / "tensors" / "0000001.npy", big)
    (hook / "records.jsonl").write_text('{"record_id": 1, "filename": "tensors/0000000.npy"}\n')
    (tensors / RUN / "big.bin").write_bytes(os.urandom(int(2.5 * 1024 * 1024)))  # chunked binary file
    rollout = rollouts / ROLLOUT
    (rollout / "steps" / "step_000" / "capture").mkdir(parents=True)
    (rollout / "rollout.json").write_text('{"scenario_key": "AV-S01/C2"}')
    np.savez(rollout / "steps" / "step_000" / "capture" / "features.npz",
             block_0000=np.ones((2, 2), np.float32), ids=np.arange(5, dtype=np.int64))
    (tensors / f"scenario-{ROLLOUT}" / "s").mkdir(parents=True)
    (tensors / f"scenario-{ROLLOUT}" / "s" / "blob.bin").write_bytes(b"\xff\x00")
    old = time.time() - 3600
    for path in src.rglob("*"):
        os.utime(path, (old, old))
    staging = tmp_path / "staging"
    assert upload.main(["--tensor-data", str(tensors), "--rollouts", str(rollouts), "--staging", str(staging),
                        "--chunk-mb", "1", "--dry-run", "--keep-shards"]) == 0
    (folder,) = [p for p in staging.iterdir() if p.is_dir()]
    record = json.loads((folder / "dry-run.json").read_text())
    # the uploader records folders relative to its PROJECT_ROOT; rewrite them relative to the synthetic root
    for units in record["units"].values():
        for unit in units.values():
            unit["folders"] = [Path(f).relative_to(src.resolve()).as_posix() for f in unit["folders"]]
    return src, record, folder


def fetcher(folder):
    calls = []

    def fetch(path):
        calls.append(path)
        return folder / path
    fetch.calls = calls
    return fetch


def same_tree(a: Path, b: Path):
    names = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    assert names == sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    for name in names:
        x, y = a / name, b / name
        if x.suffix == ".npy":
            left, right = np.load(x), np.load(y)
            assert left.dtype.name == right.dtype.name and np.array_equal(left, right), name
        elif x.suffix == ".npz":
            with np.load(x) as left, np.load(y) as right:
                assert sorted(left.files) == sorted(right.files)
                assert all(np.array_equal(left[k], right[k]) for k in left.files)
        else:
            assert x.read_bytes() == y.read_bytes(), name
            assert abs(x.stat().st_mtime - y.stat().st_mtime) < 1e-3, name


def test_agent_loop_unit_round_trips_with_chunks(batch, tmp_path):
    src, record, folder = batch
    assert record["units"]["agent_loop"][RUN]["chunked"] >= 2
    dest = tmp_path / "dest"
    result = restore.restore_unit(record, RUN, fetcher(folder), dest, min_free_gb=0)
    assert result["rows"] == record["units"]["agent_loop"][RUN]["rows"]
    same_tree(src / "tensor_data" / RUN, dest / "tensor_data" / RUN)


def test_rollout_unit_maps_prefixes_and_only_prefix_skips_rollout(batch, tmp_path):
    src, record, folder = batch
    dest = tmp_path / "dest"
    restore.restore_unit(record, ROLLOUT, fetcher(folder), dest, only_prefix="tensor_data/", min_free_gb=0)
    same_tree(src / "tensor_data" / f"scenario-{ROLLOUT}", dest / "tensor_data" / f"scenario-{ROLLOUT}")
    assert not (dest / "artifacts").exists()
    restore.restore_unit(record, ROLLOUT, fetcher(folder), dest, min_free_gb=0)  # existing files are kept
    same_tree(src / "artifacts" / "rollouts" / ROLLOUT, dest / "artifacts" / "rollouts" / ROLLOUT)


def test_dry_run_writes_nothing_and_existing_files_are_not_overwritten(batch, tmp_path):
    src, record, folder = batch
    dest = tmp_path / "dest"
    result = restore.restore_unit(record, RUN, fetcher(folder), dest, dry_run=True, min_free_gb=0)
    assert result["written_files"] == 0 and result["would_write"] > 0 and not dest.exists()
    keep = dest / "tensor_data" / RUN / "big.bin"
    keep.parent.mkdir(parents=True)
    keep.write_bytes(b"mine")
    result = restore.restore_unit(record, RUN, fetcher(folder), dest, min_free_gb=0)
    assert keep.read_bytes() == b"mine" and result["skipped_existing"] == ["big.bin"]


def test_wrong_row_count_or_corrupt_chunk_fails(batch, tmp_path):
    src, record, folder = batch
    record["units"]["agent_loop"][RUN]["rows"] += 1
    with pytest.raises(restore.RestoreError, match="rows"):
        restore.restore_unit(record, RUN, fetcher(folder), tmp_path / "a", min_free_gb=0)
    record["units"]["agent_loop"][RUN]["rows"] -= 1
    import pyarrow.parquet as pq

    shard = next(p for p in record["units"]["agent_loop"][RUN]["shards"] if "/files/" in p)
    table = pq.read_table(folder / shard).to_pylist()
    table[0]["chunk_sha256"] = hashlib.sha256(b"other").hexdigest()
    pq.write_table(__import__("pyarrow").Table.from_pylist(table, schema=upload.schemas()["files"]), folder / shard)
    with pytest.raises(restore.RestoreError, match="chunk_sha256"):
        restore.restore_unit(record, RUN, fetcher(folder), tmp_path / "b", min_free_gb=0)


def test_unsafe_paths_are_rejected():
    parts = restore.part_map("agent_loop", {"folders": ["tensor_data/run-x"]}, Path("/root"))
    with pytest.raises(restore.RestoreError):
        restore.local_target(parts, "../escape")
    with pytest.raises(restore.RestoreError):
        restore.local_target(parts, "/etc/passwd")
