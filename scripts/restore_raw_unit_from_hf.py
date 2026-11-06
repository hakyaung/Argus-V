#!/usr/bin/env python3
"""Rebuild one raw-data unit's local folders from the Hugging Face dataset written by upload_raw_data_to_hf.py.

The Hub manifest (manifest/units.json) says which shards hold the unit's rows and which local folders it came
from; each row's ``path`` is the file inside the unit (agent_loop: relative to tensor_data/<run>; scenario
rollouts: ``rollout/...`` → artifacts/rollouts/<id>, ``tensor_data/<hook>/...`` → tensor_data/<hook>).

    python scripts/restore_raw_unit_from_hf.py --unit-id run-20261016T055434Z-db788f63 --dry-run
    python scripts/restore_raw_unit_from_hf.py --unit-id AV-S56-C2-s0-0655d1d5 --only-prefix tensor_data/

What comes back:
  * other files (json, jsonl, md, ...): byte-identical (whole-file sha256 checked), mtime set to the uploaded one.
  * .npy: same dtype, shape and array bytes (little-endian, C order); the .npy header may differ from the
    original file (e.g. a big-endian source is restored little-endian), so file bytes/sizes can differ.
  * .npz: rebuilt uncompressed (np.savez layout) with the same member arrays.
Every row's chunk_sha256 is checked, every chunk of every value must arrive, and the row count must equal the
manifest's ``rows``. Existing files are never overwritten unless --overwrite is given. Shards are downloaded one
at a time into a temporary folder and deleted after use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "hakuuung/gpt-oss-20b-hidden-state-raw-data"
MANIFEST = "manifest/units.json"
PROJECT_TOKEN_FILE = PROJECT_ROOT / ".secrets" / "hf_token_hakuuung"
GB = 1024**3


class RestoreError(RuntimeError):
    pass


def find_unit(manifest: dict, unit_id: str, source: str | None = None) -> tuple[str, dict]:
    hits = [(name, units[unit_id]) for name, units in manifest.get("units", {}).items()
            if unit_id in units and (source is None or name == source)]
    if len(hits) != 1:
        raise RestoreError(f"unit {unit_id!r} found {len(hits)} times in the manifest (use --source)")
    return hits[0]


def part_map(source: str, unit: dict, dest_root: Path) -> list[tuple[str, Path]]:
    """(row path prefix, local folder) for each folder of the unit, longest prefix first."""
    parts = []
    for folder in unit["folders"]:
        local = dest_root / folder
        if source == "agent_loop":
            prefix = ""
        elif PurePosixPath(folder).parent.name.startswith("rollouts") and PurePosixPath(folder).parent.parent.name == "artifacts":
            # artifacts/rollouts/<id>, and the harness-v2 roots artifacts/rollouts-v2/<id>, rollouts-v1h2/<id>
            prefix = "rollout/"
        else:
            prefix = "tensor_data/" + PurePosixPath(folder).name + "/"
        parts.append((prefix, local))
    return sorted(parts, key=lambda item: len(item[0]), reverse=True)


def local_target(parts: list[tuple[str, Path]], path: str) -> Path:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise RestoreError(f"unsafe path in row: {path!r}")
    for prefix, folder in parts:
        if path.startswith(prefix):
            return folder.joinpath(*PurePosixPath(path[len(prefix):]).parts)
    raise RestoreError(f"row path {path!r} matches no folder of the unit")


class Restorer:
    def __init__(self, parts, unit_id: str, *, only_prefix: str | None, overwrite: bool, dry_run: bool, log=print):
        self.parts, self.unit_id, self.only_prefix = parts, unit_id, only_prefix
        self.overwrite, self.dry_run, self.log = overwrite, dry_run, log
        self.rows = 0
        self.chunks: dict[tuple, set[int]] = {}   # (table, path, array_name) -> chunk indexes seen
        self.counts: dict[tuple, int] = {}        # -> chunk_count
        self.files_meta: dict[str, dict] = {}     # path -> {bytes, sha256, modified}
        self.npz_members: dict[str, dict[str, Path]] = {}
        self.skipped_existing: set[str] = set()
        self.created: set[Path] = set()
        self.npy_started: set[Path] = set()
        self.tmp = Path(tempfile.mkdtemp(prefix="restore-npz-"))

    def wanted(self, path: str) -> bool:
        return self.only_prefix is None or path.startswith(self.only_prefix)

    def claim(self, path: str, target: Path) -> bool:
        """True if this run may write ``target`` (new this run, or --overwrite)."""
        if target in self.created:
            return True
        if path in self.skipped_existing:
            return False
        if (target.exists() or target.is_symlink()) and not self.overwrite:
            self.skipped_existing.add(path)
            return False
        if not self.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.is_file():
                target.unlink()
        self.created.add(target)
        return True

    def add_row(self, table: str, row: dict) -> None:
        if row["unit_id"] != self.unit_id:
            return
        self.rows += 1
        blob = row["data"] if table == "tensors" else (
            row["text"].encode("utf-8") if row.get("text") is not None else row.get("content"))
        blob = blob or b""
        if hashlib.sha256(blob).hexdigest() != row["chunk_sha256"]:
            raise RestoreError(f"chunk_sha256 mismatch: {row['path']} chunk {row['chunk_index']}")
        key = (table, row["path"], row.get("array_name"))
        self.chunks.setdefault(key, set()).add(row["chunk_index"])
        self.counts[key] = row["chunk_count"]
        if not self.wanted(row["path"]):
            return
        target = local_target(self.parts, row["path"])
        if table == "files":
            self.files_meta[row["path"]] = {"bytes": row["bytes"], "sha256": row["sha256"], "modified": row["modified"]}
            if self.claim(row["path"], target) and not self.dry_run:
                with open(target, "r+b" if target.exists() else "wb") as handle:
                    handle.seek(row["chunk_offset"])
                    handle.write(blob)
            return
        shape, dtype = tuple(row["shape"]), np.dtype(row["dtype"])
        if row.get("array_name") is None:  # .npy
            if not self.claim(row["path"], target) or self.dry_run:
                return
            if target in self.npy_started:
                out = np.load(target, mmap_mode="r+")
            else:
                out = np.lib.format.open_memmap(target, mode="w+", dtype=dtype, shape=shape)
                self.npy_started.add(target)
            if blob:
                flat = out.reshape(-1).view(np.uint8)
                flat[row["chunk_offset"]:row["chunk_offset"] + len(blob)] = np.frombuffer(blob, np.uint8)
                out.flush()
            del out
            return
        # .npz member: staged as an .npy, zipped once every shard has been read
        if self.claim(row["path"], target) and not self.dry_run:
            members = self.npz_members.setdefault(row["path"], {})
            staged = members.get(row["array_name"])
            if staged is None:
                staged = self.tmp / f"{len(self.npz_members)}-{len(members)}.npy"
                members[row["array_name"]] = staged
                out = np.lib.format.open_memmap(staged, mode="w+", dtype=dtype, shape=shape)
            else:
                out = np.load(staged, mmap_mode="r+")
            if blob:
                flat = out.reshape(-1).view(np.uint8)
                flat[row["chunk_offset"]:row["chunk_offset"] + len(blob)] = np.frombuffer(blob, np.uint8)
                out.flush()
            del out

    def finish(self, expected_rows: int) -> dict:
        incomplete = [f"{path}|{name}" for (table, path, name), seen in self.chunks.items()
                      if seen != set(range(self.counts[(table, path, name)]))]
        if self.rows != expected_rows:
            raise RestoreError(f"{self.rows} rows for {self.unit_id}, manifest says {expected_rows}")
        if incomplete:
            raise RestoreError(f"{len(incomplete)} value(s) with missing chunks, e.g. {incomplete[:3]}")
        written = 0
        if not self.dry_run:
            for path, members in self.npz_members.items():
                target = local_target(self.parts, path)
                with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for name, staged in members.items():
                        archive.write(staged, name + ".npy")
            for path, meta in self.files_meta.items():
                target = local_target(self.parts, path)
                if path in self.skipped_existing or target not in self.created:
                    continue
                digest = hashlib.sha256()
                with open(target, "rb") as handle:
                    for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
                        digest.update(block)
                if target.stat().st_size != meta["bytes"] or digest.hexdigest() != meta["sha256"]:
                    raise RestoreError(f"restored file differs from the uploaded one: {path}")
                os.utime(target, (meta["modified"], meta["modified"]))
            written = len(self.created)
        shutil.rmtree(self.tmp, ignore_errors=True)
        return {"unit_id": self.unit_id, "rows": self.rows, "values": len(self.chunks),
                "written_files": written, "would_write": len(self.created),
                "skipped_existing": sorted(self.skipped_existing), "dry_run": self.dry_run}


def iter_shard_rows(shard_file: Path, unit_id: str):
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    table = "tensors" if "data" in pq.ParquetFile(shard_file).schema_arrow.names else "files"
    dataset = ds.dataset(shard_file, format="parquet")
    for batch in dataset.to_batches(filter=ds.field("unit_id") == unit_id, batch_size=4):
        for row in batch.to_pylist():
            yield table, row


def restore_unit(manifest: dict, unit_id: str, fetch_shard, dest_root: Path, *, source: str | None = None,
                 only_prefix: str | None = None, overwrite: bool = False, dry_run: bool = False,
                 min_free_gb: float = 15.0, log=print) -> dict:
    """``fetch_shard(repo_path) -> local Path`` (deleted by the caller's ``release`` if it has one)."""
    source, unit = find_unit(manifest, unit_id, source)
    parts = part_map(source, unit, dest_root)
    restorer = Restorer(parts, unit_id, only_prefix=only_prefix, overwrite=overwrite, dry_run=dry_run, log=log)
    try:
        for number, shard in enumerate(unit["shards"], 1):
            need = manifest["shards"][shard]["bytes"]
            free = shutil.disk_usage(dest_root if dest_root.exists() else PROJECT_ROOT).free
            if free - need < min_free_gb * GB and not dry_run:
                raise RestoreError(f"only {free / GB:.1f} GiB free; keeping {min_free_gb} GiB, stopping before {shard}")
            log(f"[{number}/{len(unit['shards'])}] {shard} ({need / 1e9:.2f} GB)")
            local = fetch_shard(shard)
            try:
                for table, row in iter_shard_rows(local, unit_id):
                    restorer.add_row(table, row)
            finally:
                release = getattr(fetch_shard, "release", None)
                if release:
                    release(local)
        return restorer.finish(unit["rows"]) | {"source": source, "folders": unit["folders"]}
    finally:
        shutil.rmtree(restorer.tmp, ignore_errors=True)


def hub_fetcher(repo: str, revision: str, token, work: Path):
    from huggingface_hub import hf_hub_download

    def fetch(path: str) -> Path:
        return Path(hf_hub_download(repo, path, repo_type="dataset", revision=revision, token=token,
                                    local_dir=work))

    def release(local: Path) -> None:
        local.unlink(missing_ok=True)

    fetch.release = release
    return fetch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--source", choices=("agent_loop", "scenario_rollout"))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--revision", default="main", help="Hub commit to restore from (ledger: hub_commit)")
    parser.add_argument("--dest-root", type=Path, default=PROJECT_ROOT, help="folders are restored under this root")
    parser.add_argument("--only-prefix", help="restore only rows whose path starts with this (e.g. tensor_data/)")
    parser.add_argument("--overwrite", action="store_true", help="replace files that already exist")
    parser.add_argument("--dry-run", action="store_true", help="download and check rows; write nothing")
    parser.add_argument("--min-free-gb", type=float, default=15.0)
    parser.add_argument("--use-project-token", action="store_true",
                        help=f"authenticate with {PROJECT_TOKEN_FILE.relative_to(PROJECT_ROOT)} (public repo needs none)")
    args = parser.parse_args(argv)

    from huggingface_hub import hf_hub_download

    token = PROJECT_TOKEN_FILE.read_text(encoding="utf-8").strip() if args.use_project_token else False
    work = Path(tempfile.mkdtemp(prefix="restore-shards-"))
    try:
        manifest = json.loads(Path(hf_hub_download(args.repo, MANIFEST, repo_type="dataset", revision=args.revision,
                                                   token=token, local_dir=work)).read_text(encoding="utf-8"))
        result = restore_unit(manifest, args.unit_id, hub_fetcher(args.repo, args.revision, token, work),
                              args.dest_root.resolve(), source=args.source, only_prefix=args.only_prefix,
                              overwrite=args.overwrite, dry_run=args.dry_run, min_free_gb=args.min_free_gb)
    except RestoreError as error:
        print(f"[error] {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
