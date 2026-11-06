"""Compare last-token and reasoning-mean probes using concatenated or averaged layers.

Run on the computer holding the reviewed run and original C-01/Hook artifacts.
No generator inference or test activation access. Audit coverage before fitting.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def compare(
    root,
    run_dir,
    output,
    hook_roots,
    *,
    layer=23,
    layers=None,
    layer_aggregation="concat",
    weight_decay=1.0,
    audit_only=False,
    allow_partial=False,
):
    sys.path.insert(0, str(root / "src"))
    import numpy as np
    import torch

    from argus_v.evaluation.metrics import auroc, average_precision
    from argus_v.probes import TrainingConfig, fit_probe, load_probe, save_probe
    from argus_v.probes.data import file_sha256
    from argus_v.probes.linear import select_threshold
    from argus_v.probes.reasoning import (
        capture_key,
        extract_mean,
        index_replays,
        paired_datasets,
        read_json,
        reasoning_span,
        resolve_layers,
        source_views,
    )

    layers = resolve_layers(layer, layers)
    if layer_aggregation not in ("concat", "mean"):
        raise ValueError("unsupported layer aggregation")
    baseline = load_probe(run_dir / "model")
    if baseline.spec.pooling != "none":
        raise ValueError("source run must be an original last-token reviewed run")
    config = replace(
        TrainingConfig(**baseline.training_report["training_config"]), weight_decay=weight_decay
    )
    manifest = run_dir / "dataset/manifest.json"
    if file_sha256(manifest) != baseline.training_report["manifest_sha256"]:
        raise ValueError("Source manifest changed since original training")
    output.mkdir(parents=True, exist_ok=False)
    print(f"Output: {output}", flush=True)
    torch.set_num_threads(1)
    save_json(
        output / "plan.json",
        {
            "source_run": str(run_dir),
            "manifest_sha256": file_sha256(manifest),
            "layer": layers[0] if len(layers) == 1 else None,
            "layers": list(layers),
            "layer_aggregation": layer_aggregation,
            "layer_combination": (
                "unweighted coordinate-wise mean of raw layer vectors"
                if layer_aggregation == "mean"
                else "concatenate in increasing layer order"
            ),
            "training_config": asdict(config),
            "feature_modes": ["last_token", "reasoning_mean"],
            "reasoning_span": "runner m1-context.json reasoning_token_span [start,end)",
            "hook_roots": [str(p) for p in hook_roots],
            "allow_partial": allow_partial,
            "partial_policy": "retain trajectories with full source-manifest coverage in both arms",
            "test_features_opened": False,
            "audit_only": audit_only,
        },
    )
    views = source_views(manifest, baseline, layers)
    pooled_specs = {
        split: replace(dataset.spec, pooling="reasoning_mean", layer_aggregation=layer_aggregation)
        for split, dataset in views.items()
    }
    metadata = {}
    for split, dataset in views.items():
        for i, record in enumerate(dataset.records):
            capture = (manifest.parent / record["capture_dir"]).resolve()
            metadata[(split, i)] = (capture, read_json(capture / "metadata.json"))
    wanted = {capture_key(m) for _, m in metadata.values()}
    print("Indexing exact pre-action Hook replays (metadata only)...", flush=True)
    replays, issues = index_replays(hook_roots, wanted)
    save_json(output / "source-diagnostics.json", issues)
    available, excluded, provenance = {}, [], []
    for split, dataset in views.items():
        for i, record in enumerate(dataset.records):
            capture, meta = metadata[(split, i)]
            try:
                if record["token_index"] != meta["observed_prefix_length"] - 1:
                    raise ValueError("source sample must be the final pre-action prefix position")
                span = reasoning_span(capture, meta)
                vector, origin = extract_mean(
                    capture, meta, dataset.spec, span, replays, layer_aggregation=layer_aggregation
                )
                if (
                    vector.shape != (pooled_specs[split].input_dim,)
                    or not np.isfinite(vector).all()
                ):
                    raise ValueError("invalid pooled feature vector")
                available[(split, i)] = vector
                provenance.append(
                    {
                        "snapshot_id": record["snapshot_id"],
                        "action_id": record["action_id"],
                        "trajectory_id": record["trajectory_id"],
                        "run_id": record["run_id"],
                        "split": split,
                        "capture": str(capture),
                        "reasoning_span": list(span),
                        "reasoning_token_count": span[1] - span[0],
                        "c01_archive_sha256": meta["artifact"]["sha256"],
                        "span_sidecar_sha256": file_sha256(capture.parent / "m1-context.json"),
                        **origin,
                    }
                )
            except (ValueError, KeyError, TypeError, OSError) as error:
                excluded.append(
                    {
                        "snapshot_id": record["snapshot_id"],
                        "action_id": record["action_id"],
                        "trajectory_id": record["trajectory_id"],
                        "split": split,
                        "reason": str(error),
                    }
                )
        print(f"Checked {split}: {len(dataset.records)} actions", flush=True)
    # Dropping a difficult normal step would artificially lower trajectory FPR.
    # Use complete trajectories in both train and validation, including positives.
    incomplete = {(r["split"], r["trajectory_id"]) for r in excluded}
    indexes = {}
    for split, dataset in views.items():
        indexes[split] = []
        for i, record in enumerate(dataset.records):
            if (split, record["trajectory_id"]) in incomplete:
                if (split, i) in available:
                    excluded.append(
                        {
                            "snapshot_id": record["snapshot_id"],
                            "action_id": record["action_id"],
                            "trajectory_id": record["trajectory_id"],
                            "split": split,
                            "reason": "incomplete_reasoning_coverage_in_same_trajectory",
                        }
                    )
            else:
                indexes[split].append(i)
    usable = {
        split: {
            "source_actions": len(dataset.labels),
            "paired_actions": len(indexes[split]),
            "paired_labels": dict(Counter(int(dataset.labels[i]) for i in indexes[split])),
            "paired_trajectories": len(
                {dataset.records[i]["trajectory_id"] for i in indexes[split]}
            ),
        }
        for split, dataset in views.items()
    }
    retained = {
        (s, views[s].records[i]["run_id"], views[s].records[i]["snapshot_id"])
        for s in views
        for i in indexes[s]
    }
    for item in provenance:
        item["retained_in_comparison"] = (
            item["split"],
            item["run_id"],
            item["snapshot_id"],
        ) in retained
    save_json(output / "extraction-provenance.json", provenance)
    save_json(
        output / "coverage.json",
        {
            "status": "coverage_incomplete" if excluded else "coverage_complete",
            "layers": list(layers),
            "layer_aggregation": layer_aggregation,
            "input_dim": pooled_specs["train"].input_dim,
            "sample_counts": usable,
            "exclusion_counts": dict(Counter(r["reason"] for r in excluded)),
            "excluded": excluded,
            "test_features_opened": False,
        },
    )
    print(
        json.dumps(
            {
                "coverage": usable,
                "excluded_actions": len(excluded),
                "layer_aggregation": layer_aggregation,
                "input_dim": pooled_specs["train"].input_dim,
            },
            indent=2,
        ),
        flush=True,
    )
    if audit_only:
        return {"status": "audit_only", "output": str(output), "sample_counts": usable}
    if excluded and not allow_partial:
        raise ValueError(
            "Incomplete reasoning coverage; see coverage.json. No model trained. "
            "--allow-partial explicitly compares only complete covered trajectories."
        )
    for split in views:
        if set(usable[split]["paired_labels"]) != {0, 1}:
            raise ValueError(f"Paired {split} needs both label classes; see coverage.json")
    extraction_hash = file_sha256(output / "extraction-provenance.json")
    paired = {
        split: paired_datasets(
            dataset,
            indexes[split],
            [available[(split, i)] for i in indexes[split]],
            extraction_hash,
            layer_aggregation=layer_aggregation,
        )
        for split, dataset in views.items()
    }
    arrays = {}
    for split, modes in paired.items():
        for name, dataset in modes.items():
            arrays[f"{split}_{name}"] = dataset.features
        arrays[f"{split}_labels"] = modes["last_token"].labels
    np.savez_compressed(output / "paired-features.npz", **arrays)
    save_json(
        output / "paired-samples.json",
        {split: list(modes["last_token"].records) for split, modes in paired.items()},
    )
    results = []
    for name in ("last_token", "reasoning_mean"):
        layout = "+".join(f"L{i:02d}" for i in layers)
        print(f"Fitting {layout}-{layer_aggregation}-{name}...", flush=True)
        train, validation = paired["train"][name], paired["validation"][name]
        grouped = replace(
            validation,
            records=tuple({**r, "run_id": r["trajectory_id"]} for r in validation.records),
        )
        probe = fit_probe(train, grouped, config)
        scores = probe.model.risk_scores(validation.features)
        threshold, metrics = select_threshold(scores, grouped, config.max_normal_trajectory_fpr)
        auc = auroc(scores, validation.labels == 1)
        ap = average_precision(scores, validation.labels == 1)
        report = {
            **probe.training_report,
            "validation_metrics": metrics,
            "feature_mode": name,
            "layers": list(layers),
            "input_dim": train.spec.input_dim,
            "layer_aggregation": layer_aggregation,
            "source_reviewed_run": str(run_dir),
            "source_manifest_sha256": file_sha256(manifest),
            "extraction_provenance_sha256": extraction_hash,
            "paired_features_sha256": file_sha256(output / "paired-features.npz"),
            "normal_trajectory_grouping": "original trajectory_id (all steps together)",
            "validation_auroc": auc,
            "validation_average_precision": ap,
            "alarms_disabled": threshold > 1,
            "complete_source_coverage": not excluded,
            "limitation": (
                "Validation comparison only; partial coverage changes the evaluated population."
            ),
        }
        probe = replace(probe, threshold=threshold, training_report=report)
        destination = output / "models" / name
        save_probe(probe, destination)
        np.testing.assert_array_equal(
            scores, load_probe(destination).model.risk_scores(validation.features)
        )
        save_json(
            output / f"{name}-validation-scores.json",
            [
                {
                    "snapshot_id": r["snapshot_id"],
                    "action_id": r["action_id"],
                    "trajectory_id": r["trajectory_id"],
                    "label": int(y),
                    "risk_score": float(s),
                    "alarm": bool(float(s) >= threshold),
                }
                for r, y, s in zip(validation.records, validation.labels, scores, strict=True)
            ],
        )
        row = {
            "feature_mode": name,
            "layer": layers[0] if len(layers) == 1 else None,
            "layers": list(layers),
            "input_dim": train.spec.input_dim,
            "layer_aggregation": layer_aggregation,
            "train_count": len(train.labels),
            "validation_count": len(validation.labels),
            "train_bce": report["final_train_bce"],
            "validation_auroc": auc,
            "validation_average_precision": ap,
            "threshold": threshold,
            "alarms_disabled": threshold > 1,
            **metrics,
            "model_path": str(destination),
        }
        results.append(row)
        print(json.dumps(row, indent=2), flush=True)
    summary = {
        "status": "validation_comparison_complete",
        "output": str(output),
        "results": results,
        "source_run": str(run_dir),
        "layer_aggregation": layer_aggregation,
        "sample_counts": usable,
        "complete_source_coverage": not excluded,
        "paired_examples_identical": True,
        "save_load_scores_identical": True,
        "test_features_opened": False,
        "limitation": "Repeated validation exploration is not independent test evidence.",
    }
    save_json(output / "comparison.json", summary)
    with (output / "comparison.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--hook-roots", type=Path, nargs="+")
    layout = parser.add_mutually_exclusive_group()
    layout.add_argument("--layer", type=int, default=23)
    layout.add_argument("--layers", type=int, nargs="+", help="e.g. 0 11 23; concatenate per layer")
    parser.add_argument(
        "--layer-aggregation",
        choices=("concat", "mean"),
        default="concat",
        help="mean: average raw layer vectors coordinate-wise before standardization",
    )
    parser.add_argument("--weight-decay", type=float, default=1.0)
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = "reasoning-layer-mean" if args.layer_aggregation == "mean" else "reasoning-mean"
    output = (args.output or root / "artifacts" / f"{prefix}-{stamp}-{uuid4().hex[:6]}").resolve()
    roots = args.hook_roots or [root / "tensor_data", root / "argus_hook/tensor_data"]
    try:
        compare(
            root,
            args.run_dir.resolve(),
            output,
            [p.resolve() for p in roots],
            layer=args.layer,
            layers=args.layers,
            layer_aggregation=args.layer_aggregation,
            weight_decay=args.weight_decay,
            audit_only=args.audit_only,
            allow_partial=args.allow_partial,
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.exit(2, f"Comparison stopped: {error}\nDiagnostics: {output}\n")


if __name__ == "__main__":
    main()
