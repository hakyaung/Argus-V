"""Safe compaction, exact feature reuse, resumability and explicit deletion boundaries."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from test_probe_assembly import sources  # noqa: F401
from test_reasoning_mean import comparison, setup_run
from test_reasoning_replay import replayer

from argus_v.probes.data import file_sha256

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "compact_replays", ROOT / "scripts/compact_reasoning_replays.py"
)
compactor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compactor)


def prepare(sources, adapter, ids, *, storage="full"):  # noqa: F811
    original, _ = setup_run(sources, adapter, ids)
    output = sources["root"] / "replays"
    replayer.replay(
        ROOT,
        original,
        output,
        sources["root"] / "checkpoint",
        layers=(0, 1),
        adapter=adapter,
        storage=storage,
    )
    return original, output


def compact(original, output, **kwargs):
    return compactor.compact(ROOT, original, [output], **kwargs)


def compare(sources, original, output, name):  # noqa: F811
    return comparison.compare(
        ROOT,
        original,
        sources["root"] / name,
        [output],
        layers=(0, 1),
        layer_aggregation="mean",
    )


def test_compaction_is_readonly_by_default_then_deletes_only_verified_tensors(
    sources,  # noqa: F811
    adapter,
    ids,
    monkeypatch,  # noqa: F811
):
    original, output = prepare(sources, adapter, ids)
    compare(sources, original, output, "before")
    snapshots = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    raw_size = sum(p.stat().st_size for p in output.rglob("*.npy"))
    report = compact(original, output)
    assert report["dry_run"] and report["full_tensor_bytes"] == raw_size
    assert all(p.read_bytes() == value for p, value in snapshots.items())
    incomplete = output / "incomplete" / "block_0000.npy"
    incomplete.parent.mkdir()
    incomplete.write_bytes(b"interrupted action must be retained")
    test_capture = sources["cases"][-1]["capture"]
    (test_capture / "metadata.json").write_text("sealed test metadata")
    (test_capture / "features.npz").write_text("sealed test tensor")

    def forbidden(*args, **kwargs):
        raise AssertionError("no model execution during compaction/comparison")

    monkeypatch.setattr(adapter.model, "forward", forbidden)
    report = compact(original, output, apply=True, delete_source_tensors=True)
    assert report["converted_actions"] == 4 and report["reclaimed_bytes"] == raw_size
    assert list(output.rglob("*.npy")) == [incomplete]
    compare(sources, original, output, "after")
    with (
        np.load(sources["root"] / "before/paired-features.npz") as before,
        np.load(sources["root"] / "after/paired-features.npz") as after,
    ):
        for key in before.files:
            np.testing.assert_array_equal(before[key], after[key])
    for mode in ("reasoning_mean", "last_token"):
        before = json.loads((sources["root"] / f"before/{mode}-validation-scores.json").read_text())
        after = json.loads((sources["root"] / f"after/{mode}-validation-scores.json").read_text())
        assert before == after
    again = compact(original, output, apply=True, delete_source_tensors=True)
    assert again["converted_actions"] == 0 and again["reclaimed_bytes"] == 0
    assert again["already_pooled_actions"] == 4


def test_conversion_without_deletion_resumes_then_can_reclaim_space(sources, adapter, ids):  # noqa: F811
    original, output = prepare(sources, adapter, ids)
    raw_size = sum(p.stat().st_size for p in output.rglob("*.npy"))
    first = compact(original, output, apply=True, max_actions=1)
    assert first["converted_actions"] == 1 and len(list(output.rglob("*.npy"))) == 8
    final = compact(original, output, apply=True, delete_source_tensors=True)
    assert final["converted_actions"] == 3 and final["already_pooled_actions"] == 1
    assert final["reclaimed_bytes"] == raw_size and not list(output.rglob("*.npy"))


@pytest.mark.parametrize("fault", ["source", "summary", "span", "outside_path"])
def test_corruption_never_triggers_source_deletion(sources, adapter, ids, fault):  # noqa: F811
    original, output = prepare(sources, adapter, ids)
    manifest = sorted(output.glob("*/manifest.json"))[0]
    raw = manifest.parent / "block_0000.npy"
    if fault == "source":
        np.save(raw, np.load(raw) + 1)
    else:
        compact(original, output, apply=True)
        if fault == "summary":
            (manifest.parent / "pooled-features.npz").write_bytes(b"bad archive")
        elif fault == "span":
            meta = json.loads(manifest.read_text())
            meta["reasoning_span"] = [0, 3]
            manifest.write_text(json.dumps(meta))
        else:
            meta = json.loads(manifest.read_text())
            meta["source_tensors"][0]["filename"] = "../../outside.npy"
            manifest.write_text(json.dumps(meta))
    with pytest.raises((ValueError, OSError)):
        compact(original, output, apply=True, delete_source_tensors=True)
    assert raw.exists()


def test_failed_summary_write_keeps_full_manifest_and_source(
    sources,  # noqa: F811
    adapter,
    ids,
    monkeypatch,  # noqa: F811
):
    original, output = prepare(sources, adapter, ids)
    manifests = {p: p.read_bytes() for p in output.glob("*/manifest.json")}

    def no_space(*args, **kwargs):
        raise OSError("No space left")

    monkeypatch.setattr(np, "savez_compressed", no_space)
    with pytest.raises(OSError, match="No space left"):
        compact(original, output, apply=True, delete_source_tensors=True)
    assert all(p.read_bytes() == value for p, value in manifests.items())
    assert len(list(output.rglob("*.npy"))) == 8


def test_new_pooled_replay_saves_no_full_tensors_and_resumes(sources, adapter, ids):  # noqa: F811
    original, output = prepare(sources, adapter, ids, storage="pooled")
    assert not list(output.rglob("*.npy"))
    assert len(list(output.rglob("pooled-features.npz"))) == 4
    before = {p: file_sha256(p) for p in output.rglob("pooled-features.npz")}
    report = replayer.replay(
        ROOT,
        original,
        output,
        sources["root"] / "checkpoint",
        layers=(0, 1),
        adapter=adapter,
        storage="pooled",
    )
    assert report["completed_actions"] == 4 and report["pending_actions"] == 0
    assert all(file_sha256(p) == sha for p, sha in before.items())
    compare(sources, original, output, "pooled")
    assert (sources["root"] / "pooled/models/reasoning_mean/weights.npz").is_file()


def test_deletion_requires_explicit_apply(sources, adapter, ids):  # noqa: F811
    original, output = prepare(sources, adapter, ids)
    with pytest.raises(ValueError, match="requires --apply"):
        compact(original, output, delete_source_tensors=True)
    assert len(list(output.rglob("*.npy"))) == 8
