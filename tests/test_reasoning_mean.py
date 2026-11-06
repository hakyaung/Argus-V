import importlib.util
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest
from test_probe_assembly import sources  # noqa: F401
from test_run_reviewed_probe import add_normal_validation_step, prepare, runner

from argus_v.activations import ActionSpan, SnapshotContext, extract_prefix, write_capture
from argus_v.probes import load_probe, load_probe_dataset
from argus_v.probes.data import FeatureSpec

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mean_comparison", ROOT / "scripts/compare_reasoning_mean.py"
)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def setup_run(
    sources,  # noqa: F811
    adapter,
    ids,
    *,
    extra_normal_step=False,
    extra_validation_normal=False,
    dense=False,
    layer=1,
):
    if extra_validation_normal:
        source = sources["cases"][2]
        folder = source["rollout"].parent.parent / "extra-normal"
        shutil.copytree(source["rollout"].parent, folder)
        rollout = json.loads((folder / "rollout.json").read_text())
        rollout.update(trajectory_id="extra-normal", scenario_id="AV-S06", task_id="AV-S06")
        rollout["steps"][0].update(run_id="extra-normal-s000", action_id="extra-normal:0")
        put(folder / "rollout.json", rollout)
        capture = folder / "steps/step_000/capture"
        meta = json.loads((capture / "metadata.json").read_text())
        meta["context"].update(
            run_id="extra-normal-s000", trajectory_id="extra-normal", scenario_id="AV-S06"
        )
        meta["context"]["action"]["action_id"] = "extra-normal:0"
        put(capture / "metadata.json", meta)
        sources["cases"].append({**source, "capture": capture, "rollout": folder / "rollout.json"})
    path, samples = prepare(sources)
    cases = list(sources["cases"])
    if extra_normal_step:
        samples.append(add_normal_validation_step(sources, samples))
        progress = json.loads(path.read_text())
        progress["samples"] = samples
        put(path, progress)
        extra = {**cases[2], "capture": cases[2]["capture"].parent.parent / "step_001/capture"}
        cases.append(extra)
    replays = []
    for i, case in enumerate(cases):
        meta = json.loads((case["capture"] / "metadata.json").read_text())
        context = SnapshotContext(
            **{**meta["context"], "action": ActionSpan(**meta["context"]["action"])}
        )
        full = extract_prefix(adapter, ids, context, token_positions=tuple(range(ids.shape[1])))
        if dense:
            shutil.rmtree(case["capture"])
            write_capture(full, case["capture"])
        put(case["capture"].parent / "m1-context.json", {"reasoning_token_span": [1, 3]})
        folder = sources["tensors"] / f"full-replay-{i}"
        put(
            folder / "manifest.json",
            {
                "schema_version": "argus-v.hook-inspection.v1",
                "status": "complete",
                "phase": "pre_action_full_prefix_replay",
                "identity": asdict(adapter.identity),
                "model_type": adapter.model_type,
                "context": {**meta["context"], "input_ids": ids[0].tolist()},
            },
        )
        values = full.features[f"block_{layer:04d}"].numpy()[None]
        np.save(folder / "block.npy", values)
        row = {
            "kind": "tensor",
            "module": adapter.block_paths[layer],
            "layer_index": layer,
            "location": "output[0]" if adapter.model_type == "gpt2" else "output",
            "meaning": "post-residual block output, before final norm",
            "shape": list(values.shape),
            "filename": "block.npy",
        }
        (folder / "records.jsonl").write_text(json.dumps(row) + "\n")
        replays.append(folder)
    original = sources["root"] / "original"
    runner.run(sources["root"], path, original, [sources["rollouts"]], layers=(layer,), epochs=3)
    return original, replays


def compare(sources, original, **kwargs):  # noqa: F811
    return comparison.compare(
        ROOT,
        original,
        sources["root"] / "mean-comparison",
        [sources["tensors"]],
        layer=1,
        weight_decay=0.001,
        **kwargs,
    )


def test_pooled_comparison_seals_test_and_reproduces_paired_baseline(sources, adapter, ids):  # noqa: F811
    original, replays = setup_run(sources, adapter, ids)
    before = (original / "model/weights.npz").read_bytes()
    test_capture = sources["cases"][-1]["capture"]
    (test_capture / "metadata.json").write_text("sealed test metadata")
    (test_capture / "features.npz").write_text("sealed test vectors")
    (replays[-1] / "block.npy").write_text("sealed test Hook vector")
    result = compare(sources, original)
    assert result["complete_source_coverage"]
    assert result["paired_examples_identical"] and result["save_load_scores_identical"]
    assert not result["test_features_opened"]
    assert result["sample_counts"]["train"]["paired_actions"] == 2
    output = sources["root"] / "mean-comparison"
    mean = load_probe(output / "models/reasoning_mean")
    last = load_probe(output / "models/last_token")
    assert mean.spec.pooling == "reasoning_mean" and last.spec.pooling == "none"
    validation = load_probe_dataset(
        original / "dataset/manifest.json", split="validation", layers=(1,)
    )
    with pytest.raises(ValueError, match="differs from probe"):
        mean.predict(validation.snapshots[0])
    np.testing.assert_array_equal(
        last.model.risk_scores(validation.features),
        load_probe(original / "model").model.risk_scores(validation.features),
    )
    with np.load(output / "paired-features.npz") as arrays:
        expected = np.load(replays[0] / "block.npy")[0, 1:3].mean(axis=0, dtype=np.float64)
        np.testing.assert_array_equal(
            arrays["train_reasoning_mean"][0], expected.astype(np.float32)
        )
    assert (original / "model/weights.npz").read_bytes() == before
    assert (output / "comparison.csv").is_file()


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_prefix",
        "wrong_trajectory",
        "generation_phase",
        "different_activations",
        "missing_tensor",
        "outside_tensor",
        "future_span",
        "duplicate_replay",
        "mlp_output",
    ],
)
def test_invalid_sources_do_not_become_sentence_means(sources, adapter, ids, fault):  # noqa: F811
    original, replays = setup_run(sources, adapter, ids)
    folder = replays[0]
    manifest = json.loads((folder / "manifest.json").read_text())
    row = json.loads((folder / "records.jsonl").read_text())
    if fault == "wrong_prefix":
        manifest["context"]["input_ids"][0] += 1
    elif fault == "wrong_trajectory":
        manifest["context"]["trajectory_id"] = "unrelated-interactive-run"
    elif fault == "generation_phase":
        manifest["phase"] = "generation"
    elif fault == "different_activations":
        values = np.load(folder / "block.npy")
        np.save(folder / "block.npy", values + 1)
    elif fault == "missing_tensor":
        (folder / "block.npy").unlink()
    elif fault == "outside_tensor":
        row["filename"] = "../elsewhere.npy"
    elif fault == "future_span":
        put(
            sources["cases"][0]["capture"].parent / "m1-context.json",
            {"reasoning_token_span": [1, 5]},
        )
    elif fault == "duplicate_replay":
        shutil.copytree(folder, sources["tensors"] / "duplicate")
    elif fault == "mlp_output":
        row["module"] += ".mlp"
    put(folder / "manifest.json", manifest)
    (folder / "records.jsonl").write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="Incomplete reasoning coverage"):
        compare(sources, original)
    output = sources["root"] / "mean-comparison"
    coverage = json.loads((output / "coverage.json").read_text())
    assert coverage["excluded"]
    assert not (output / "models").exists()


def test_audit_only_reports_missing_coverage_without_training(sources, adapter, ids):  # noqa: F811
    original, replays = setup_run(sources, adapter, ids)
    shutil.rmtree(replays[0])
    result = compare(sources, original, audit_only=True)
    assert result["status"] == "audit_only"
    assert not (sources["root"] / "mean-comparison/models").exists()


def test_dense_reasoning_capture_needs_no_hook_original(sources, adapter, ids):  # noqa: F811
    original, replays = setup_run(sources, adapter, ids, dense=True)
    for path in replays:
        shutil.rmtree(path)
    result = compare(sources, original)
    assert result["complete_source_coverage"]
    provenance = json.loads(
        (sources["root"] / "mean-comparison/extraction-provenance.json").read_text()
    )
    assert all(row["source_kind"] == "dense_reasoning_C01" for row in provenance)


def test_partial_comparison_fits_both_arms_on_complete_shared_trajectories(sources, adapter, ids):  # noqa: F811
    original, replays = setup_run(
        sources, adapter, ids, extra_normal_step=True, extra_validation_normal=True
    )
    shutil.rmtree(replays[-1])
    result = compare(sources, original, allow_partial=True)
    assert not result["complete_source_coverage"]
    assert result["sample_counts"]["validation"]["source_actions"] == 4
    assert result["sample_counts"]["validation"]["paired_actions"] == 2
    assert all(row["normal_trajectory_count"] == 1 for row in result["results"])
    output = sources["root"] / "mean-comparison"
    last = json.loads((output / "last_token-validation-scores.json").read_text())
    mean = json.loads((output / "reasoning_mean-validation-scores.json").read_text())
    assert [row["action_id"] for row in last] == [row["action_id"] for row in mean]


def test_partial_coverage_removes_whole_normal_trajectory(sources, adapter, ids):  # noqa: F811
    original, replays = setup_run(sources, adapter, ids, extra_normal_step=True)
    shutil.rmtree(replays[-1])
    with pytest.raises(ValueError, match="Paired validation needs both label classes"):
        compare(sources, original, allow_partial=True)
    coverage = json.loads((sources["root"] / "mean-comparison/coverage.json").read_text())
    assert coverage["sample_counts"]["validation"]["source_actions"] == 3
    assert coverage["sample_counts"]["validation"]["paired_actions"] == 1
    assert {r["action_id"] for r in coverage["excluded"]} == {"trajectory-2:0", "trajectory-2:1"}


def test_legacy_feature_specs_default_to_unpooled():
    identity = {
        "model_id": "m",
        "model_revision": "r",
        "tokenizer_id": "t",
        "tokenizer_revision": "r",
    }
    legacy = {
        "identity": identity,
        "config_sha256": "a" * 64,
        "model_type": "gpt2",
        "layers": [0],
        "hidden_sizes": [32],
        "module_paths": ["transformer.h.0"],
    }
    assert FeatureSpec.from_dict(legacy).pooling == "none"
