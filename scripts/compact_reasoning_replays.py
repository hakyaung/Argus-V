"""Audit or compact completed train/validation replays in place, without model loading.

Default is a read-only inventory. --apply saves and verifies summaries.
--delete-source-tensors additionally removes ONLY verified full NPY tensors whose
mean, last token and original reference rows have been persisted successfully.
Interrupted actions without complete manifests are left untouched.
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path


def compact(
    root, run_dir, hook_roots, *, apply=False, delete_source_tensors=False, max_actions=None
):
    sys.path.insert(0, str(root / "src"))
    from argus_v.probes import load_probe
    from argus_v.probes.data import file_sha256
    from argus_v.probes.pooled import PHASE, convert_full, delete_verified_sources, read_pooled
    from argus_v.probes.reasoning import (
        capture_key,
        index_replays,
        read_json,
        reasoning_span,
        source_views,
    )

    if delete_source_tensors and not apply:
        raise ValueError("--delete-source-tensors requires --apply")
    if max_actions is not None and max_actions < 1:
        raise ValueError("max-actions must be positive")
    baseline = load_probe(run_dir / "model")
    manifest = run_dir / "dataset/manifest.json"
    if (
        baseline.spec.pooling != "none"
        or file_sha256(manifest) != baseline.training_report["manifest_sha256"]
    ):
        raise ValueError("expected the unchanged original reviewed run")
    # Discover layer layout using JSON only. Test tensors are never loaded.
    all_layers = set()
    for root_dir in hook_roots:
        for path in root_dir.rglob("manifest.json"):
            hook = read_json(path)
            if hook.get("phase") == PHASE:
                all_layers.update(hook["selected_layers"])
            elif hook.get("phase") == "pre_action_full_prefix_replay":
                rows = [
                    json.loads(line)
                    for line in (path.parent / "records.jsonl").read_text().splitlines()
                ]
                all_layers.update(r["layer_index"] for r in rows if r.get("kind") == "tensor")
    layers = tuple(sorted(all_layers)) or baseline.spec.layers
    views = source_views(manifest, baseline, layers)
    wanted, sources = set(), {}
    for dataset in views.values():
        for record in dataset.records:
            capture = (manifest.parent / record["capture_dir"]).resolve()
            meta = read_json(capture / "metadata.json")
            key = capture_key(meta)
            wanted.add(key)
            sources[key] = (capture, meta, dataset.spec)
    replays, issues = index_replays(hook_roots, wanted)
    summary = {
        "dry_run": not apply,
        "completed_actions_seen": 0,
        "converted_actions": 0,
        "already_pooled_actions": 0,
        "full_tensor_bytes": 0,
        "reclaimed_bytes": 0,
        "test_features_opened": False,
        "generation_performed": False,
        "issues": issues,
        "note": "Incomplete actions, original C-01, rollouts and trained models are untouched.",
    }
    processed = 0
    for key, matches in replays.items():
        capture, meta, full_spec = sources[key]
        for path, hook in matches:
            if max_actions is not None and processed >= max_actions:
                print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
                return summary
            processed += 1
            summary["completed_actions_seen"] += 1
            if hook["phase"] == PHASE:
                selected = hook["selected_layers"]
            else:
                rows = [
                    json.loads(line)
                    for line in (path.parent / "records.jsonl").read_text().splitlines()
                ]
                selected = sorted({r["layer_index"] for r in rows if r.get("kind") == "tensor"})
            indexes = [full_spec.layers.index(layer) for layer in selected]
            spec = replace(
                full_spec,
                layers=tuple(selected),
                hidden_sizes=tuple(full_spec.hidden_sizes[i] for i in indexes),
                module_paths=tuple(full_spec.module_paths[i] for i in indexes),
            )
            if hook["phase"] == PHASE:
                summary["already_pooled_actions"] += 1
            if not apply:
                if hook["phase"] != PHASE:
                    from argus_v.probes.reasoning import safe_tensor_path

                    tensors = {
                        safe_tensor_path(path.parent, r["filename"])
                        for r in rows
                        if r.get("kind") == "tensor"
                    }
                    summary["full_tensor_bytes"] += sum(p.stat().st_size for p in tensors)
                continue
            span = reasoning_span(capture, meta)
            if hook["phase"] != PHASE:
                hook = convert_full(path, hook, meta, spec, span, capture)
                summary["converted_actions"] += 1
                summary["full_tensor_bytes"] += sum(s["bytes"] for s in hook["source_tensors"])
            else:
                read_pooled(path, hook, meta, spec, span, capture)
            if delete_source_tensors:
                summary["reclaimed_bytes"] += delete_verified_sources(
                    path, hook, meta, spec, span, capture
                )
            print(
                f"Processed {processed}: {path.parent.name}; "
                f"reclaimed {summary['reclaimed_bytes'] / 1024**3:.3f} GiB",
                flush=True,
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--hook-roots", type=Path, nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-source-tensors", action="store_true")
    parser.add_argument("--max-actions", type=int)
    args = parser.parse_args()
    try:
        compact(
            args.repo_root.resolve(),
            args.run_dir.resolve(),
            [p.resolve() for p in args.hook_roots],
            apply=args.apply,
            delete_source_tensors=args.delete_source_tensors,
            max_actions=args.max_actions,
        )
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        parser.exit(
            2, f"Compaction stopped: {error}\nVerified completed summaries remain resumable.\n"
        )


if __name__ == "__main__":
    main()
