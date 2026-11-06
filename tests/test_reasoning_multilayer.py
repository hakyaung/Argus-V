"""Synthetic models: layer means/concatenation, replay reuse and sealed test data."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch
from test_probe_assembly import sources  # noqa: F401
from test_reasoning_mean import comparison, setup_run
from test_reasoning_replay import replayer
from transformers import GPT2Config, GPT2LMHeadModel, GptOssConfig, GptOssForCausalLM

from argus_v.activations import BlockAdapter, ModelIdentity
from argus_v.probes import load_probe, load_probe_dataset
from argus_v.probes.data import FeatureSpec
from argus_v.probes.reasoning import combine_layers, resolve_layers

ROOT = Path(__file__).resolve().parents[1]
LAYERS = (0, 11, 23)


@pytest.fixture(params=["gpt2", "gpt_oss"])
def adapter(request):
    torch.manual_seed(7)
    torch.set_num_threads(1)
    if request.param == "gpt2":
        config = GPT2Config(vocab_size=64, n_embd=32, n_layer=24, n_head=4, n_positions=64)
        config._attn_implementation = "eager"
        model = GPT2LMHeadModel(config)
    else:
        config = GptOssConfig(
            vocab_size=64,
            hidden_size=32,
            intermediate_size=32,
            num_hidden_layers=24,
            num_attention_heads=4,
            num_key_value_heads=2,
            head_dim=8,
            num_local_experts=4,
            num_experts_per_tok=2,
            max_position_embeddings=64,
            sliding_window=8,
            rope_scaling=None,
        )
        config._attn_implementation = "eager"
        config._experts_implementation = "eager"
        model = GptOssForCausalLM(config)
    return BlockAdapter.from_model(
        model.eval(), ModelIdentity(f"tiny-24-{request.param}", "seed-7", "synthetic", "v1")
    )


def replay(sources, original, adapter, layers, **kwargs):  # noqa: F811
    return replayer.replay(
        ROOT,
        original,
        sources["root"] / "multi-replays",
        sources["root"] / "checkpoint",
        layers=layers,
        adapter=adapter,
        **kwargs,
    )


def compare(sources, original, roots, **kwargs):  # noqa: F811
    return comparison.compare(
        ROOT,
        original,
        sources["root"] / "multi-comparison",
        roots,
        layers=LAYERS,
        weight_decay=1,
        **kwargs,
    )


@pytest.mark.parametrize("aggregation", ["concat", "mean"])
def test_reuse_l23_and_replay_missing_layers_with_explicit_aggregation(
    sources,  # noqa: F811
    adapter,
    ids,
    monkeypatch,  # noqa: F811
    aggregation,
):
    original, old = setup_run(sources, adapter, ids, layer=23)
    weights = {k: v.clone() for k, v in adapter.model.state_dict().items()}
    probe_weights = (original / "model/weights.npz").read_bytes()
    old_bytes = (old[0] / "block.npy").read_bytes()
    test = sources["cases"][-1]["capture"]
    (test / "metadata.json").write_text("sealed test metadata")
    (test / "features.npz").write_text("sealed test activation")
    (old[-1] / "block.npy").write_text("sealed test Hook tensor")

    def forbidden(*args, **kwargs):
        raise AssertionError("generator.generate must not run")

    monkeypatch.setattr(adapter.model, "generate", forbidden)
    first = replay(sources, original, adapter, (0, 11), max_actions=1)
    assert first["completed_actions"] == 1 and first["pending_actions"] == 3
    folder = next((sources["root"] / "multi-replays").glob("*/manifest.json")).parent
    before = (folder / "block_0000.npy").stat().st_mtime_ns
    final = replay(sources, original, adapter, (0, 11))
    assert final["completed_actions"] == 4 and final["pending_actions"] == 0
    assert (folder / "block_0000.npy").stat().st_mtime_ns == before
    result = compare(
        sources,
        original,
        [sources["root"] / "multi-replays", sources["tensors"]],
        layer_aggregation=aggregation,
    )
    assert result["complete_source_coverage"] and not result["test_features_opened"]
    out = sources["root"] / "multi-comparison"
    mean = load_probe(out / "models/reasoning_mean")
    assert mean.spec.layers == LAYERS
    assert mean.spec.input_dim == (32 if aggregation == "mean" else 96)
    assert mean.spec.layer_aggregation == aggregation
    assert mean.spec.pooling == "reasoning_mean"
    assert mean.training_report["training_config"]["weight_decay"] == 1
    records = json.loads((out / "paired-samples.json").read_text())
    expected = []
    for row in records["train"]:
        replay_folder = sources["root"] / "multi-replays" / row["snapshot_id"]
        l23 = next(
            p
            for p in old
            if json.loads((p / "manifest.json").read_text())["context"]["action"]["action_id"]
            == row["action_id"]
        )
        vectors = [
            np.load(replay_folder / f"block_{layer:04d}.npy")[0, 1:3]
            .mean(axis=0, dtype=np.float64)
            .astype(np.float32)
            for layer in (0, 11)
        ]
        vectors.append(
            np.load(l23 / "block.npy")[0, 1:3].mean(axis=0, dtype=np.float64).astype(np.float32)
        )
        expected.append(
            np.stack(vectors).mean(axis=0, dtype=np.float64).astype(np.float32)
            if aggregation == "mean"
            else np.concatenate(vectors)
        )
    with np.load(out / "paired-features.npz") as arrays:
        np.testing.assert_array_equal(arrays["train_reasoning_mean"], np.stack(expected))
        if aggregation == "mean":
            original_train = load_probe_dataset(
                original / "dataset/manifest.json", split="train", layers=LAYERS
            )
            expected_last = (
                original_train.features.reshape(-1, 3, 32)
                .mean(axis=1, dtype=np.float64)
                .astype(np.float32)
            )
            np.testing.assert_array_equal(arrays["train_last_token"], expected_last)
            # Normalization follows both averages and uses train samples only.
            expected_mean = np.stack(expected).astype(np.float64).mean(axis=0).astype(np.float32)
            np.testing.assert_array_equal(mean.model.mean.numpy(), expected_mean)
    plan = json.loads((out / "plan.json").read_text())
    assert plan["layer_aggregation"] == aggregation
    assert all(r["input_dim"] == mean.spec.input_dim for r in result["results"])
    provenance = json.loads((out / "extraction-provenance.json").read_text())
    assert all(r["layer_order"] == list(LAYERS) for r in provenance)
    assert all([p["layer"] for p in r["per_layer"]] == list(LAYERS) for r in provenance)
    assert all(r["layer_aggregation"] == aggregation for r in provenance)
    for mode in ("last_token", "reasoning_mean"):
        score_rows = json.loads((out / f"{mode}-validation-scores.json").read_text())
        assert [r["action_id"] for r in score_rows] == [
            r["action_id"] for r in records["validation"]
        ]
        assert [r["label"] for r in score_rows] == [
            r["next_action_violation"] for r in records["validation"]
        ]
    assert all(torch.equal(v, weights[k]) for k, v in adapter.model.state_dict().items())
    assert (original / "model/weights.npz").read_bytes() == probe_weights
    assert (old[0] / "block.npy").read_bytes() == old_bytes


def test_one_multi_layer_replay_checks_middle_layer_integrity(sources, adapter, ids):  # noqa: F811
    original, _ = setup_run(sources, adapter, ids, layer=23)
    replay(sources, original, adapter, LAYERS)
    root = sources["root"] / "multi-replays"
    first = next(root.glob("*/block_0011.npy"))
    np.save(first, np.load(first) + 1)
    with pytest.raises(ValueError, match="completed replay changed"):
        replay(sources, original, adapter, LAYERS)
    with pytest.raises(ValueError, match="Incomplete reasoning coverage"):
        compare(sources, original, [root])
    coverage = json.loads((sources["root"] / "multi-comparison/coverage.json").read_text())
    assert any("differs from original C-01" in r["reason"] for r in coverage["excluded"])
    assert not (sources["root"] / "multi-comparison/models").exists()


@pytest.mark.parametrize("aggregation", ["concat", "mean"])
def test_missing_middle_layer_excludes_whole_trajectory_in_both_arms(
    sources,  # noqa: F811
    adapter,
    ids,  # noqa: F811
    aggregation,
):
    original, _ = setup_run(
        sources, adapter, ids, layer=23, extra_normal_step=True, extra_validation_normal=True
    )
    replay(sources, original, adapter, (0, 11))
    root = sources["root"] / "multi-replays"
    for path in root.glob("*/manifest.json"):
        meta = json.loads(path.read_text())
        if meta["context"]["action"]["action_id"] == "trajectory-2:1":
            (path.parent / "block_0011.npy").unlink()
            break
    else:
        pytest.fail("fixture missing second normal step")
    result = compare(
        sources,
        original,
        [root, sources["tensors"]],
        allow_partial=True,
        layer_aggregation=aggregation,
    )
    assert result["sample_counts"]["validation"]["paired_actions"] == 2
    assert all(r["normal_trajectory_count"] == 1 for r in result["results"])
    out = sources["root"] / "multi-comparison"
    coverage = json.loads((out / "coverage.json").read_text())
    assert {r["action_id"] for r in coverage["excluded"]} == {"trajectory-2:0", "trajectory-2:1"}
    a = json.loads((out / "last_token-validation-scores.json").read_text())
    b = json.loads((out / "reasoning_mean-validation-scores.json").read_text())
    assert [(r["snapshot_id"], r["label"]) for r in a] == [
        (r["snapshot_id"], r["label"]) for r in b
    ]


def test_dense_three_layer_means_need_no_replay(sources, adapter, ids):  # noqa: F811
    original, old = setup_run(sources, adapter, ids, layer=23, dense=True)
    for path in old:
        shutil.rmtree(path)
    result = compare(sources, original, [])
    assert result["complete_source_coverage"]
    out = sources["root"] / "multi-comparison"
    records = json.loads((out / "paired-samples.json").read_text())["train"]
    capture = original / "dataset" / records[0]["capture_dir"]
    with np.load(capture / "features.npz") as arrays:
        expected = np.concatenate(
            [
                arrays[f"block_{i:04d}"][1:3].mean(axis=0, dtype=np.float64).astype(np.float32)
                for i in LAYERS
            ]
        )
    with np.load(out / "paired-features.npz") as arrays:
        np.testing.assert_array_equal(arrays["train_reasoning_mean"][0], expected)


@pytest.mark.parametrize("layers", [(), (0, 23, 11), (0, 11, 11), (-1, 11, 23), (True, 11, 23)])
def test_invalid_layer_layout_is_rejected(layers):
    with pytest.raises(ValueError, match="layers must be"):
        resolve_layers(layers=layers)


def test_layer_mean_audit_does_not_train(sources, adapter, ids, monkeypatch):  # noqa: F811
    original, _ = setup_run(sources, adapter, ids, layer=23, dense=True)

    def forbidden(*args, **kwargs):
        raise AssertionError("audit must not train a probe")

    monkeypatch.setattr("argus_v.probes.fit_probe", forbidden)
    result = compare(sources, original, [], layer_aggregation="mean", audit_only=True)
    assert result["status"] == "audit_only"
    assert not (sources["root"] / "multi-comparison/models").exists()


def test_layer_aggregation_has_distinct_dimensions_and_roundtrips():
    spec = FeatureSpec.from_dict(
        {
            "identity": {
                "model_id": "m",
                "model_revision": "r",
                "tokenizer_id": "t",
                "tokenizer_revision": "r",
            },
            "config_sha256": "a" * 64,
            "model_type": "gpt_oss",
            "layers": [0, 11, 23],
            "hidden_sizes": [2880, 2880, 2880],
            "module_paths": ["l0", "l11", "l23"],
            "pooling": "reasoning_mean",
        }
    )
    averaged = replace(spec, layer_aggregation="mean")
    assert spec.input_dim == 8640 and averaged.input_dim == 2880
    assert "layer_aggregation" not in spec.to_dict()  # Legacy replay plans remain resumable.
    assert FeatureSpec.from_dict(averaged.to_dict()) == averaged
    assert FeatureSpec.from_dict(spec.to_dict()) == spec
    with pytest.raises(ValueError, match="equal hidden sizes"):
        replace(averaged, hidden_sizes=(2880, 1440, 2880))
    with pytest.raises(ValueError, match="unsupported layer aggregation"):
        replace(spec, layer_aggregation="unknown")


def test_coordinate_mean_is_equal_weight_and_rejects_unequal_sizes():
    vectors = [
        np.array([1, 9], dtype=np.float32),
        np.array([4, 3], dtype=np.float32),
        np.array([7, 0], dtype=np.float32),
    ]
    np.testing.assert_array_equal(combine_layers(vectors, "mean"), np.array([4, 4], np.float32))
    with pytest.raises(ValueError, match="equal hidden sizes"):
        combine_layers([vectors[0], np.ones(3)], "mean")


def test_compact_split_three_layer_roots_preserves_inputs_and_resume(sources, adapter, ids):  # noqa: F811
    from test_reasoning_pooled import compactor

    original, _ = setup_run(sources, adapter, ids, layer=23)
    replay(sources, original, adapter, (0, 11))
    roots = [sources["root"] / "multi-replays", sources["tensors"]]
    result = compare(sources, original, roots, layer_aggregation="mean")
    assert result["complete_source_coverage"]
    with np.load(sources["root"] / "multi-comparison/paired-features.npz") as archive:
        expected = {key: archive[key].copy() for key in archive.files}
    summary = compactor.compact(ROOT, original, roots, apply=True, delete_source_tensors=True)
    assert summary["converted_actions"] == 8 and summary["reclaimed_bytes"] > 0
    resumed = replay(sources, original, adapter, (0, 11), storage="pooled")
    assert resumed["completed_actions"] == 4 and resumed["pending_actions"] == 0
    comparison.compare(
        ROOT,
        original,
        sources["root"] / "compact-comparison",
        roots,
        layers=LAYERS,
        layer_aggregation="mean",
    )
    with np.load(sources["root"] / "compact-comparison/paired-features.npz") as archive:
        for key, value in expected.items():
            np.testing.assert_array_equal(archive[key], value)
