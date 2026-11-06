"""Replay saved train/validation token IDs locally; no generation or sandbox execution.

The CLI stores small float32 per-layer summaries by default. --storage full keeps
legacy full-prefix tensors. Every replay must reproduce original C-01 rows.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def replay(
    root,
    run_dir,
    output,
    model_dir,
    *,
    layer=23,
    layers=None,
    max_actions=None,
    adapter=None,
    storage="full",
):
    sys.path.insert(0, str(root / "src"))
    import numpy as np
    import torch

    from argus_v.activations import (
        ActionSpan,
        BlockAdapter,
        ModelIdentity,
        SnapshotContext,
        extract_prefix,
    )
    from argus_v.probes import load_probe
    from argus_v.probes.data import file_sha256
    from argus_v.probes.pooled import PHASE, read_pooled, token_mean, write_pooled
    from argus_v.probes.reasoning import read_json, reasoning_span, resolve_layers, source_views

    layers = resolve_layers(layer, layers)
    if storage not in ("full", "pooled"):
        raise ValueError("storage must be full or pooled")
    if max_actions is not None and max_actions < 1:
        raise ValueError("max-actions must be positive")
    baseline = load_probe(run_dir / "model")
    manifest = run_dir / "dataset/manifest.json"
    manifest_hash = file_sha256(manifest)
    if (
        manifest_hash != baseline.training_report["manifest_sha256"]
        or baseline.spec.pooling != "none"
    ):
        raise ValueError("expected the unchanged original reviewed run")
    views = source_views(manifest, baseline, layers)
    spec = views["train"].spec
    filenames = {
        layer: "block.npy" if len(layers) == 1 else f"block_{layer:04d}.npy" for layer in layers
    }
    plan = {
        "schema_version": "argus-v.reasoning-replay-plan.v1",
        "source_manifest": str(manifest),
        "manifest_sha256": manifest_hash,
        "model_dir": str(model_dir),
        "feature_spec": json.loads(json.dumps(spec.to_dict())),
        "test_features_opened": False,
        "generation_performed": False,
    }
    output.mkdir(parents=True, exist_ok=True)
    plan_file = output / "replay-plan.json"
    if plan_file.exists():
        if read_json(plan_file) != plan:
            raise ValueError("output belongs to a different replay plan; choose a new directory")
    elif any(output.iterdir()):
        raise ValueError("output is not an empty replay directory")
    else:
        write_json(plan_file, plan)
    jobs, excluded, completed = [], [], []
    for split, dataset in views.items():
        for record in dataset.records:
            capture = (manifest.parent / record["capture_dir"]).resolve()
            meta = read_json(capture / "metadata.json")
            try:
                span = reasoning_span(capture, meta)
            except (ValueError, KeyError, TypeError, OSError) as error:
                excluded.append(
                    {"action_id": record["action_id"], "split": split, "reason": str(error)}
                )
                continue
            folder = output / record["snapshot_id"]
            done = folder / "manifest.json"
            if done.exists():
                saved = read_json(done)
                if saved.get("phase") == PHASE:
                    if saved.get("selected_layers") != list(layers):
                        raise ValueError(f"completed pooled layer layout changed: {folder}")
                    read_pooled(done, saved, meta, spec, span, capture)
                    completed.append(record["action_id"])
                    continue
                artifacts = (
                    {"block.npy": saved.get("artifact_sha256")}
                    if len(layers) == 1
                    else saved.get("artifacts_sha256", {})
                )
                if (
                    saved.get("schema_version") != "argus-v.hook-inspection.v1"
                    or saved.get("status") != "complete"
                    or saved.get("source_c01_archive_sha256") != meta["artifact"]["sha256"]
                    or saved.get("context", {}).get("run_id") != record["run_id"]
                    or saved.get("prefix_ids_sha256") != meta["prefix_ids_sha256"]
                    or saved.get("selected_layers") != list(layers)
                    or set(artifacts) != set(filenames.values())
                    or any(
                        artifacts[name] != file_sha256(folder / name) for name in filenames.values()
                    )
                    or saved.get("records_sha256") != file_sha256(folder / "records.jsonl")
                ):
                    raise ValueError(f"completed replay changed: {folder}")
                completed.append(record["action_id"])
            else:
                jobs.append((record, capture, meta, span, folder))
    print(f"Output: {output}", flush=True)
    print(
        f"Pending={len(jobs)}, already complete={len(completed)}, invalid spans={len(excluded)}",
        flush=True,
    )
    write_json(output / "replay-exclusions.json", excluded)
    selected = jobs[:max_actions] if max_actions is not None else jobs
    if selected and adapter is None:
        # Match the existing scenario runner's loading options and keep all
        # checkpoint/tokenizer access local. No model.generate() is called.
        sys.path.insert(0, str(root / "argus_hook"))
        import load_full_model_CUDA as loader

        loader.MODEL_DIR = str(model_dir)
        _, model = loader.load_moe_model()
        adapter = BlockAdapter.from_model(model, ModelIdentity(**asdict(spec.identity)))
    for number, (record, capture, meta, span, folder) in enumerate(selected, 1):
        sidecar = read_json(capture.parent / "c01-input.json")
        ids = sidecar["input_ids"]
        context = SnapshotContext(
            **{**meta["context"], "action": ActionSpan(**meta["context"]["action"])}
        )
        if adapter.identity != spec.identity or adapter.model_type != spec.model_type:
            raise ValueError("loaded model identity/type differs from original")
        if tuple(adapter.block_paths[layer] for layer in layers) != spec.module_paths:
            raise ValueError("loaded block path differs from original")
        print(
            f"Replaying {number}/{len(selected)}: {record['action_id']} ({len(ids)} tokens)",
            flush=True,
        )
        full = extract_prefix(
            adapter,
            torch.tensor([ids], device=adapter.model.get_input_embeddings().weight.device),
            context,
            layers=layers,
            token_positions=tuple(range(len(ids))),
        )
        if full.metadata["config_sha256"] != spec.config_sha256:
            raise ValueError("loaded model configuration differs from original C-01")
        positions = [row["token_index"] for row in meta["alignment"]]
        with np.load(capture / "features.npz", allow_pickle=False) as archive:
            for layer in layers:
                values = full.features[f"block_{layer:04d}"].numpy()
                if not np.array_equal(values[positions], archive[f"block_{layer:04d}"]):
                    raise ValueError(
                        f"replay differs from original C-01 at {record['action_id']} "
                        f"layer {layer}; "
                        "use the original checkpoint/runtime, not a newly generated trajectory"
                    )
        folder.mkdir(parents=True, exist_ok=True)
        if storage == "pooled":
            arrays = {}
            for layer in layers:
                key = f"block_{layer:04d}"
                values = full.features[key].numpy()
                arrays[f"mean_{key}"] = token_mean(values, span)
                arrays[f"last_{key}"] = values[-1].copy()
                arrays[f"reference_{key}"] = values[positions].copy()
            write_pooled(folder, meta, spec, span, capture, arrays)
            del values, full, arrays
            completed.append(record["action_id"])
            continue
        rows = []
        for layer, size, module in zip(layers, spec.hidden_sizes, spec.module_paths, strict=True):
            values = full.features[f"block_{layer:04d}"].numpy()
            np.save(folder / filenames[layer], values[None], allow_pickle=False)
            rows.append(
                {
                    "kind": "tensor",
                    "module": module,
                    "layer_index": layer,
                    "location": "output",
                    "shape": [1, len(ids), size],
                    "meaning": "post-residual block output, before final norm",
                    "filename": filenames[layer],
                }
            )
        (folder / "records.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        hashes = {name: file_sha256(folder / name) for name in filenames.values()}
        # Publish complete metadata last; interrupted samples can be replayed.
        write_json(
            folder / "manifest.json",
            {
                "schema_version": "argus-v.hook-inspection.v1",
                "status": "complete",
                "phase": "pre_action_full_prefix_replay",
                "identity": asdict(spec.identity),
                "model_type": spec.model_type,
                "selected_layers": list(layers),
                "context": {**asdict(context), "input_ids": ids},
                "prefix_ids_sha256": meta["prefix_ids_sha256"],
                "reasoning_span": list(span),
                "source_c01_archive_sha256": meta["artifact"]["sha256"],
                **(
                    {"artifact_sha256": hashes["block.npy"]}
                    if len(layers) == 1
                    else {"artifacts_sha256": hashes}
                ),
                "records_sha256": file_sha256(folder / "records.jsonl"),
            },
        )
        completed.append(record["action_id"])
        del values, full
    summary = {
        "output": str(output),
        "completed_actions": len(completed),
        "pending_actions": len(jobs) - len(selected),
        "excluded_actions": len(excluded),
        "test_features_opened": False,
        "generation_performed": False,
        "new_action_storage": storage,
        "limitation": "Excluded spans require whole-trajectory exclusion in paired comparison.",
    }
    write_json(output / "replay-summary.json", summary)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    layout = parser.add_mutually_exclusive_group()
    layout.add_argument("--layer", type=int, default=23)
    layout.add_argument(
        "--layers", type=int, nargs="+", help="e.g. 0 11 23; one forward per prefix"
    )
    parser.add_argument("--max-actions", type=int)
    parser.add_argument(
        "--storage",
        choices=("pooled", "full"),
        default="pooled",
        help="pooled: save per-layer mean/last/reference rows only; full: legacy NPYs",
    )
    args = parser.parse_args()
    try:
        replay(
            args.repo_root.resolve(),
            args.run_dir.resolve(),
            args.output.resolve(),
            args.model_dir.resolve(),
            layer=args.layer,
            layers=args.layers,
            max_actions=args.max_actions,
            storage=args.storage,
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.exit(
            2, f"Replay stopped: {error}\nCompleted artifacts remain resumable: {args.output}\n"
        )


if __name__ == "__main__":
    main()
