"""Exact, pre-action reasoning means; never substitute sparse observations or generations."""

import json
from collections import defaultdict
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import numpy as np

from .data import file_sha256, load_probe_dataset


def resolve_layers(layer=23, layers=None):
    selected = tuple(layers) if layers is not None else (layer,)
    if (
        not selected
        or any(type(i) is not int or i < 0 for i in selected)
        or selected != tuple(sorted(set(selected)))
    ):
        raise ValueError("layers must be nonempty, unique, increasing nonnegative block indexes")
    return selected


def source_views(manifest, baseline, layers):
    """Verify the original layer layout before loading another layout of the same samples."""
    views = {}
    for split in ("train", "validation"):
        original = load_probe_dataset(manifest, split=split, layers=baseline.spec.layers)
        if original.fingerprint != baseline.training_report[f"{split}_fingerprint"]:
            raise ValueError(f"{split} source fingerprint changed")
        dataset = (
            original
            if layers == baseline.spec.layers
            else load_probe_dataset(manifest, split=split, layers=layers)
        )
        if dataset.records != original.records or not np.array_equal(
            dataset.labels, original.labels
        ):
            raise ValueError(f"{split} sample alignment changed across layer layouts")
        comparable = replace(
            dataset.spec,
            layers=original.spec.layers,
            hidden_sizes=original.spec.hidden_sizes,
            module_paths=original.spec.module_paths,
        )
        if comparable != original.spec:
            raise ValueError(f"{split} source model/configuration changed across layer layouts")
        views[split] = dataset
    return views


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prefix_digest(ids):
    return sha256(json.dumps(ids, separators=(",", ":")).encode("ascii")).hexdigest()


def capture_key(metadata):
    context = metadata["context"]
    action = context.get("action") or {}
    step = context.get("step", action.get("step_index"))
    if (
        not isinstance(context.get("run_id"), str)
        or not isinstance(context.get("trajectory_id"), str)
        or type(step) is not int
        or step < 0
    ):
        raise ValueError("missing exact run/trajectory/step coordinates")
    return (
        context["run_id"],
        context["trajectory_id"],
        step,
        digest(metadata["identity"]),
        metadata["prefix_ids_sha256"],
    )


def index_replays(roots, wanted):
    """Read Hook manifests only. Unrelated and test tensor files are never opened."""
    found, issues, seen = defaultdict(list), [], set()
    for root in roots:
        if not Path(root).is_dir():
            issues.append({"path": str(root), "reason": "Hook root directory not found"})
            continue
        for path in sorted(Path(root).rglob("manifest.json")):
            path = path.resolve()
            if path in seen:
                continue
            seen.add(path)
            try:
                metadata = read_json(path)
                if not isinstance(metadata, dict):
                    continue
                if metadata.get("schema_version") != "argus-v.hook-inspection.v1" or metadata.get(
                    "phase"
                ) not in ("pre_action_full_prefix_replay", "pre_action_pooled_replay"):
                    continue
                ids = metadata.get("context", {}).get("input_ids")
                if (
                    not isinstance(ids, list)
                    or not ids
                    or any(type(i) is not int or i < 0 for i in ids)
                ):
                    raise ValueError("invalid replay input_ids")
                metadata["prefix_ids_sha256"] = prefix_digest(ids)
                key = capture_key(metadata)
                if key not in wanted:
                    continue
                if metadata.get("status") != "complete":
                    raise ValueError("replay did not complete")
                found[key].append((path, metadata))
            except (ValueError, KeyError, TypeError, OSError) as error:
                issues.append({"path": str(path), "reason": str(error)})
    return found, issues


def reasoning_span(capture, metadata):
    """Use runner's original token coordinates, not decoded character offsets."""
    sidecar = read_json(capture.parent / "c01-input.json")
    ids = sidecar.get("input_ids")
    if (
        not isinstance(ids, list)
        or not ids
        or any(type(i) is not int or i < 0 for i in ids)
        or len(ids) != metadata["observed_prefix_length"]
        or prefix_digest(ids) != metadata["prefix_ids_sha256"]
        or sidecar.get("identity") != metadata["identity"]
    ):
        raise ValueError("original prefix sidecar does not match C-01")
    span = read_json(capture.parent / "m1-context.json").get("reasoning_token_span")
    if (
        not isinstance(span, list)
        or len(span) != 2
        or any(type(i) is not int for i in span)
        or not 0 <= span[0] < span[1] <= len(ids)
        or span[1] > metadata["context"]["action"]["token_start"]
    ):
        raise ValueError("missing/empty/invalid pre-action reasoning span")
    generation_file = capture.parent / "agent-generation.json"
    if generation_file.is_file():
        generation = read_json(generation_file)
        if (
            generation.get("cutoff") != len(ids)
            or generation.get("input_ids", [])[: len(ids)] != ids
            or generation.get("reasoning_span") != span
        ):
            raise ValueError("reasoning span or prefix conflicts with original generation")
    return tuple(span)


def safe_tensor_path(directory, relative):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("tensor path escapes replay directory")
    return path


def full_replay_mean(path, hook, metadata, spec, span, capture):
    """Only the post-residual block output, with the exact entire prefix and model."""
    context = hook["context"]
    original = metadata["context"]
    scenario = context.get("scenario_id")
    if scenario is not None and scenario != original["scenario_id"]:
        raise ValueError("replay scenario mismatch")
    action = context.get("action")
    if action is not None and action != original["action"]:
        raise ValueError("replay action annotation mismatch")
    if hook.get("model_type") != spec.model_type:
        raise ValueError("replay model type mismatch")
    matches = []
    records = path.parent / "records.jsonl"
    with records.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if (
                row.get("kind") == "tensor"
                and row.get("module") == spec.module_paths[0]
                and row.get("layer_index") == spec.layers[0]
                and row.get("location") in ("output", "output[0]")
                and row.get("meaning") == "post-residual block output, before final norm"
            ):
                matches.append(row)
    if len(matches) != 1:
        raise ValueError("replay needs exactly one full-prefix post-block output")
    row = matches[0]
    shape = [1, metadata["observed_prefix_length"], spec.input_dim]
    if row.get("shape") != shape:
        raise ValueError("replay is not a complete [1, prefix, hidden] tensor")
    tensor_path = safe_tensor_path(path.parent, row["filename"])
    values = np.load(tensor_path, mmap_mode="r", allow_pickle=False)
    if list(values.shape) != shape or values.dtype.kind != "f":
        raise ValueError("NPY shape/dtype conflicts with replay record")
    # The Hook manifest has no config hash. Verify every overlapping original
    # C-01 row exactly, rather than trusting a model name or directory spelling.
    positions = [r["token_index"] for r in metadata["alignment"]]
    with np.load(capture / "features.npz", allow_pickle=False) as archive:
        reference = archive[f"block_{spec.layers[0]:04d}"]
        if not np.array_equal(values[0, positions].astype(np.float32), reference):
            raise ValueError("full replay differs from original C-01 activations")
    start, end = span
    total = np.zeros(spec.input_dim, dtype=np.float64)
    for offset in range(start, end, 128):
        chunk = values[0, offset : min(offset + 128, end)]
        if not np.isfinite(chunk).all():
            raise ValueError("non-finite reasoning activation")
        total += chunk.sum(axis=0, dtype=np.float64)
    vector = (total / (end - start)).astype(np.float32)
    return vector, {
        "source_kind": "full_prefix_hook_replay",
        "hook_manifest": str(path),
        "hook_manifest_sha256": file_sha256(path),
        "records_sha256": file_sha256(records),
        "tensor": str(tensor_path),
        "tensor_sha256": file_sha256(tensor_path),
    }


def combine_layers(vectors, aggregation):
    """Combine raw activation coordinates before train-only feature standardization."""
    if aggregation == "concat":
        return np.concatenate(vectors, axis=-1)
    if aggregation != "mean":
        raise ValueError("unsupported layer aggregation")
    if len({v.shape for v in vectors}) != 1:
        raise ValueError("layer mean requires equal hidden sizes")
    return np.stack(vectors).mean(axis=0, dtype=np.float64).astype(np.float32)


def extract_mean(capture, metadata, spec, span, replays, *, layer_aggregation="concat"):
    if len(spec.layers) > 1:
        vectors, origins = [], []
        for layer, size, module in zip(
            spec.layers, spec.hidden_sizes, spec.module_paths, strict=True
        ):
            single = replace(spec, layers=(layer,), hidden_sizes=(size,), module_paths=(module,))
            vector, origin = extract_mean(capture, metadata, single, span, replays)
            vectors.append(vector)
            origins.append({"layer": layer, "input_dim": size, **origin})
        return combine_layers(vectors, layer_aggregation), {
            "source_kind": (
                "mean_of_layer_reasoning_means"
                if layer_aggregation == "mean"
                else "concatenated_layer_reasoning_means"
            ),
            "layer_aggregation": layer_aggregation,
            "layer_order": list(spec.layers),
            "per_layer": origins,
        }
    positions = {r["token_index"]: i for i, r in enumerate(metadata["alignment"])}
    start, end = span
    if all(i in positions for i in range(start, end)):
        with np.load(capture / "features.npz", allow_pickle=False) as archive:
            rows = archive[f"block_{spec.layers[0]:04d}"]
            selected = rows[[positions[i] for i in range(start, end)]]
            if not np.isfinite(selected).all():
                raise ValueError("non-finite reasoning activation")
            vector = selected.mean(axis=0, dtype=np.float64).astype(np.float32)
        return vector, {"source_kind": "dense_reasoning_C01"}
    matches = []
    for path, hook in replays.get(capture_key(metadata), []):
        selected = hook.get("selected_layers")
        if selected is not None and spec.layers[0] not in selected:
            continue
        if hook.get("phase") == "pre_action_pooled_replay":
            matches.append((path, hook))
            continue
        # Separate roots may contain the same prefix for different layers.
        # Read only the tensor index to choose a layer; never open unrelated NPYs.
        with (path.parent / "records.jsonl").open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        if any(r.get("layer_index") == spec.layers[0] for r in rows):
            matches.append((path, hook))
    if not matches:
        raise ValueError("no exact full-prefix replay; sparse C-01 is not a sentence mean")
    if len(matches) != 1:
        raise ValueError("ambiguous full-prefix replay copies; provide a narrower --hook-roots")
    path, hook = matches[0]
    if hook.get("phase") == "pre_action_pooled_replay":
        from .pooled import read_pooled

        vector = read_pooled(path, hook, metadata, spec, span, capture)[spec.layers[0]]
        archive = path.parent / hook["pooled_artifact"]["filename"]
        return vector, {
            "source_kind": "verified_pooled_replay",
            "hook_manifest": str(path),
            "hook_manifest_sha256": file_sha256(path),
            "pooled_archive": str(archive),
            "pooled_archive_sha256": file_sha256(archive),
        }
    return full_replay_mean(path, hook, metadata, spec, span, capture)


def paired_datasets(dataset, indexes, vectors, extraction_sha256, *, layer_aggregation="concat"):
    """Persist distinct feature semantics and fingerprints for the two input layouts."""
    records = tuple(dataset.records[i] for i in indexes)
    original = tuple(dataset.snapshots[i] for i in indexes)
    last = dataset.features[indexes]
    if layer_aggregation == "mean":
        parts = np.split(last, np.cumsum(dataset.spec.hidden_sizes)[:-1], axis=-1)
        last = combine_layers(parts, layer_aggregation)
    result = {}
    for name, matrix, pooling in (
        ("last_token", last, "none"),
        ("reasoning_mean", np.stack(vectors), "reasoning_mean"),
    ):
        spec = replace(dataset.spec, pooling=pooling, layer_aggregation=layer_aggregation)
        snapshots = tuple(
            replace(s, spec=spec, features=matrix[i].copy()) for i, s in enumerate(original)
        )
        fingerprint = digest(
            {
                "source_fingerprint": dataset.fingerprint,
                "extraction_sha256": extraction_sha256,
                "feature_spec": spec.to_dict(),
                "snapshot_ids": [r["snapshot_id"] for r in records],
                "feature_sha256": sha256(matrix.tobytes()).hexdigest(),
            }
        )
        result[name] = replace(
            dataset,
            features=matrix,
            labels=dataset.labels[indexes].copy(),
            records=records,
            snapshots=snapshots,
            spec=spec,
            fingerprint=fingerprint,
        )
    return result
