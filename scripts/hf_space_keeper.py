#!/usr/bin/env python3
"""Keep disk space free while rollouts run: upload finished rollouts to the Hub, verify them twice, then
delete their local activation captures.

Every cycle, for each rollouts root (default: artifacts/rollouts-v2, artifacts/rollouts-v1h2):

  1. upload   finished rollouts (rollout.json has ``report`` and ``finished_at``, untouched for
              --min-age-minutes) that the Hub manifest does not list yet, via scripts/upload_raw_data_to_hf.py
              (shared shards, one finalize commit). Waits until --upload-min-gb has piled up, or the oldest
              waiting rollout is --max-wait-hours old.
  2. check 1  (manifest / shard level, Hub revision pinned to one commit) the unit is in manifest/units.json;
              its signature (files, bytes, max mtime) equals the local one exactly; its folders are exactly
              this rollout folder; every shard it names exists in the Hub tree with the manifest's size.
  3. check 2  (content level, independent of the manifest signature) every shard is downloaded at that
              commit and every row of the unit is checked: chunk_sha256 matches the row bytes AND the bytes
              re-serialised from the local file (same serialisation as the uploader); dtype / shape / nbytes
              (arrays) and size / whole-file sha256 (files) match; every local file and npz member has rows,
              every value has chunks 0..count-1, and the row count equals the manifest's ``rows``.
  4. delete   only if both checks pass and the local signature is still unchanged: the rollout's
              ``steps/*/capture`` folders (features.npz + metadata.json, ~80 % of a rollout). rollout.json and
              the other step json files stay, so ``scenario_rollout.py plan`` resume keeps working.

Every decision is appended to artifacts/hf-upload/space-keeper-ledger.jsonl. A failed check never deletes
anything; the unit is retried next cycle (and reported). Restore a cleaned rollout with
``scripts/restore_raw_unit_from_hf.py --unit-id <rollout folder> --revision <hub_commit from the ledger>``.

    python scripts/hf_space_keeper.py --once --dry-run     # report only: no upload, no delete
    python scripts/hf_space_keeper.py                      # loop every --interval-minutes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from Research.hidden_state_dashboard import RawDataIndex  # noqa: E402
import upload_raw_data_to_hf as up  # noqa: E402

DEFAULT_ROOTS = ("artifacts/rollouts-v2", "artifacts/rollouts-v1h2")
LEDGER = PROJECT_ROOT / "artifacts" / "hf-upload" / "space-keeper-ledger.jsonl"
SOURCE = "scenario_rollout"
GB = 1024**3


class CheckError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"{datetime.now().strftime('%F %T')} {message}", flush=True)


def ledger(record: dict, path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": now(), **record}, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- local state

def capture_dirs(folder: Path) -> list[Path]:
    return sorted(p for p in (folder / "steps").glob("*/capture") if p.is_dir() and not p.is_symlink())


def finished(folder: Path) -> bool:
    try:
        record = json.loads((folder / "rollout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(record.get("report")) and bool(record.get("finished_at"))


def local_units(root: Path, min_age_minutes: float) -> dict[str, dict]:
    """Finished, settled rollout units under ``root`` that still have captures: id -> {unit, signature, folder}."""
    index = RawDataIndex(None, root)
    out = {}
    cutoff = time.time() - min_age_minutes * 60
    for unit_id, unit in index.units(SOURCE).items():
        folder = unit["parts"][0][1]
        if len(unit["parts"]) != 1 or not finished(folder) or not capture_dirs(folder):
            continue
        sig = up.signature(unit, index)
        if sig["modified"] > cutoff:
            continue
        out[unit_id] = {"unit": unit, "signature": sig, "folder": folder, "index": index}
    return out


# ---------------------------------------------------------------- hub state

class Hub:
    def __init__(self, repo: str, token: str | None):
        from huggingface_hub import HfApi

        self.repo, self.token, self.api = repo, token, HfApi(token=token)

    def snapshot(self) -> tuple[str, dict, dict[str, int]]:
        """(commit sha, manifest, {data/ path: size}) all read at one pinned commit."""
        from huggingface_hub import hf_hub_download

        sha = self.api.dataset_info(self.repo).sha
        manifest = json.loads(Path(hf_hub_download(self.repo, up.MANIFEST, repo_type="dataset", revision=sha,
                                                   token=self.token)).read_text(encoding="utf-8"))
        tree = {item.path: item.size for item in self.api.list_repo_tree(self.repo, repo_type="dataset",
                                                                         revision=sha, recursive=True)
                if hasattr(item, "size") and item.path.startswith("data/")}
        return sha, manifest, tree

    def fetch(self, path: str, revision: str, work: Path) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(self.repo, path, repo_type="dataset", revision=revision, token=self.token,
                                    local_dir=work))


# ---------------------------------------------------------------- checks

def check_manifest(unit_id: str, local: dict, manifest: dict, tree: dict[str, int]) -> dict:
    """Check 1: manifest entry, exact signature, folders, shards present with the manifest's size."""
    entry = manifest.get("units", {}).get(SOURCE, {}).get(unit_id)
    if entry is None:
        raise CheckError("not in the Hub manifest")
    if entry["signature"] != local["signature"]:
        raise CheckError(f"signature differs: hub {entry['signature']} local {local['signature']}")
    folders = [local["index"]._display(path) for _, path in local["unit"]["parts"]]
    if entry["folders"] != folders:
        raise CheckError(f"folders differ: hub {entry['folders']} local {folders}")
    if entry.get("skipped"):
        raise CheckError(f"{entry['skipped']} file(s) were skipped at upload")
    if not entry.get("shards") or not entry.get("rows"):
        raise CheckError("manifest entry has no shards/rows")
    for shard in entry["shards"]:
        want = manifest["shards"].get(shard, {}).get("bytes")
        if want is None or tree.get(shard) != want:
            raise CheckError(f"shard {shard}: manifest {want} / hub tree {tree.get(shard)}")
    return entry


class ContentCheck:
    """Check 2 for one unit: feed it every Hub row of the unit, then ``finish``."""

    def __init__(self, unit_id: str, local: dict):
        self.unit_id = unit_id
        self.parts = local["unit"]["parts"]
        self.rows = 0
        self.chunks: dict[tuple, set[int]] = {}
        self.counts: dict[tuple, int] = {}
        self.file_digest: dict[str, str] = {}
        self._arrays: dict[str, dict] = {}
        self.expected = self._expected(local)

    def _target(self, path: str) -> Path:
        for prefix, folder in sorted(self.parts, key=lambda item: len(item[0]), reverse=True):
            if path.startswith(prefix):
                rel = path[len(prefix):]
                if rel.startswith("/") or ".." in Path(rel).parts:
                    raise CheckError(f"unsafe row path {path!r}")
                return folder / rel
        raise CheckError(f"row path {path!r} matches no folder")

    def _expected(self, local: dict) -> set[tuple]:
        """Every (table, path, array_name) the local unit must have on the Hub."""
        want = set()
        for prefix, folder in self.parts:
            for relative, _, _ in local["index"].scan(folder):
                path, target = prefix + relative, folder / relative
                suffix = target.suffix.lower()
                if suffix == ".npz":
                    with np.load(target, allow_pickle=False) as archive:
                        want |= {("tensors", path, name) for name in archive.files}
                elif suffix == ".npy":
                    want.add(("tensors", path, None))
                else:
                    want.add(("files", path, None))
        return want

    def _local_array(self, path: str, name: str | None) -> np.ndarray:
        key = f"{path}|{name}"
        if key not in self._arrays:
            target = self._target(path)
            if name is None:
                self._arrays[key] = up.open_npy(target)
            else:
                with np.load(target, allow_pickle=False) as archive:
                    self._arrays[key] = up.little_endian(archive[name])
        return self._arrays[key]

    def add(self, table: str, row: dict) -> None:
        self.rows += 1
        path, name = row["path"], row.get("array_name")
        if table == "tensors":
            blob = row["data"] or b""
        else:
            blob = row["text"].encode("utf-8") if row.get("text") is not None else (row.get("content") or b"")
        digest = hashlib.sha256(blob).hexdigest()
        if digest != row["chunk_sha256"]:
            raise CheckError(f"chunk_sha256 differs from the Hub row bytes: {path} chunk {row['chunk_index']}")
        if table == "tensors":
            array = self._local_array(path, name)
            if (array.dtype.name, list(array.shape), array.nbytes) != (row["dtype"], list(row["shape"]), row["nbytes"]):
                raise CheckError(f"dtype/shape/nbytes differ: {path}|{name}")
            local = up.array_bytes(array, row["chunk_offset"], len(blob))
        else:
            target = self._target(path)
            if target.stat().st_size != row["bytes"]:
                raise CheckError(f"file size differs: {path}")
            with target.open("rb") as handle:
                handle.seek(row["chunk_offset"])
                local = handle.read(len(blob))
            if path not in self.file_digest:
                self.file_digest[path] = hashlib.sha256(target.read_bytes()).hexdigest()
            if self.file_digest[path] != row["sha256"]:
                raise CheckError(f"whole-file sha256 differs: {path}")
        if hashlib.sha256(local).hexdigest() != row["chunk_sha256"]:
            raise CheckError(f"local bytes differ from the Hub row: {path}|{name} chunk {row['chunk_index']}")
        key = (table, path, name)
        self.chunks.setdefault(key, set()).add(row["chunk_index"])
        self.counts[key] = row["chunk_count"]

    def finish(self, expected_rows: int) -> dict:
        self._arrays.clear()
        if self.rows != expected_rows:
            raise CheckError(f"{self.rows} Hub rows, manifest says {expected_rows}")
        incomplete = [k for k, seen in self.chunks.items() if seen != set(range(self.counts[k]))]
        if incomplete:
            raise CheckError(f"{len(incomplete)} value(s) with missing chunks, e.g. {incomplete[:2]}")
        missing = self.expected - set(self.chunks)
        extra = set(self.chunks) - self.expected
        if missing or extra:
            raise CheckError(f"coverage: {len(missing)} local value(s) not on the Hub, {len(extra)} unknown on the Hub"
                             f" (e.g. {sorted(map(str, missing or extra))[:2]})")
        return {"rows": self.rows, "values": len(self.chunks)}


def iter_rows(shard_file: Path, unit_ids: set[str]):
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq

    table = "tensors" if "data" in pq.ParquetFile(shard_file).schema_arrow.names else "files"
    dataset = ds.dataset(shard_file, format="parquet")
    for batch in dataset.to_batches(filter=ds.field("unit_id").isin(sorted(unit_ids)), batch_size=16):
        for row in batch.to_pylist():
            yield table, row


def check_contents(units: dict[str, dict], entries: dict[str, dict], manifest: dict, fetch, release,
                   min_free_gb: float) -> dict[str, dict | str]:
    """Check 2 for several units; each needed shard is downloaded once. unit id -> result dict or error text."""
    checks, failed = {}, {}
    for unit_id, local in units.items():
        try:
            checks[unit_id] = ContentCheck(unit_id, local)
        except (OSError, ValueError, CheckError) as error:
            failed[unit_id] = f"local scan: {type(error).__name__}: {error}"
    by_shard: dict[str, set[str]] = {}
    for unit_id in checks:
        for shard in entries[unit_id]["shards"]:
            by_shard.setdefault(shard, set()).add(unit_id)
    for shard, wanted in sorted(by_shard.items()):
        wanted -= set(failed)
        if not wanted:
            continue
        need = manifest["shards"][shard]["bytes"]
        if shutil.disk_usage(PROJECT_ROOT).free - need < min_free_gb * GB:
            for unit_id in wanted:
                failed[unit_id] = f"not enough free disk to download {shard}"
            continue
        local_file = fetch(shard)
        try:
            for table, row in iter_rows(local_file, wanted):
                unit_id = row["unit_id"]
                if unit_id in failed:
                    continue
                try:
                    checks[unit_id].add(table, row)
                except (OSError, ValueError, KeyError, CheckError) as error:
                    failed[unit_id] = f"{type(error).__name__}: {error}"
        finally:
            release(local_file)
    results: dict[str, dict | str] = dict(failed)
    for unit_id, check in checks.items():
        if unit_id in failed:
            continue
        try:
            results[unit_id] = check.finish(entries[unit_id]["rows"])
        except CheckError as error:
            results[unit_id] = f"{type(error).__name__}: {error}"
    return results


# ---------------------------------------------------------------- delete

def delete_captures(root: Path, folder: Path) -> tuple[int, int]:
    """Remove ``folder/steps/*/capture`` (no symlinks followed). Returns (folders, bytes) removed."""
    root, folder = root.resolve(), folder.resolve()
    if folder.parent != root or folder.is_symlink():
        raise CheckError(f"refusing to delete outside {root}: {folder}")
    removed = freed = 0
    for capture in capture_dirs(folder):
        if capture.resolve().parent.parent.parent != folder:
            raise CheckError(f"unexpected capture path {capture}")
        freed += sum(p.stat().st_size for p in capture.rglob("*") if p.is_file() and not p.is_symlink())
        shutil.rmtree(capture)
        removed += 1
    return removed, freed


# ---------------------------------------------------------------- cycle

def upload(root: Path, unit_ids: list[str], python: str) -> int:
    cmd = [python, str(PROJECT_ROOT / "scripts" / "upload_raw_data_to_hf.py"), "--source", SOURCE,
           "--rollouts", str(root), "--units", ",".join(unit_ids), "--yes", "--min-age-minutes", "0"]
    log(f"[upload] {root.name}: {len(unit_ids)} unit(s)")
    return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode


def cycle(args, hub: Hub | None) -> dict:
    summary = {"uploaded": 0, "verified": 0, "deleted": 0, "freed": 0, "failed": 0}
    roots = [PROJECT_ROOT / r if not Path(r).is_absolute() else Path(r) for r in args.roots]
    sha, manifest, tree = hub.snapshot() if hub else ("", {"units": {}, "shards": {}}, {})
    on_hub = manifest.get("units", {}).get(SOURCE, {})

    # 1. upload what is not on the Hub yet
    uploaded_any = False
    for root in roots:
        if not root.is_dir():
            continue
        local = local_units(root, args.min_age_minutes)
        new = {u: v for u, v in local.items() if u not in on_hub}
        if not new:
            continue
        size = sum(v["signature"]["bytes"] for v in new.values())
        oldest = min(v["signature"]["modified"] for v in new.values())
        due = size >= args.upload_min_gb * 1e9 or time.time() - oldest >= args.max_wait_hours * 3600
        log(f"[plan] {root.name}: {len(new)} new unit(s), {size / 1e9:.2f} GB, oldest {(time.time() - oldest) / 3600:.1f} h"
            + ("" if due else " (waiting for more)"))
        if due and not args.dry_run:
            names = sorted(new)
            for start in range(0, len(names), args.max_units_per_upload):
                code = upload(root, names[start:start + args.max_units_per_upload], args.python)
                ledger({"event": "upload", "root": str(root.relative_to(PROJECT_ROOT)), "units": len(names[start:start + args.max_units_per_upload]),
                        "returncode": code})
                if code == 0:
                    summary["uploaded"] += len(names[start:start + args.max_units_per_upload])
                    uploaded_any = True
                else:
                    log(f"[upload] failed (exit {code}); retry next cycle")
                    break
    if uploaded_any:
        sha, manifest, tree = hub.snapshot()

    # 2-4. verify twice, delete
    for root in roots:
        if not root.is_dir():
            continue
        local = {u: v for u, v in local_units(root, args.min_age_minutes).items()
                 if u in manifest.get("units", {}).get(SOURCE, {})}
        if not local:
            continue
        entries, passed1 = {}, {}
        for unit_id, item in local.items():
            try:
                entries[unit_id] = check_manifest(unit_id, item, manifest, tree)
                passed1[unit_id] = item
            except CheckError as error:
                summary["failed"] += 1
                log(f"[check1 FAIL] {unit_id}: {error}")
                ledger({"event": "check_failed", "stage": 1, "unit_id": unit_id, "hub_commit": sha, "error": str(error)})
        if not passed1:
            continue
        log(f"[check1] {root.name}: {len(passed1)}/{len(local)} passed; check 2 (download + content)")
        with tempfile.TemporaryDirectory(prefix="space-keeper-", dir=args.work_dir) as work:
            work = Path(work)
            results = check_contents(passed1, entries, manifest, lambda p: hub.fetch(p, sha, work),
                                     lambda f: Path(f).unlink(missing_ok=True), args.min_free_gb)
        for unit_id, result in sorted(results.items()):
            item = passed1[unit_id]
            if isinstance(result, str):
                summary["failed"] += 1
                log(f"[check2 FAIL] {unit_id}: {result}")
                ledger({"event": "check_failed", "stage": 2, "unit_id": unit_id, "hub_commit": sha, "error": result})
                continue
            summary["verified"] += 1
            again = up.signature(item["unit"], item["index"])
            if again != entries[unit_id]["signature"]:
                summary["failed"] += 1
                log(f"[pre-delete FAIL] {unit_id}: changed during the check")
                ledger({"event": "check_failed", "stage": "pre_delete", "unit_id": unit_id, "hub_commit": sha})
                continue
            record = {"event": "deleted" if not args.dry_run else "would_delete", "unit_id": unit_id,
                      "root": str(root.relative_to(PROJECT_ROOT)), "local_path": str(item["folder"].relative_to(PROJECT_ROOT)),
                      "hub_repo": args.repo, "hub_commit": sha, "batch": entries[unit_id].get("batch"),
                      "signature": entries[unit_id]["signature"], "shards": entries[unit_id]["shards"],
                      "check1": "pass", "check2": result, "kept": "rollout.json and steps/*/*.json (captures removed)"}
            if args.dry_run:
                record["capture_dirs"] = len(capture_dirs(item["folder"]))
            else:
                removed, freed = delete_captures(root, item["folder"])
                record |= {"capture_dirs": removed, "bytes_freed": freed}
                summary["deleted"] += 1
                summary["freed"] += freed
            ledger(record)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--roots", nargs="+", default=list(DEFAULT_ROOTS))
    parser.add_argument("--repo", default=up.DEFAULT_REPO)
    parser.add_argument("--interval-minutes", type=float, default=30)
    parser.add_argument("--min-age-minutes", type=float, default=30, help="a rollout must be untouched this long")
    parser.add_argument("--upload-min-gb", type=float, default=2.0, help="upload once this much new data waits")
    parser.add_argument("--max-wait-hours", type=float, default=3.0, help="...or the oldest waiting unit is this old")
    parser.add_argument("--max-units-per-upload", type=int, default=400)
    parser.add_argument("--min-free-gb", type=float, default=15.0, help="never download a shard below this")
    parser.add_argument("--work-dir", default=None, help="where shards are downloaded for check 2 (default: /tmp)")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="no upload, no delete; report and ledger would_delete")
    args = parser.parse_args(argv)

    token = up.PROJECT_TOKEN_FILE.read_text(encoding="utf-8").strip() if up.PROJECT_TOKEN_FILE.is_file() else None
    hub = Hub(args.repo, token)
    owner, user = args.repo.split("/", 1)[0], hub.api.whoami().get("name")
    if user != owner:
        raise SystemExit(f"token belongs to {user!r}, repo owner is {owner!r}")
    log(f"[start] repo {args.repo} as {user}; roots {args.roots}; dry_run={args.dry_run}")
    while True:
        try:
            summary = cycle(args, hub)
            log(f"[cycle] uploaded {summary['uploaded']} · verified {summary['verified']} · deleted {summary['deleted']}"
                f" ({summary['freed'] / 1e9:.2f} GB) · failed {summary['failed']} · free {shutil.disk_usage(PROJECT_ROOT).free / GB:.1f} GiB")
        except Exception as error:  # keep the keeper alive; the next cycle starts from the Hub state again
            log(f"[cycle error] {type(error).__name__}: {error}")
            ledger({"event": "cycle_error", "error": f"{type(error).__name__}: {error}"})
        if args.once:
            return 0
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
