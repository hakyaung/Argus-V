"""hf_select: selective reads over shards that use the uploader's real schema (no network)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

pq = pytest.importorskip("pyarrow.parquet")
pa = pytest.importorskip("pyarrow")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hf_select  # noqa: E402
import upload_raw_data_to_hf as upload  # noqa: E402


def tensor_rows(unit, path, array, layer, chunk=None, array_name=None):
    data = np.ascontiguousarray(array).reshape(-1).view(np.uint8).tobytes()
    pieces = [(0, data)] if chunk is None else [(o, data[o:o + chunk]) for o in range(0, len(data), chunk)]
    rows = []
    for i, (offset, piece) in enumerate(pieces):
        rows.append({"source": "scenario_rollout", "unit_id": unit, "unit_kind": "scenario_rollout", "folder": "f",
                     "path": path, "chunk_index": i, "chunk_count": len(pieces), "chunk_offset": offset,
                     "chunk_sha256": hashlib.sha256(piece).hexdigest(), "array_name": array_name,
                     "dtype": array.dtype.str, "shape": list(array.shape), "nbytes": len(data), "record_id": 1,
                     "phase": "pre_action", "module": f"model.layers.{layer}", "module_class": "Block",
                     "layer_index": layer, "call_index": 0, "forward_call_index": 0, "location": "output",
                     "meaning": "m", "source_dtype": "bfloat16", "record_json": "{}", "data": piece})
    return rows


@pytest.fixture
def hub(tmp_path):
    schema = upload.schemas()["tensors"]
    rng = np.random.default_rng(0)
    arrays = {
        ("U1", "steps/a.npy", 0): rng.standard_normal((4, 8)).astype(np.float32),
        ("U1", "steps/a.npy", 16): rng.standard_normal((4, 8)).astype(np.float32),
        ("U1", "steps/big.npy", 16): rng.standard_normal((64,)).astype(np.float32),  # chunked over 3 rows
        ("U2", "steps/a.npy", 16): rng.standard_normal((2, 3)).astype(np.float16),
    }
    rows = {}
    for (unit, path, layer), arr in arrays.items():
        chunk = 100 if "big" in path else None
        rows[(unit, path, layer)] = tensor_rows(unit, f"{path}@L{layer}", arr, layer, chunk=chunk)
    big = rows[("U1", "steps/big.npy", 16)]
    # shard A: two row groups; the chunked value is split across the row groups AND across shards
    shard_a = [rows[("U1", "steps/a.npy", 0)] + big[:1], rows[("U1", "steps/a.npy", 16)] + big[1:2]]
    shard_b = [big[2:] + rows[("U2", "steps/a.npy", 16)]]
    files = {}
    for name, groups in (("data/scenario_rollout/tensors/b-00000.parquet", shard_a),
                         ("data/scenario_rollout/tensors/b-00001.parquet", shard_b)):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with pq.ParquetWriter(target, schema) as writer:
            for group in groups:
                writer.write_table(pa.Table.from_pylist(group, schema=schema))
        files[name] = target
    manifest = {"units": {"scenario_rollout": {
        "U1": {"shards": sorted(files), "rows": 7, "signature": {"bytes": 1}},
        "U2": {"shards": ["data/scenario_rollout/tensors/b-00001.parquet"], "rows": 1, "signature": {"bytes": 1}}}},
        "shards": {}}
    opened = []

    def opener(shard):
        opened.append(shard)
        return open(files[shard], "rb")

    sel = hf_select.HubSelector(cache_dir=tmp_path / "cache", opener=opener, manifest_loader=lambda: manifest)
    return sel, arrays, files, opened


def test_select_by_layer_reads_only_needed_row_groups(hub):
    sel, arrays, _, _ = hub
    rows = sel.select(units=["U1"], layer=0)
    assert {r["path"] for r in rows} == {"steps/a.npy@L0"}
    plan = sel.plan(rows)
    assert plan["row_groups"] == 1 and plan["values"] == 1
    got = list(sel.fetch_arrays(rows))
    assert len(got) == 1 and np.array_equal(got[0][1], arrays[("U1", "steps/a.npy", 0)])
    assert sel.stats["row_groups_read"] == 1


def test_chunked_value_is_completed_across_row_groups_and_shards(hub):
    sel, arrays, _, _ = hub
    rows = sel.select(units=["U1"], path_glob="steps/big*")
    assert len(rows) == 3  # all chunks, although they live in 3 row groups of 2 shards
    (key, array, meta), = sel.fetch_arrays(rows)
    assert np.array_equal(array, arrays[("U1", "steps/big.npy", 16)])
    # selecting one chunk alone still yields the whole value
    one = [r for r in rows if r["chunk_index"] == 1]
    (_, again, _), = sel.fetch_arrays(one)
    assert np.array_equal(again, array)


def test_unit_filter_never_opens_unrelated_shards(hub):
    sel, arrays, _, opened = hub
    rows = sel.select(units=["U2"])
    assert set(opened) == {"data/scenario_rollout/tensors/b-00001.parquet"}
    (_, array, meta), = sel.fetch_arrays(rows)
    assert array.dtype == np.float16 and np.array_equal(array, arrays[("U2", "steps/a.npy", 16)])


def test_index_is_cached_on_disk(hub, tmp_path):
    sel, _, _, opened = hub
    sel.select(units=["U2"])
    n = len(opened)
    fresh = hf_select.HubSelector(cache_dir=tmp_path / "cache", opener=sel._opener,
                                  manifest_loader=sel._manifest_loader)
    fresh.select(units=["U2"])
    assert len(opened) == n  # served from the cached index, shard not reopened


def test_corrupted_chunk_is_rejected(hub):
    sel, _, _, _ = hub
    rows = sel.select(units=["U1"], layer=0)
    rows[0] = dict(rows[0], chunk_sha256="0" * 64)
    with pytest.raises(hf_select.SelectError, match="sha256 mismatch"):
        list(sel.fetch_arrays(rows))


def test_unknown_unit_is_an_error(hub):
    sel, _, _, _ = hub
    with pytest.raises(hf_select.SelectError, match="not in the Hub manifest"):
        sel.select(units=["NOPE"])


def test_layer_filter_understands_c01_block_members():
    assert hf_select.row_layer({"layer_index": None, "array_name": "block_0016"}) == 16
    assert hf_select.row_layer({"layer_index": 3, "array_name": "block_0016"}) == 3
    assert hf_select.row_layer({"layer_index": None, "array_name": "token_ids"}) is None
