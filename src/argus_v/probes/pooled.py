"""Small, verified replay summaries. Full tensors are never needed after conversion.

Pooling retains the existing 128-token float64 accumulation, then stores float32.
Only a single layer of a single action is read at a time during conversion.
"""

import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .data import file_sha256
from .reasoning import capture_key, full_replay_mean, prefix_digest, safe_tensor_path

PHASE = "pre_action_pooled_replay"
ALGORITHM = "chunks128_float64_then_float32"
ARCHIVE = "pooled-features.npz"


def token_mean(values, span):
    start, end = span
    if not 0 <= start < end <= len(values):
        raise ValueError("invalid pooling span")
    total = np.zeros(values.shape[-1], dtype=np.float64)
    for offset in range(start, end, 128):
        chunk = values[offset : min(offset + 128, end)]
        if not np.isfinite(chunk).all():
            raise ValueError("non-finite reasoning activation")
        total += chunk.sum(axis=0, dtype=np.float64)
    return (total / (end - start)).astype(np.float32)


def _single(spec, i):
    return replace(
        spec,
        layers=(spec.layers[i],),
        hidden_sizes=(spec.hidden_sizes[i],),
        module_paths=(spec.module_paths[i],),
    )


def read_pooled(path, hook, metadata, spec, span, capture):
    """Validate identity, exact span, checksum and original C-01 rows before reuse."""
    if (
        hook.get("schema_version") != "argus-v.hook-inspection.v1"
        or hook.get("phase") != PHASE
        or hook.get("status") != "complete"
        or hook.get("identity") != asdict(spec.identity)
        or hook.get("model_type") != spec.model_type
        or hook.get("config_sha256") != spec.config_sha256
        or capture_key(hook) != capture_key(metadata)
        or prefix_digest(hook["context"]["input_ids"]) != metadata["prefix_ids_sha256"]
        or hook.get("reasoning_span") != list(span)
        or hook.get("pooling_algorithm") != ALGORITHM
        or hook.get("source_c01_archive_sha256") != metadata["artifact"]["sha256"]
        or hook["context"].get("action") != metadata["context"].get("action")
        or hook["context"].get("scenario_id") != metadata["context"].get("scenario_id")
    ):
        raise ValueError("pooled replay metadata differs from original")
    artifact = hook.get("pooled_artifact", {})
    if artifact.get("filename") != ARCHIVE:
        raise ValueError("invalid pooled archive filename")
    archive = safe_tensor_path(path.parent, artifact["filename"])
    if artifact.get("sha256") != file_sha256(archive):
        raise ValueError("pooled archive checksum mismatch")
    if file_sha256(capture / "features.npz") != metadata["artifact"]["sha256"]:
        raise ValueError("original C-01 archive checksum mismatch")
    positions = [r["token_index"] for r in metadata["alignment"]]
    result = {}
    with (
        np.load(archive, allow_pickle=False) as arrays,
        np.load(capture / "features.npz", allow_pickle=False) as original,
    ):
        for layer, size, module in zip(
            spec.layers, spec.hidden_sizes, spec.module_paths, strict=True
        ):
            if layer not in hook.get("selected_layers", []) or hook.get("blocks", {}).get(
                str(layer)
            ) != {"hidden_size": size, "module": module}:
                raise ValueError("pooled layer layout differs")
            key = f"block_{layer:04d}"
            mean, last, reference = (
                arrays[f"{mode}_{key}"] for mode in ("mean", "last", "reference")
            )
            for vector in (mean, last):
                if (
                    vector.dtype != np.float32
                    or vector.shape != (size,)
                    or not np.isfinite(vector).all()
                ):
                    raise ValueError("invalid pooled vector shape/dtype/values")
            if reference.dtype != np.float32 or not np.array_equal(reference, original[key]):
                raise ValueError("pooled C-01 reference rows differ")
            if metadata["observed_prefix_length"] - 1 not in positions or not np.array_equal(
                last, reference[positions.index(metadata["observed_prefix_length"] - 1)]
            ):
                raise ValueError("pooled last token differs from original")
            result[layer] = mean.copy()
    return result


def write_pooled(folder, metadata, spec, span, capture, arrays, *, source_tensors=None):
    """Read back every small array before atomically publishing the complete manifest."""
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / ARCHIVE
    temporary = folder / (ARCHIVE + ".tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays)
        handle.flush()
        os.fsync(handle.fileno())
    with np.load(temporary, allow_pickle=False) as saved:
        if set(saved.files) != set(arrays) or any(
            not np.array_equal(saved[key], value) for key, value in arrays.items()
        ):
            raise ValueError("pooled archive readback differs")
    temporary.replace(archive)
    sidecar = json.loads((capture.parent / "c01-input.json").read_text(encoding="utf-8"))
    hook = {
        "schema_version": "argus-v.hook-inspection.v1",
        "status": "complete",
        "phase": PHASE,
        "identity": asdict(spec.identity),
        "model_type": spec.model_type,
        "config_sha256": spec.config_sha256,
        "context": {**metadata["context"], "input_ids": sidecar["input_ids"]},
        "prefix_ids_sha256": metadata["prefix_ids_sha256"],
        "selected_layers": list(spec.layers),
        "reasoning_span": list(span),
        "pooling_algorithm": ALGORITHM,
        "source_c01_archive_sha256": metadata["artifact"]["sha256"],
        "blocks": {
            str(layer): {"hidden_size": size, "module": module}
            for layer, size, module in zip(
                spec.layers, spec.hidden_sizes, spec.module_paths, strict=True
            )
        },
        "pooled_artifact": {"filename": ARCHIVE, "sha256": file_sha256(archive)},
        "source_tensors": source_tensors or [],
    }
    manifest = folder / "manifest.json"
    read_pooled(manifest, hook, metadata, spec, span, capture)
    temporary_manifest = folder / "manifest.json.tmp"
    with temporary_manifest.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(hook, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary_manifest.replace(manifest)
    # Persist both filenames before a caller may delete the full source tensors.
    if os.name != "nt":
        directory_fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return hook


def convert_full(path, hook, metadata, spec, span, capture):
    """Convert one completed action in place; never remove its full tensors here."""
    records = path.parent / "records.jsonl"
    if hook.get("records_sha256") and hook["records_sha256"] != file_sha256(records):
        raise ValueError("full replay records checksum mismatch")
    rows = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    if hook.get("selected_layers") is not None and hook["selected_layers"] != list(spec.layers):
        raise ValueError("conversion requires every stored layer")
    arrays, sources = {}, []
    for i, layer in enumerate(spec.layers):
        matches = [r for r in rows if r.get("layer_index") == layer]
        if len(matches) != 1:
            raise ValueError("full replay layer missing or ambiguous")
        filename = matches[0]["filename"]
        source = safe_tensor_path(path.parent, filename)
        if (
            Path(filename).name != filename
            or source.suffix != ".npy"
            or (path.parent / filename).is_symlink()
        ):
            raise ValueError("conversion only accepts local NPY tensor files")
        sha = file_sha256(source)
        expected = hook.get("artifacts_sha256", {}).get(source.name) or hook.get("artifact_sha256")
        if expected is not None and sha != expected:
            raise ValueError("full replay tensor checksum mismatch")
        vector, _ = full_replay_mean(path, hook, metadata, _single(spec, i), span, capture)
        values = np.load(source, mmap_mode="r", allow_pickle=False)
        try:
            key = f"block_{layer:04d}"
            arrays[f"mean_{key}"] = vector
            arrays[f"last_{key}"] = values[0, -1].astype(np.float32, copy=True)
            positions = [r["token_index"] for r in metadata["alignment"]]
            arrays[f"reference_{key}"] = values[0, positions].astype(np.float32, copy=True)
        finally:
            values._mmap.close()
        sources.append({"filename": source.name, "sha256": sha, "bytes": source.stat().st_size})
    return write_pooled(path.parent, metadata, spec, span, capture, arrays, source_tensors=sources)


def delete_verified_sources(path, hook, metadata, spec, span, capture):
    """Only called on explicit --delete-source-tensors after revalidating the summary."""
    read_pooled(path, hook, metadata, spec, span, capture)
    sources = []
    for item in hook.get("source_tensors", []):
        filename = item["filename"]
        source = safe_tensor_path(path.parent, filename)
        if (
            Path(filename).name != filename
            or (path.parent / filename).is_symlink()
            or source.parent != path.parent.resolve()
            or source.suffix != ".npy"
        ):
            raise ValueError("refusing unexpected source deletion path")
        if source.exists():
            if source.stat().st_size != item["bytes"] or file_sha256(source) != item["sha256"]:
                raise ValueError("source changed after compaction; nothing deleted for this action")
            sources.append((source, item["bytes"]))
    reclaimed = 0
    for source, size in sources:
        source.unlink()
        reclaimed += size
    return reclaimed
