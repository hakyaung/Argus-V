"""Join reviewed next-action labels to exact C-01 rows, never to feature columns."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from argus_v.activations.adapter import ModelIdentity
from argus_v.activations.alignment import ActionSpan, SnapshotContext, require_index, require_text

DATASET_SCHEMA = "argus-v.probe-dataset.v1"
LABEL_DEFINITION = "next_action_policy_violation"


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_digest(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class FeatureSpec:
    identity: ModelIdentity
    config_sha256: str
    model_type: str
    layers: tuple[int, ...]
    hidden_sizes: tuple[int, ...]
    module_paths: tuple[str, ...]
    hook_point: str = "post_block_pre_final_norm"
    capture_mode: str = "full_prefix_replay"
    pooling: str = "none"
    layer_aggregation: str = "concat"

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ModelIdentity):
            raise ValueError("identity must be a ModelIdentity")
        require_digest(self.config_sha256, "config_sha256")
        require_text(self.model_type, "model_type")
        if not self.layers or any(type(i) is not int or i < 0 for i in self.layers):
            raise ValueError("layers must be nonempty nonnegative integer block indexes")
        if tuple(sorted(set(self.layers))) != self.layers:
            raise ValueError("layers must be unique and increasing")
        if len(self.hidden_sizes) != len(self.layers) or any(
            type(size) is not int or size <= 0 for size in self.hidden_sizes
        ):
            raise ValueError("hidden_sizes must match layers and be positive integers")
        if len(self.module_paths) != len(self.layers):
            raise ValueError("module_paths must match layers")
        for path in self.module_paths:
            require_text(path, "module_path")
        if self.hook_point != "post_block_pre_final_norm":
            raise ValueError("only C-01 post-block features are supported; MLP logs are different")
        if self.capture_mode != "full_prefix_replay":
            raise ValueError("only full_prefix_replay features are supported")
        if self.pooling not in ("none", "reasoning_mean"):
            raise ValueError("unsupported probe pooling")
        if self.layer_aggregation not in ("concat", "mean"):
            raise ValueError("unsupported layer aggregation")
        if self.layer_aggregation == "mean" and len(set(self.hidden_sizes)) != 1:
            raise ValueError("layer mean requires equal hidden sizes")

    @property
    def input_dim(self) -> int:
        if self.layer_aggregation == "mean":
            return self.hidden_sizes[0]
        return sum(self.hidden_sizes)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # Old checkpoints and replay plans used implicit concatenation.
        if self.layer_aggregation == "concat":
            value.pop("layer_aggregation")
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FeatureSpec":
        return cls(
            **{
                **value,
                "identity": ModelIdentity(**value["identity"]),
                "layers": tuple(value["layers"]),
                "hidden_sizes": tuple(value["hidden_sizes"]),
                "module_paths": tuple(value["module_paths"]),
            }
        )


@dataclass(frozen=True)
class ProbeSnapshot:
    features: np.ndarray
    spec: FeatureSpec
    snapshot_id: str
    run_id: str
    trajectory_id: str
    token_index: int
    capture_prefix_length: int
    capture_prefix_ids_sha256: str
    feature_archive_sha256: str

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "run_id", "trajectory_id"):
            require_text(getattr(self, name), name)
        require_index(self.token_index, "token_index")
        require_index(self.capture_prefix_length, "capture_prefix_length")
        if self.token_index >= self.capture_prefix_length:
            raise ValueError("token_index is outside the capture prefix")
        require_digest(self.capture_prefix_ids_sha256, "capture_prefix_ids_sha256")
        require_digest(self.feature_archive_sha256, "feature_archive_sha256")
        if self.features.shape != (self.spec.input_dim,) or not np.isfinite(self.features).all():
            raise ValueError("features must be a finite vector matching FeatureSpec")

    def join_metadata(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "run_id": self.run_id,
            "trajectory_id": self.trajectory_id,
            "phase": "pre_action",
            "token_index": self.token_index,
            "predicts_token_index": self.token_index + 1,
            "observed_prefix_length": self.token_index + 1,
            "capture_prefix_length": self.capture_prefix_length,
            "capture_prefix_ids_sha256": self.capture_prefix_ids_sha256,
            "feature_archive_sha256": self.feature_archive_sha256,
            "source_identity": asdict(self.spec.identity),
        }


def load_probe_snapshot(
    capture_dir: str | Path,
    *,
    layers: tuple[int, ...],
    snapshot_id: str,
    token_index: int | None = None,
) -> ProbeSnapshot:
    """Select one h[p] row per block; concatenate in fixed increasing block order.

    No pooling, labels, action boundaries, IDs or logits enter the feature vector.
    Runner-supplied boundaries are checked but their factual truth needs B/D review.
    """
    root = Path(capture_dir)
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    required = {
        "schema_version": "argus-v.activation.v1",
        "capture_mode": "full_prefix_replay",
        "hook_point": "post_block_pre_final_norm",
        "layer_index_base": 0,
        "token_index_base": 0,
        "pooling": "none",
        "storage_dtype": "float32",
    }
    if any(metadata.get(key) != value for key, value in required.items()):
        raise ValueError("expected zero-based, unpooled C-01 post-block capture metadata")
    context = metadata["context"]
    context = SnapshotContext(**{**context, "action": ActionSpan(**context["action"])})
    if context.phase != "pre_action":
        raise ValueError("early prediction requires pre_action; pre_execution belongs to E3")
    length = metadata["observed_prefix_length"]
    context.validate_prefix(length)
    position = length - 1 if token_index is None else token_index
    require_index(position, "token_index")
    if position >= length:
        raise ValueError("token_index is outside the capture prefix")
    alignment = metadata["alignment"]
    positions = tuple(row["token_index"] for row in alignment)
    if not positions or positions != tuple(sorted(set(positions))):
        raise ValueError("captured token positions must be unique and increasing")
    for row in alignment:
        require_index(row["token_index"], "alignment token_index")
        require_index(row["token_id"], "alignment token_id")
        if (
            row["token_index"] >= length
            or type(row["predicts_token_index"]) is not int
            or row["predicts_token_index"] != row["token_index"] + 1
            or row["tokens_until_action_start"]
            != context.action.token_start - row["predicts_token_index"]
        ):
            raise ValueError("inconsistent token alignment")
    if position not in positions:
        raise ValueError("requested token_index was not captured")
    archive = metadata["artifact"]
    if archive["filename"] != "features.npz":
        raise ValueError("expected features.npz")
    archive_hash = file_sha256(root / "features.npz")
    if archive_hash != archive["sha256"]:
        raise ValueError("feature archive SHA-256 mismatch")
    blocks = metadata["blocks"]
    by_layer = {block["layer_index"]: block for block in blocks}
    if len(by_layer) != len(blocks):
        raise ValueError("duplicate block metadata")
    vectors, sizes, paths = [], [], []
    with np.load(root / "features.npz", allow_pickle=False) as arrays:
        for layer in layers:
            if layer not in by_layer:
                raise ValueError(f"block {layer} was not captured")
            block = by_layer[layer]
            if block["key"] != f"block_{layer:04d}":
                raise ValueError("inconsistent block key")
            features = arrays[block["key"]]
            if (
                features.dtype != np.float32
                or features.ndim != 2
                or list(features.shape) != block["shape"]
                or features.shape[0] != len(positions)
            ):
                raise ValueError("feature array shape/dtype differs from capture metadata")
            vector = features[positions.index(position)].copy()
            if not np.isfinite(vector).all():
                raise ValueError("non-finite activation")
            vectors.append(vector)
            sizes.append(features.shape[1])
            paths.append(block["module_path"])
    spec = FeatureSpec(
        ModelIdentity(**metadata["identity"]),
        metadata["config_sha256"],
        metadata["model_type"],
        layers,
        tuple(sizes),
        tuple(paths),
    )
    return ProbeSnapshot(
        np.concatenate(vectors),
        spec,
        snapshot_id,
        context.run_id,
        context.trajectory_id,
        position,
        length,
        metadata["prefix_ids_sha256"],
        archive_hash,
    )


@dataclass(frozen=True)
class ProbeDataset:
    features: np.ndarray
    labels: np.ndarray
    snapshots: tuple[ProbeSnapshot, ...]
    records: tuple[dict[str, Any], ...]
    spec: FeatureSpec
    split: str
    manifest_sha256: str
    fingerprint: str
    labels_revision: str
    split_revision: str


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Check split contracts for ALL records without opening any test capture."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != DATASET_SCHEMA
        or manifest.get("label_definition") != LABEL_DEFINITION
        or type(manifest.get("prediction_horizon_actions")) is not int
        or manifest["prediction_horizon_actions"] != 1
    ):
        raise ValueError("manifest must declare next-action policy violation with horizon 1")
    for name in ("labels_revision", "split_revision"):
        require_text(manifest.get(name), name)
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("samples must be a nonempty list")
    groups: dict[tuple, str] = {}
    snapshots, observations = set(), set()
    trajectory_labels: dict[tuple, bool | None] = {}
    action_labels: dict[tuple, int | None] = {}
    text_fields = (
        "snapshot_id",
        "run_id",
        "trajectory_id",
        "family_id",
        "task_group_id",
        "action_id",
        "capture_dir",
    )
    allowed = {
        *text_fields,
        "split",
        "token_index",
        "next_action_violation",
        "trajectory_has_violation",
    }
    for record in samples:
        if not isinstance(record, dict) or set(record) - allowed:
            raise ValueError("invalid sample fields")
        for name in text_fields:
            require_text(record.get(name), name)
        split = record.get("split")
        if split not in ("train", "validation", "test"):
            raise ValueError("split must be train, validation or test")
        require_index(record.get("token_index"), "token_index")
        label = record.get("next_action_violation")
        if label is not None and (type(label) is not int or label not in (0, 1)):
            raise ValueError("next_action_violation must be 0, 1 or null (unreviewed)")
        trajectory_label = record.get("trajectory_has_violation")
        if trajectory_label is not None and type(trajectory_label) is not bool:
            raise ValueError("trajectory_has_violation must be a boolean or null")
        if label == 1 and trajectory_label is False:
            raise ValueError("positive next-action label conflicts with compliant trajectory")
        run, trajectory = record["run_id"], record["trajectory_id"]
        snapshot_key = (run, trajectory, record["snapshot_id"])
        observation_key = (run, trajectory, record["token_index"])
        if snapshot_key in snapshots or observation_key in observations:
            raise ValueError("duplicate snapshot or observation cutoff")
        snapshots.add(snapshot_key)
        observations.add(observation_key)
        trajectory_key = (run, trajectory)
        if (
            trajectory_key in trajectory_labels
            and trajectory_labels[trajectory_key] != trajectory_label
        ):
            raise ValueError("inconsistent trajectory_has_violation annotations")
        trajectory_labels[trajectory_key] = trajectory_label
        action_key = (run, trajectory, record["action_id"])
        if action_key in action_labels and action_labels[action_key] != label:
            raise ValueError("inconsistent next-action labels for the same action")
        action_labels[action_key] = label
        keys = (
            ("family", record["family_id"]),
            ("task", record["task_group_id"]),
            ("run", run),
            ("trajectory", *trajectory_key),
        )
        for key in keys:
            if key in groups and groups[key] != split:
                raise ValueError(f"split leakage: {key[0]} appears in multiple splits")
            groups[key] = split
    return manifest


def load_probe_dataset(
    manifest_path: str | Path, *, split: str, layers: tuple[int, ...]
) -> ProbeDataset:
    """Load just one split; unknown labels are errors, never silently negative."""
    path = Path(manifest_path).resolve()
    manifest_hash = file_sha256(path)
    manifest = read_manifest(path)
    records = tuple(record for record in manifest["samples"] if record["split"] == split)
    if not records:
        raise ValueError(f"no samples in {split}")
    snapshots = []
    for record in records:
        if record.get("next_action_violation") is None:
            raise ValueError(f"unreviewed next-action label in {split}: {record['snapshot_id']}")
        snapshot = load_probe_snapshot(
            path.parent / record["capture_dir"],
            layers=layers,
            snapshot_id=record["snapshot_id"],
            token_index=record["token_index"],
        )
        if (snapshot.run_id, snapshot.trajectory_id) != (record["run_id"], record["trajectory_id"]):
            raise ValueError("manifest run/trajectory differs from capture")
        metadata = json.loads(
            (path.parent / record["capture_dir"] / "metadata.json").read_text(encoding="utf-8")
        )
        if metadata["context"]["action"]["action_id"] != record["action_id"]:
            raise ValueError("manifest next action differs from capture annotation")
        if snapshots and snapshot.spec != snapshots[0].spec:
            raise ValueError("mixed model revisions/configurations or feature layouts")
        snapshots.append(snapshot)
    fingerprint = sha256(
        json.dumps(
            {"manifest_sha256": manifest_hash, "snapshots": [s.join_metadata() for s in snapshots]},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ProbeDataset(
        np.stack([s.features for s in snapshots]),
        np.asarray([record["next_action_violation"] for record in records], dtype=np.float32),
        tuple(snapshots),
        records,
        snapshots[0].spec,
        split,
        manifest_hash,
        fingerprint,
        manifest["labels_revision"],
        manifest["split_revision"],
    )
