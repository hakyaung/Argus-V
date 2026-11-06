#!/usr/bin/env python3
"""Upload agent_loop.py / scenario_rollout.py raw outputs to a Hugging Face dataset as Parquet.

The split is the dashboard's 원본 데이터 tab (Research/hidden_state_dashboard.py RawDataIndex):

  agent_loop        tensor_data/<run>           (every tensor_data folder except scenario hook runs)
  scenario_rollout  artifacts/rollouts/<rollout> + tensor_data/scenario-*-<id8>, scenario-replay-*-<id8>

Each source becomes two dataset configs (subsets) in the repo:

  <source>_tensors  data/<source>/tensors/*.parquet  one row per array: every .npy file and every array
                    inside an .npz, joined with its records.jsonl line (module, layer, location, ...).
                    ``data`` holds the raw little-endian C-order bytes; rebuild with
                    np.frombuffer(row["data"], dtype=row["dtype"]).reshape(row["shape"]).
  <source>_files    data/<source>/files/*.parquet    every other file (json, jsonl, md, ...) as text
                    (UTF-8) or content (binary), with size and sha256.

A Parquet value cannot reach 2 GiB and huge cells make row groups unusable, so an array or file larger
than --chunk-mb is split into consecutive rows with chunk_index / chunk_count / chunk_offset: join the
``data`` (or ``content``) of all chunks of the same (unit_id, path, array_name) in chunk_index order.
Every row carries ``chunk_sha256`` of its own bytes, so a download can be checked piece by piece.

The run has three phases, so the Hub sees few commits (it allows 128 per repo and hour):

  1. convert   every planned unit into one local batch folder (--staging/<batch>/data/...); shards hold
               many units. Nothing is uploaded if any source file could not be read.
  2. upload    the batch folder with HfApi.upload_folder (batched commits; re-running resumes it).
  3. finalize  after checking that every shard is on the Hub with its local size: one commit writes
               manifest/units.json (which unit is in which shard) and README.md, and deletes every
               data/ file the manifest does not reference (left-overs of earlier or partial uploads).

The Hub manifest, not a local file, decides what is already uploaded: a re-run uploads only new units.
A unit that changed after its upload cannot be replaced inside shared shards: use --rebuild, which
converts and uploads everything again and switches over in the finalize commit. An interrupted run resumes
from its converted batch (phase 2), or re-converts if conversion itself was interrupted.

    pip install pyarrow huggingface_hub hf_xet
    hf auth login            # token with write access to the target repo (or set HF_TOKEN)

    python scripts/upload_raw_data_to_hf.py --dry-run --limit-units 2      # convert + check, nothing kept
    python scripts/upload_raw_data_to_hf.py --source all                    # asks before uploading
    python scripts/upload_raw_data_to_hf.py --source all --yes --rebuild    # replace everything on the Hub
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from Research.hidden_state_dashboard import RawDataIndex  # noqa: E402

DEFAULT_REPO = "hakuuung/gpt-oss-20b-hidden-state-raw-data"
PROJECT_TOKEN_FILE = PROJECT_ROOT / ".secrets" / "hf_token_hakuuung"  # gitignored; Argus-V-only write token
SOURCES = RawDataIndex.SOURCES
TABLES = ("tensors", "files")
MANIFEST = "manifest/units.json"
BATCH_FORMAT = "shared-shards-v1"  # batch.json / manifest written by this script; other layouts are never reused
MB, GB = 1024**2, 1024**3
PARQUET_RATIO = 0.4  # measured ~0.22 (zstd) on these activations; leave margin when checking free disk
README_START, README_END = "<!-- argus-raw-data:start -->", "<!-- argus-raw-data:end -->"


def schemas():
    import pyarrow as pa

    unit = [("source", pa.string()), ("unit_id", pa.string()), ("unit_kind", pa.string()),
            ("folder", pa.string()), ("path", pa.string()),
            # values over --chunk-mb are split over rows; small ones are chunk 0 of 1 at offset 0
            ("chunk_index", pa.int32()), ("chunk_count", pa.int32()), ("chunk_offset", pa.int64()),
            ("chunk_sha256", pa.string())]  # of this row's data/content bytes (text: its UTF-8 bytes)
    tensors = pa.schema(unit + [
        ("array_name", pa.string()),  # key inside an .npz; null for .npy
        ("dtype", pa.string()), ("shape", pa.list_(pa.int64())), ("nbytes", pa.int64()),  # nbytes: whole array
        ("record_id", pa.int64()), ("phase", pa.string()), ("module", pa.string()),
        ("module_class", pa.string()), ("layer_index", pa.int64()), ("call_index", pa.int64()),
        ("forward_call_index", pa.int64()), ("location", pa.string()), ("meaning", pa.string()),
        ("source_dtype", pa.string()),
        ("record_json", pa.large_string()),  # the full records.jsonl line (null when no record lists the file)
        ("data", pa.large_binary()),
    ])
    files = pa.schema(unit + [
        ("bytes", pa.int64()), ("sha256", pa.string()), ("modified", pa.float64()),  # of the whole file
        ("text", pa.large_string()), ("content", pa.large_binary()),  # exactly one is set; chunks are content
    ])
    return {"tensors": tensors, "files": files}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unit"


def little_endian(array: np.ndarray) -> np.ndarray:
    array = np.ascontiguousarray(array)
    if array.dtype.byteorder == ">":
        array = array.astype(array.dtype.newbyteorder("<"))
    return array


def chunk_ranges(size: int, chunk: int) -> list[tuple[int, int]]:
    """(offset, length) pieces of at most ``chunk`` bytes; an empty value is one empty piece."""
    return [(offset, min(chunk, size - offset)) for offset in range(0, size, chunk)] or [(0, 0)]


def open_npy(path: Path) -> np.ndarray:
    """Memory-map a plain little-endian C-order .npy so chunks are read lazily; otherwise load and normalize."""
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.flags.c_contiguous and array.dtype.byteorder in ("<", "=", "|"):
        return array
    return little_endian(np.asarray(array))


def array_bytes(array: np.ndarray, offset: int, length: int) -> bytes:
    return memoryview(array.reshape(-1).view(np.uint8))[offset:offset + length].tobytes()


class RecordLookup:
    """records.jsonl line for a tensor file (``filename`` is relative to the records file's folder).

    Files are visited in sorted order, so one capture's tensors are contiguous: keep one records file cached.
    """

    FIELDS = ("record_id", "phase", "module", "module_class", "layer_index", "call_index", "forward_call_index",
              "location", "meaning", "source_dtype")

    def __init__(self):
        self.key, self.records = None, {}

    def find(self, path: Path) -> dict | None:
        for folder in (path.parent.parent, path.parent):
            records = folder / "records.jsonl"
            if records.is_file():
                if self.key != records:
                    self.key, self.records = records, {}
                    with records.open(encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if line:
                                record = json.loads(line)
                                if record.get("filename"):
                                    self.records[record["filename"]] = (record, line)
                found = self.records.get(path.relative_to(folder).as_posix())
                if found:
                    return found
        return None


class ShardWriter:
    """Parquet shards data/<source>/<table>/<batch>-NNNNN.parquet shared by many units (~``shard_bytes`` each).

    ``membership`` maps each closed shard to the units with rows in it (for the Hub manifest).
    """

    def __init__(self, root: Path, source: str, table: str, batch: str, schema, shard_bytes: int,
                 row_group_bytes: int, compression: str | None, on_close=None):
        self.root, self.source, self.table, self.batch = root, source, table, batch
        self.schema, self.compression, self.on_close = schema, compression, on_close or (lambda _path: None)
        self.shard_bytes, self.row_group_bytes = shard_bytes, row_group_bytes
        self.rows, self.pending, self.written, self.index, self.writer = [], 0, 0, 0, None
        self.units: set[str] = set()
        self.membership: dict[str, list[str]] = {}

    def repo_path(self, index: int) -> str:
        return f"data/{self.source}/{self.table}/{self.batch}-{index:05d}.parquet"

    def add(self, row: dict, size: int) -> None:
        self.rows.append(row)
        self.pending += size
        if self.pending >= self.row_group_bytes:
            self.flush()

    def flush(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if not self.rows:
            return
        if self.writer is None:
            local = self.root / self.repo_path(self.index)
            local.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(local, self.schema, compression=self.compression)
        self.writer.write_table(pa.Table.from_pylist(self.rows, schema=self.schema))  # one row group
        self.units.update(row["unit_id"] for row in self.rows)
        self.written += self.pending
        self.rows, self.pending = [], 0
        if self.written >= self.shard_bytes:
            self.close_shard()

    def close_shard(self) -> None:
        if self.writer is None:
            return
        self.writer.close()
        path = self.repo_path(self.index)
        self.membership[path] = sorted(self.units)
        self.writer, self.written, self.index, self.units = None, 0, self.index + 1, set()
        self.on_close(self.root / path)

    def close(self) -> dict[str, list[str]]:
        self.flush()
        self.close_shard()
        return self.membership


def convert_unit(source: str, unit_id: str, unit: dict, index: RawDataIndex, writers: dict,
                 chunk_bytes: int = 256 * MB, log=print) -> dict:
    """Write one unit's arrays and files into the two shard writers; return row counts.

    Only reading a source file may skip it (removed meanwhile, damaged or pickled array); skips are listed
    in ``counts["skipped_paths"]`` and stop the upload. A Parquet write error is not caught: it would leave
    the shard broken for every later file, so the run stops instead.
    """
    lookup = RecordLookup()
    counts = {"tensors": 0, "files": 0, "rows": 0, "chunked": 0, "skipped": 0, "skipped_paths": []}
    for prefix, folder in unit["parts"]:
        base = {"source": source, "unit_id": unit_id, "unit_kind": unit["kind"], "folder": index._display(folder)}
        for relative, _, _ in index.scan(folder):
            target, path = folder / relative, prefix + relative
            suffix = target.suffix.lower()
            try:
                if suffix == ".npy":
                    arrays = [(None, open_npy(target))]
                    record, line = lookup.find(target) or ({}, None)
                elif suffix == ".npz":
                    with np.load(target, allow_pickle=False) as archive:
                        arrays = [(name, little_endian(archive[name])) for name in archive.files]
                    record, line = {}, None
                else:
                    raw, modified = target.read_bytes(), target.stat().st_mtime
            except (OSError, ValueError, EOFError) as error:
                counts["skipped"] += 1
                counts["skipped_paths"].append(f"{path}: {type(error).__name__}: {error}")
                log(f"  [unreadable] {path}: {type(error).__name__}: {error}")
                continue
            if suffix in (".npy", ".npz"):
                for name, array in arrays:
                    ranges = chunk_ranges(array.nbytes, chunk_bytes)
                    fields = {**base, "path": path, "array_name": name, "dtype": array.dtype.name,
                              "shape": list(array.shape), "nbytes": array.nbytes,
                              **{field: record.get(field) for field in RecordLookup.FIELDS}, "record_json": line}
                    for number, (offset, length) in enumerate(ranges):
                        data = array_bytes(array, offset, length)
                        writers["tensors"].add({**fields, "chunk_index": number, "chunk_count": len(ranges),
                                                "chunk_offset": offset, "chunk_sha256": hashlib.sha256(data).hexdigest(),
                                                "data": data}, length)
                    counts["tensors"] += 1
                    counts["rows"] += len(ranges)
                    counts["chunked"] += len(ranges) > 1
                del arrays  # release the memory map before the next file
            else:
                ranges = chunk_ranges(len(raw), chunk_bytes)
                text = None
                if len(ranges) == 1:
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                fields = {**base, "path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                          "modified": modified}
                for number, (offset, length) in enumerate(ranges):
                    piece = raw[offset:offset + length]
                    writers["files"].add({**fields, "chunk_index": number, "chunk_count": len(ranges),
                                          "chunk_offset": offset, "chunk_sha256": hashlib.sha256(piece).hexdigest(),
                                          "text": text, "content": None if text is not None else piece}, length)
                counts["files"] += 1
                counts["rows"] += len(ranges)
                counts["chunked"] += len(ranges) > 1
    return counts


def signature(unit: dict, index: RawDataIndex) -> dict:
    files = [row for _, folder in unit["parts"] for row in index.scan(folder)]
    return {"files": len(files), "bytes": sum(size for _, size, _ in files),
            "modified": max((mtime for _, _, mtime in files), default=0.0)}


def readme(existing: str, manifest: dict) -> str:
    """Keep the existing card (license etc.); set the configs and replace only our generated section."""
    from huggingface_hub import DatasetCard

    card = DatasetCard(existing or "---\n---\n")
    card.data["configs"] = [{"config_name": f"{source}_{table}", "data_files": f"data/{source}/{table}/*.parquet"}
                            for source in SOURCES for table in TABLES]
    lines = [README_START, "## Argus-V raw hidden-state data (gpt-oss-20b)", "",
             "Raw outputs of `argus_hook/agent_loop.py` (manual tasks) and `argus_hook/scenario_rollout.py`"
             " (scenario rollouts), kept apart as separate subsets.", "",
             "| subset | content |", "|---|---|",
             "| `agent_loop_tensors` / `scenario_rollout_tensors` | one row per array (`.npy` file or `.npz` member),"
             " joined with its HookInspection `records.jsonl` line |",
             "| `agent_loop_files` / `scenario_rollout_files` | every other file (rollout.json, step.json, records.jsonl,"
             " manifest.json, transcripts …) |", "",
             "```python", "import numpy as np", "from datasets import load_dataset", "",
             "rows = load_dataset(\"<repo>\", \"scenario_rollout_tensors\", split=\"train\", streaming=True)",
             "row = next(iter(rows))",
             "array = np.frombuffer(row[\"data\"], dtype=row[\"dtype\"]).reshape(row[\"shape\"])",
             "# arrays/files over 256 MiB span rows: concatenate `data`/`content` of the same",
             "# (unit_id, path, array_name) in `chunk_index` order (chunk_count rows) before reshaping;",
             "# every row's `chunk_sha256` is the sha256 of its own bytes", "```", "",
             "`unit_id` is one run (`tensor_data/run-*`) or one rollout (`artifacts/rollouts/<id>` plus its"
             " `tensor_data/scenario-*` Hook folders); `path` is the file path inside that unit.",
             f"`{MANIFEST}` lists every unit with its source file count/size and the shards holding its rows.", "",
             "| source | units | arrays | files | source bytes |", "|---|---:|---:|---:|---:|"]
    for source in SOURCES:
        units = manifest.get("units", {}).get(source, {})
        lines.append(f"| {source} | {len(units)} | {sum(u['tensors'] for u in units.values()):,} |"
                     f" {sum(u['files'] for u in units.values()):,} |"
                     f" {sum(u['signature']['bytes'] for u in units.values()) / 1e9:,.2f} GB |")
    lines.append(README_END)
    text = card.text
    if README_START in text and README_END in text:
        text = text[:text.index(README_START)] + text[text.index(README_END) + len(README_END):]
    card.text = text.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    return str(card)


def retry_after(error) -> float | None:
    """Seconds to wait for an HTTP 429 (Retry-After, else 10 minutes); None for any other error."""
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) != 429:
        return None
    try:
        return max(60.0, float(response.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return 600.0


def with_retries(action, label: str, rate_limit_wait: float, sleep=None, log=print):
    """Run ``action``; wait out rate limits (up to ``rate_limit_wait`` s in total) and retry other errors twice."""
    sleep = sleep or (lambda seconds: time.sleep(seconds))  # looked up at call time (tests patch it)
    waited, attempt = 0.0, 0
    while True:
        try:
            return action()
        except Exception as error:
            wait = retry_after(error)
            if wait is not None:
                if waited + wait > rate_limit_wait:
                    raise
                log(f"[rate limit] {label}: Hub limit reached; waiting {wait / 60:.0f} min, then resuming")
                waited += wait
            else:
                attempt += 1
                if attempt == 3:
                    raise
                wait = 30 * attempt
                log(f"[retry {attempt}] {label} failed ({type(error).__name__}: {error}); waiting {wait}s")
            sleep(wait)


def remote_state(api, repo: str, token) -> tuple[dict, dict[str, int], str]:
    """(manifest, {data/ file: size}, README text) currently on the Hub."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    files = {item.path: item.size for item in api.list_repo_tree(repo, repo_type="dataset", recursive=True)
             if hasattr(item, "size") and item.path.startswith("data/")}
    texts = {}
    for name in (MANIFEST, "README.md"):
        try:
            texts[name] = Path(hf_hub_download(repo, name, repo_type="dataset", token=token)).read_text(encoding="utf-8")
        except EntryNotFoundError:
            texts[name] = ""
    manifest = json.loads(texts[MANIFEST]) if texts[MANIFEST] else {"units": {}, "shards": {}}
    if manifest.get("format", BATCH_FORMAT) != BATCH_FORMAT or "shards" not in manifest:
        # a manifest of another layout (e.g. one Parquet file per unit) does not describe these shards:
        # treat everything as not uploaded; the finalize commit then removes those data/ files
        print("[warn] the Hub manifest has another layout; uploading everything again in this layout")
        manifest = {"units": {}, "shards": {}}
    return manifest, files, texts["README.md"]


def plan_units(index: RawDataIndex, sources, wanted, limit: int | None, manifest: dict, rebuild: bool,
               min_age_minutes: float) -> tuple[list, list[str], list[str]]:
    """(units to convert, skipped messages, changed-since-upload messages)."""
    now, plan, skipped, changed = time.time(), [], [], []
    for source in sources:
        chosen = [(unit_id, unit, signature(unit, index)) for unit_id, unit in index.units(source).items()
                  if wanted is None or unit_id in wanted]
        chosen.sort(key=lambda item: item[2]["modified"], reverse=True)
        if limit:
            chosen = chosen[:limit]
        for unit_id, unit, sig in chosen:
            uploaded = manifest.get("units", {}).get(source, {}).get(unit_id)
            if now - sig["modified"] < min_age_minutes * 60:
                skipped.append(f"{source}/{unit_id} (still being written; next run)")
            elif uploaded and not rebuild:
                if uploaded["signature"] != sig:
                    changed.append(f"{source}/{unit_id}")
                skipped.append(f"{source}/{unit_id} (already on the Hub)")
            else:
                plan.append((source, unit_id, unit, sig))
    return plan, skipped, changed


def convert_batch(plan, index: RawDataIndex, folder: Path, batch: str, args, log=print) -> dict:
    """Phase 1: every planned unit into ``folder`` (shards shared by units); returns the batch record."""
    table_schemas = schemas()
    compression = None if args.compression == "none" else args.compression

    def check_disk(path: Path) -> None:
        free = shutil.disk_usage(folder).free
        if free < 2 * GB:
            raise RuntimeError(f"disk almost full ({free / GB:.1f} GiB free) after {path.name}; "
                               "free space and run again (conversion restarts)")

    writers = {(source, table): ShardWriter(folder, source, table, batch, table_schemas[table], args.shard_mb * MB,
                                            args.row_group_mb * MB, compression, on_close=check_disk)
               for source in SOURCES for table in TABLES}
    units: dict[str, dict] = {}
    for number, (source, unit_id, unit, sig) in enumerate(plan, 1):
        log(f"[convert {number}/{len(plan)}] {source}/{unit_id}: {sig['files']:,} files, {sig['bytes'] / 1e9:.2f} GB")
        counts = convert_unit(source, unit_id, unit, index, {table: writers[(source, table)] for table in TABLES},
                              args.chunk_mb * MB, log=log)
        log(f"  arrays {counts['tensors']:,} · files {counts['files']:,} · rows {counts['rows']:,}"
            + (f" · split into chunks {counts['chunked']}" if counts["chunked"] else "")
            + (f" · UNREADABLE {counts['skipped']}" if counts["skipped"] else ""))
        units.setdefault(source, {})[unit_id] = {"kind": unit["kind"], "signature": sig,
                                                 "folders": [index._display(path) for _, path in unit["parts"]],
                                                 **counts, "shards": []}
    shards = {}
    for (source, _table), writer in writers.items():
        for path, members in writer.close().items():
            shards[path] = {"bytes": (folder / path).stat().st_size, "units": members}
            for unit_id in members:
                units[source][unit_id]["shards"].append(path)
    return {"format": BATCH_FORMAT, "batch": batch, "repo": args.repo,
            "created_at": datetime.now(timezone.utc).isoformat(), "rebuild": args.rebuild,
            "units": units, "shards": shards}


def folder_gib(folder: Path) -> float:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file()) / GB


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=DEFAULT_REPO, help="Hugging Face dataset repo id")
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--units", help="comma-separated unit ids (dashboard 원본 데이터 names); default: all")
    parser.add_argument("--limit-units", type=int, help="at most this many units per source (newest first)")
    parser.add_argument("--tensor-data", type=Path, default=PROJECT_ROOT / "tensor_data")
    parser.add_argument("--rollouts", type=Path, default=PROJECT_ROOT / "artifacts" / "rollouts")
    parser.add_argument("--staging", type=Path, default=PROJECT_ROOT / "artifacts" / "hf-upload" / "staging")
    parser.add_argument("--shard-mb", type=int, default=2048, help="target Parquet shard size (uncompressed)")
    parser.add_argument("--row-group-mb", type=int, default=128)
    parser.add_argument("--chunk-mb", type=int, default=256,
                        help="split arrays/files larger than this over several rows (a Parquet value must stay < 2 GiB)")
    parser.add_argument("--compression", default="zstd", choices=("zstd", "snappy", "gzip", "none"))
    parser.add_argument("--min-age-minutes", type=float, default=10.0,
                        help="skip units written to more recently than this (a run may still be writing)")
    parser.add_argument("--rate-limit-wait-minutes", type=float, default=180,
                        help="on HTTP 429 wait for the limit to reset, up to this long in total")
    parser.add_argument("--rebuild", action="store_true",
                        help="convert and upload every unit again; the finalize commit replaces the old data")
    parser.add_argument("--private", action="store_true", help="create the repo as private if it does not exist")
    parser.add_argument("--dry-run", action="store_true",
                        help="phase 1 only: convert and report; no Hub access, the batch is deleted afterwards")
    parser.add_argument("--keep-shards", action="store_true", help="with --dry-run: keep the converted batch")
    parser.add_argument("--yes", action="store_true", help="do not ask before uploading")
    parser.add_argument("--token", help="Hugging Face token (default: .secrets/hf_token_hakuuung in this repo,"
                                        " then HF_TOKEN or the saved login)")
    args = parser.parse_args(argv)
    if not 1 <= args.chunk_mb < 2048:
        parser.error("--chunk-mb must be 1..2047")
    schemas()  # imports pyarrow: fail early with a clear message

    index = RawDataIndex(args.tensor_data.expanduser(), args.rollouts.expanduser())
    sources = SOURCES if args.source == "all" else (args.source,)
    wanted = set(filter(None, (args.units or "").split(","))) or None
    staging = args.staging.expanduser()

    api, manifest, remote_files, existing_readme = None, {"units": {}, "shards": {}}, {}, ""
    if not args.dry_run:
        from huggingface_hub import HfApi

        # Argus-V uploads use the project-local token (.secrets/, gitignored) ahead of HF_TOKEN or the
        # machine-wide login, which may belong to another project/account.
        if args.token is None and PROJECT_TOKEN_FILE.is_file():
            args.token = PROJECT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        api = HfApi(token=args.token)
        owner = args.repo.split("/", 1)[0]
        user = api.whoami().get("name")
        if user != owner:
            raise SystemExit(f"refusing to upload: token belongs to {user!r}, repo owner is {owner!r}"
                             f" (put the owner's write token in {PROJECT_TOKEN_FILE} or pass --token)")
        print(f"[auth] uploading as {user}")
        api.create_repo(args.repo, repo_type="dataset", private=args.private, exist_ok=True)
        manifest, remote_files, existing_readme = remote_state(api, args.repo, args.token)

    # a converted batch that was not finalized resumes at phase 2 instead of converting again
    pending = None
    for record_path in sorted(staging.glob("*/batch.json")):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("format") != BATCH_FORMAT:  # another script version's layout: never resumed or deleted
            print(f"[warn] ignoring {record_path.parent} (layout {record.get('format', 'unknown')!r}, not"
                  f" {BATCH_FORMAT!r}); delete it yourself to free {folder_gib(record_path.parent):.1f} GiB")
        elif record.get("repo") == args.repo and not args.dry_run and pending is None:
            pending = (record_path.parent, record)
    for folder in staging.glob("*"):  # conversions interrupted before batch.json: restart them
        if folder.is_dir() and not any((folder / name).is_file() for name in ("batch.json", "dry-run.json")):
            shutil.rmtree(folder)

    print(f"Target: https://huggingface.co/datasets/{args.repo}" + ("  (dry run: nothing is uploaded)" if args.dry_run else ""))
    if pending:
        folder, batch = pending
        print(f"Resuming converted batch {batch['batch']} ({sum(len(u) for u in batch['units'].values())} units,"
              f" {sum(s['bytes'] for s in batch['shards'].values()) / GB:.2f} GiB Parquet)")
    else:
        plan, skipped, changed = plan_units(index, sources, wanted, args.limit_units, manifest, args.rebuild,
                                            args.min_age_minutes)
        if wanted:
            known = {unit_id for _, unit_id, _, _ in plan} | {item.split("/", 1)[1].split(" ")[0] for item in skipped}
            if wanted - known:
                parser.error("unknown unit ids: " + ", ".join(sorted(wanted - known)))
        for source in sources:
            rows = [sig for s, _, _, sig in plan if s == source]
            print(f"  {source}: {len(rows)} unit(s) to upload, {sum(r['files'] for r in rows):,} files, "
                  f"{sum(r['bytes'] for r in rows) / 1e9:.2f} GB raw")
        print(f"  skipped: {len(skipped)} unit(s)" + "".join(f"\n    {item}" for item in skipped if "written" in item))
        if changed:
            print(f"[warn] {len(changed)} uploaded unit(s) changed since their upload and are NOT updated"
                  f" (use --rebuild): {', '.join(changed[:5])}{' …' if len(changed) > 5 else ''}")
        if not plan:
            print("Nothing to upload.")
            return 0
        total = sum(sig["bytes"] for *_, sig in plan)
        staging.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(staging).free
        need = int(total * PARQUET_RATIO) + 4 * GB
        if free < need:
            print(f"[error] {free / GB:.1f} GiB free; converting {total / 1e9:.1f} GB needs about {need / GB:.1f} GiB."
                  " Free space or upload one --source at a time.")
            return 1
        if api is not None and not args.yes:
            private = api.dataset_info(args.repo).private
            visibility = "PRIVATE" if private else "PUBLIC (anyone can download it)"
            if input(f"Convert and upload {total / 1e9:.2f} GB to {args.repo}, a {visibility} dataset? [y/N] "
                     ).strip().lower() != "y":
                return 1
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]  # unique shard names
        folder = staging / batch_id
        folder.mkdir()
        batch = convert_batch(plan, index, folder, batch_id, args)
        unreadable = [f"{source}/{unit_id}: {path}" for source, units in batch["units"].items()
                      for unit_id, unit in units.items() for path in unit["skipped_paths"]]
        if unreadable:
            print(f"[error] {len(unreadable)} source file(s) could not be read; nothing is uploaded:")
            for item in unreadable[:20]:
                print(f"    {item}")
            shutil.rmtree(folder)
            return 1
        parquet = sum(shard["bytes"] for shard in batch["shards"].values())
        print(f"[convert] done: {sum(len(u) for u in batch['units'].values())} units → "
              f"{len(batch['shards'])} shard(s), {parquet / GB:.2f} GiB Parquet")
        if args.dry_run:
            if args.keep_shards:
                # not batch.json: a dry run ignores the Hub manifest, so it must never be resumed as an upload
                (folder / "dry-run.json").write_text(json.dumps(batch, indent=1, ensure_ascii=False), encoding="utf-8")
                print(f"[dry-run] converted batch kept under {folder} (delete it when done)")
            else:
                shutil.rmtree(folder)
            return 0
        (folder / "batch.json").write_text(json.dumps(batch, indent=1, ensure_ascii=False), encoding="utf-8")

    # phase 2: upload (resumable: already committed files are skipped, uploaded data is deduplicated)
    print(f"[upload] {len(batch['shards'])} shard(s) → {args.repo}")
    with_retries(lambda: api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(folder),
                                           allow_patterns=["data/**"],
                                           commit_message=f"Add raw data batch {batch['batch']}"),
                 "upload", args.rate_limit_wait_minutes * 60)

    # phase 3: verify every shard on the Hub, then manifest + README + clean-up in one commit
    _, remote_files, _ = remote_state(api, args.repo, args.token)
    missing = [path for path, shard in batch["shards"].items() if remote_files.get(path) != shard["bytes"]]
    if missing:
        print(f"[error] {len(missing)} shard(s) missing or with a different size on the Hub; run again to resume:")
        for path in missing[:20]:
            print(f"    {path}: local {batch['shards'][path]['bytes']} / hub {remote_files.get(path)}")
        return 1
    if batch["rebuild"]:
        manifest = {"units": {}, "shards": {}}
    manifest["format"] = BATCH_FORMAT
    for source, units in batch["units"].items():
        for unit_id, unit in units.items():
            manifest["units"].setdefault(source, {})[unit_id] = {
                key: value for key, value in unit.items() if key != "skipped_paths"} | {"batch": batch["batch"]}
    manifest["shards"].update(batch["shards"])
    referenced = set(manifest["shards"])
    stale = sorted(path for path in remote_files if path not in referenced)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    operations = [CommitOperationAdd(path_in_repo=MANIFEST,
                                     path_or_fileobj=json.dumps(manifest, indent=1, ensure_ascii=False).encode()),
                  CommitOperationAdd(path_in_repo="README.md",
                                     path_or_fileobj=readme(existing_readme, manifest).encode("utf-8"))]
    operations += [CommitOperationDelete(path_in_repo=path) for path in stale]
    with_retries(lambda: api.create_commit(repo_id=args.repo, repo_type="dataset", operations=operations,
                                           commit_message=f"Finalize raw data batch {batch['batch']}"
                                           + (f" (remove {len(stale)} unreferenced file(s))" if stale else "")),
                 "finalize", args.rate_limit_wait_minutes * 60)
    shutil.rmtree(folder)
    print(f"[done] batch {batch['batch']}: {sum(len(u) for u in batch['units'].values())} unit(s) uploaded and"
          f" verified" + (f"; removed {len(stale)} unreferenced data file(s)" if stale else ""))
    for source in SOURCES:
        units = manifest["units"].get(source, {})
        print(f"  {source}: {len(units)} unit(s) on the Hub")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
