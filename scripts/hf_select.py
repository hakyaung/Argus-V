#!/usr/bin/env python3
"""Selective reader for the Argus-V raw hidden-state dataset on the Hugging Face Hub.

Reads only what is needed instead of streaming or downloading whole shards:

1. ``manifest/units.json`` tells which shards hold a unit, so other shards are never opened.
2. The metadata columns (everything except ``data`` / ``content`` / ``text`` / ``record_json``) are read per row group
   over HTTP range requests, and cached locally as a small index (pinned to the Hub commit).
3. Rows are filtered on that index (unit, path glob, array name, layer, location, phase, module, ...).
4. Only the row groups that contain selected rows are read, and only their payload column. Arrays split over
   several rows (values over the uploader's --chunk-mb) are completed automatically, verified against
   ``chunk_sha256`` and joined in ``chunk_index`` order.

The smallest unit of transfer is one row group's payload column chunk; ``plan`` / ``--dry-run`` report how many
bytes a selection will transfer before anything is fetched.

    # what does one rollout contain? (index is built and cached on first use)
    python scripts/hf_select.py ls --unit AV-S09-C0-s2-9b75411e --limit 20
    # how much would layer 16 of that rollout cost to fetch?
    python scripts/hf_select.py get --unit AV-S09-C0-s2-9b75411e --layer 16 --dry-run
    # fetch it into .npy files plus a selection record
    python scripts/hf_select.py get --unit AV-S09-C0-s2-9b75411e --layer 16 --out /tmp/sel

Python use (e.g. building probe features)::

    from hf_select import HubSelector
    sel = HubSelector()
    rows = sel.select(units=["AV-S09-C0-s2-9b75411e"], layer=16)
    for key, array, meta in sel.fetch_arrays(rows):
        ...

Read-only: never writes to the Hub. Downloads need no token for this public repo; when one is needed, the
project-local token (.secrets/hf_token_hakuuung) is used, never the machine-wide login.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPO = "hakuuung/gpt-oss-20b-hidden-state-raw-data"
PROJECT_TOKEN_FILE = PROJECT_ROOT / ".secrets" / "hf_token_hakuuung"
DEFAULT_CACHE = PROJECT_ROOT / "artifacts" / "hf-index"
MANIFEST = "manifest/units.json"
PAYLOAD = {"tensors": "data", "files": "content"}
HEAVY = {"data", "content", "text", "record_json"}  # never part of the index
INDEX_FORMAT = "hf-select-index-v1"


class SelectError(RuntimeError):
    pass


@dataclass(frozen=True)
class Location:
    shard: str
    row_group: int
    row: int  # index within the row group


def array_key(row: dict) -> tuple:
    return (row["unit_id"], row["path"], row.get("array_name"))


_BLOCK = re.compile(r"^block_(\d+)$")


def row_layer(row: dict) -> int | None:
    """HookInspection rows carry ``layer_index``; C-01 rollout captures (``features.npz``) have no record, and
    their layer is in the member name ``block_NNNN``."""
    if row.get("layer_index") is not None:
        return int(row["layer_index"])
    m = _BLOCK.match(row.get("array_name") or "")
    return int(m.group(1)) if m else None


def _table_of(shard: str) -> str:
    return "tensors" if "/tensors/" in shard else "files"


class HubSelector:
    """``opener(shard_path) -> binary file object`` and ``manifest_loader() -> dict`` are injectable for tests."""

    def __init__(self, repo: str = DEFAULT_REPO, revision: str | None = None, cache_dir: Path = DEFAULT_CACHE,
                 token: str | None = None, opener: Callable | None = None, manifest_loader: Callable | None = None,
                 log: Callable = lambda *a: None):
        self.repo, self.cache_dir, self.log = repo, Path(cache_dir), log
        self._token = token
        self._opener, self._manifest_loader = opener, manifest_loader
        self.revision = revision
        self._manifest = None
        self._index: dict[str, list[dict]] = {}  # shard -> metadata rows (with _rg/_row)
        self.stats = {"payload_bytes_read": 0, "row_groups_read": 0}

    # ------------------------------------------------------------------ hub access
    def _resolve_token(self):
        if self._token is None and PROJECT_TOKEN_FILE.is_file():
            self._token = PROJECT_TOKEN_FILE.read_text(encoding="utf-8").strip()
        return self._token

    def _fs(self):
        from huggingface_hub import HfFileSystem

        if not hasattr(self, "_fs_obj"):
            self._fs_obj = HfFileSystem(token=self._resolve_token())
        return self._fs_obj

    def _pin_revision(self):
        if self.revision is None and self._opener is None:
            from huggingface_hub import HfApi

            self.revision = HfApi(token=self._resolve_token()).dataset_info(self.repo).sha
        return self.revision or "local"

    def _open(self, shard: str):
        if self._opener is not None:
            return self._opener(shard)
        rev = self._pin_revision()
        return self._fs().open(f"datasets/{self.repo}@{rev}/{shard}", "rb", block_size=8 * 2**20)

    def manifest(self) -> dict:
        if self._manifest is None:
            if self._manifest_loader is not None:
                self._manifest = self._manifest_loader()
            else:
                with self._open(MANIFEST) as handle:
                    self._manifest = json.loads(handle.read().decode("utf-8"))
        return self._manifest

    # ------------------------------------------------------------------ index
    def units(self, source: str | None = None) -> dict[str, dict]:
        out = {}
        for src, units in self.manifest()["units"].items():
            if source is None or src == source:
                for unit_id, unit in units.items():
                    out[unit_id] = unit | {"source": src}
        return out

    def shards_for(self, unit_ids: Iterable[str] | None, table: str) -> list[str]:
        units = self.units()
        wanted = units.keys() if unit_ids is None else unit_ids
        shards = set()
        for unit_id in wanted:
            if unit_id not in units:
                raise SelectError(f"unit {unit_id!r} is not in the Hub manifest")
            shards.update(s for s in units[unit_id]["shards"] if _table_of(s) == table)
        return sorted(shards)

    def _cache_file(self, shard: str) -> Path:
        rev = self._pin_revision()
        return self.cache_dir / rev / (re.sub(r"[^A-Za-z0-9_.-]+", "_", shard) + ".index.json")

    def shard_index(self, shard: str) -> list[dict]:
        if shard in self._index:
            return self._index[shard]
        cache = self._cache_file(shard)
        if cache.is_file():
            doc = json.loads(cache.read_text(encoding="utf-8"))
            if doc.get("format") == INDEX_FORMAT and doc.get("shard") == shard:
                self._index[shard] = doc["rows"]
                return doc["rows"]
        import pyarrow.parquet as pq

        started = time.time()
        with self._open(shard) as handle:
            pf = pq.ParquetFile(handle)
            names = pf.schema_arrow.names
            meta_cols = [n for n in names if n not in HEAVY]
            payload = PAYLOAD[_table_of(shard)]
            payload_idx = names.index(payload)
            rows = []
            for rg in range(pf.metadata.num_row_groups):
                table = pf.read_row_group(rg, columns=meta_cols)
                rg_meta = pf.metadata.row_group(rg)
                rg_payload_bytes = rg_meta.column(payload_idx).total_compressed_size
                for i, row in enumerate(table.to_pylist()):
                    row["_rg"], row["_row"], row["_rg_payload_bytes"] = rg, i, rg_payload_bytes
                    row["_shard"] = shard
                    rows.append(row)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"format": INDEX_FORMAT, "repo": self.repo, "revision": self._pin_revision(),
                                   "shard": shard, "rows": rows}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache)
        self.log(f"[index] {shard}: {len(rows)} rows in {time.time() - started:.1f}s")
        self._index[shard] = rows
        return rows

    # ------------------------------------------------------------------ selection
    def select(self, units: Iterable[str] | None = None, table: str = "tensors", path_glob: str | None = None,
               array_name: str | None = None, layer: int | Iterable[int] | None = None,
               location: str | None = None, phase: str | None = None, module_glob: str | None = None,
               where: Callable[[dict], bool] | None = None) -> list[dict]:
        """Metadata rows matching every given filter. ``units=None`` means every unit (indexes every shard)."""
        units = list(units) if units is not None else None
        layers = None if layer is None else ({int(layer)} if isinstance(layer, (int, np.integer)) else
                                             {int(x) for x in layer})
        unit_set = set(units) if units is not None else None
        out = []
        for shard in self.shards_for(units, table):
            for row in self.shard_index(shard):
                if unit_set is not None and row["unit_id"] not in unit_set:
                    continue
                if path_glob and not fnmatch.fnmatch(row["path"], path_glob):
                    continue
                if table == "tensors":
                    if array_name is not None and row.get("array_name") != array_name:
                        continue
                    if layers is not None and row_layer(row) not in layers:
                        continue
                    if location is not None and row.get("location") != location:
                        continue
                    if phase is not None and row.get("phase") != phase:
                        continue
                    if module_glob and not fnmatch.fnmatch(row.get("module") or "", module_glob):
                        continue
                if where is not None and not where(row):
                    continue
                out.append(row)
        return self._complete_chunks(out, table)

    def _complete_chunks(self, rows: list[dict], table: str) -> list[dict]:
        """Add the missing sibling chunks of any selected multi-row value (they may sit in other row groups/shards)."""
        need = {array_key(r) for r in rows if (r.get("chunk_count") or 1) > 1}
        if not need:
            return rows
        have = {(array_key(r), r["chunk_index"]) for r in rows}
        units = sorted({k[0] for k in need})
        for shard in self.shards_for(units, table):
            for row in self.shard_index(shard):
                k = array_key(row)
                if k in need and (k, row["chunk_index"]) not in have:
                    rows.append(row)
                    have.add((k, row["chunk_index"]))
        return rows

    @staticmethod
    def plan(rows: list[dict]) -> dict:
        groups = {(r["_shard"], r["_rg"]): r["_rg_payload_bytes"] for r in rows}
        return {"rows": len(rows), "values": len({array_key(r) for r in rows}),
                "shards": len({g[0] for g in groups}), "row_groups": len(groups),
                "transfer_bytes_estimate": int(sum(groups.values())),
                "value_bytes": int(sum(r.get("nbytes") or r.get("bytes") or 0
                                       for r in {array_key(r): r for r in rows}.values()))}

    # ------------------------------------------------------------------ fetch
    def _payloads(self, rows: list[dict]) -> Iterator[tuple[dict, bytes]]:
        import pyarrow.parquet as pq

        by_group = defaultdict(list)
        for r in rows:
            by_group[(r["_shard"], r["_rg"])].append(r)
        by_shard = defaultdict(list)
        for (shard, rg), members in by_group.items():
            by_shard[shard].append((rg, members))
        for shard in sorted(by_shard):
            payload = PAYLOAD[_table_of(shard)]
            with self._open(shard) as handle:
                pf = pq.ParquetFile(handle)
                cols = [payload] + (["text"] if payload == "content" else [])
                for rg, members in sorted(by_shard[shard], key=lambda x: x[0]):
                    table = pf.read_row_group(rg, columns=cols)
                    self.stats["row_groups_read"] += 1
                    self.stats["payload_bytes_read"] += members[0]["_rg_payload_bytes"]
                    column = table.column(payload)
                    texts = table.column("text") if "text" in cols else None
                    for r in members:
                        value = column[r["_row"]].as_py()
                        if value is None and texts is not None:
                            text = texts[r["_row"]].as_py()
                            value = None if text is None else text.encode("utf-8")
                        if value is None:
                            value = b""
                        digest = hashlib.sha256(value).hexdigest()
                        if digest != r["chunk_sha256"]:
                            raise SelectError(f"sha256 mismatch for {array_key(r)} chunk {r['chunk_index']}"
                                              f" in {shard} row group {rg}")
                        yield r, value

    def fetch_values(self, rows: list[dict]) -> Iterator[tuple[tuple, bytes, dict]]:
        """(key, joined bytes, first-chunk metadata) per value; every chunk sha256-verified, order by chunk_index."""
        rows = self._complete_chunks(list(rows), "tensors" if any("dtype" in r for r in rows) else "files")
        expected = {}
        for r in rows:
            expected[array_key(r)] = r.get("chunk_count") or 1
        pending: dict[tuple, dict[int, tuple[dict, bytes]]] = defaultdict(dict)
        for r, value in self._payloads(rows):
            k = array_key(r)
            pending[k][r["chunk_index"]] = (r, value)
            if len(pending[k]) == expected[k]:
                parts = pending.pop(k)
                ordered = [parts[i] for i in range(expected[k])]
                offset = 0
                for meta, piece in ordered:
                    if meta["chunk_offset"] != offset:
                        raise SelectError(f"chunk gap in {k}: offset {meta['chunk_offset']} != {offset}")
                    offset += len(piece)
                yield k, b"".join(p for _, p in ordered), ordered[0][0]
        if pending:
            raise SelectError(f"incomplete values (missing chunks): {sorted(pending)[:3]}")

    def fetch_arrays(self, rows: list[dict]) -> Iterator[tuple[tuple, np.ndarray, dict]]:
        for k, data, meta in self.fetch_values(rows):
            dtype, shape = np.dtype(meta["dtype"]), tuple(meta["shape"])
            array = np.frombuffer(data, dtype=dtype)
            if array.size != int(np.prod(shape)):
                raise SelectError(f"{k}: {array.size} elements, shape {shape}")
            yield k, array.reshape(shape), meta


# ---------------------------------------------------------------------- CLI
def _filters(args) -> dict:
    return {"units": args.unit or None, "table": args.table, "path_glob": args.path_glob,
            "array_name": args.array_name, "layer": args.layer, "location": args.location, "phase": args.phase,
            "module_glob": args.module_glob}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--revision", help="Hub commit to read (default: current main, pinned for the whole run)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("units", help="list units in the manifest")
    for name in ("ls", "get"):
        p = sub.add_parser(name)
        p.add_argument("--unit", action="append", help="unit_id (repeatable); omit to scan every unit")
        p.add_argument("--table", choices=("tensors", "files"), default="tensors")
        p.add_argument("--path-glob")
        p.add_argument("--array-name")
        p.add_argument("--layer", type=int, action="append")
        p.add_argument("--location")
        p.add_argument("--phase")
        p.add_argument("--module-glob")
        if name == "ls":
            p.add_argument("--limit", type=int, default=50)
        else:
            p.add_argument("--out", type=Path)
            p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    sel = HubSelector(args.repo, args.revision, args.cache_dir, log=lambda m: print(m, file=sys.stderr))

    if args.command == "units":
        for unit_id, unit in sorted(sel.units().items()):
            print(f"{unit['source']}\t{unit_id}\trows={unit['rows']}\tbytes={unit['signature']['bytes']}")
        return 0
    if not args.unit:
        print("[warn] no --unit: indexing every shard of the table (one-time, cached)", file=sys.stderr)
    rows = sel.select(**_filters(args))
    plan = sel.plan(rows)
    print(f"[plan] revision={sel.revision} " + json.dumps(plan), file=sys.stderr)
    if args.command == "ls":
        keys = ("unit_id", "path", "array_name", "dtype", "shape", "layer_index", "location", "phase",
                "chunk_index", "chunk_count") if args.table == "tensors" else ("unit_id", "path", "bytes", "chunk_index")
        for r in rows[:args.limit]:
            print("\t".join(str(r.get(k)) for k in keys))
        if len(rows) > args.limit:
            print(f"... {len(rows) - args.limit} more", file=sys.stderr)
        return 0
    if args.dry_run:
        return 0
    if not args.out:
        parser.error("get needs --out (or --dry-run)")
    args.out.mkdir(parents=True, exist_ok=True)
    record = {"repo": args.repo, "revision": sel.revision, "filters": {k: v for k, v in _filters(args).items() if v},
              "plan": plan, "values": [], "started_at": time.time()}
    if args.table == "tensors":
        for k, array, meta in sel.fetch_arrays(rows):
            name = _safe("__".join(str(x) for x in k if x)) + ".npy"
            np.save(args.out / name, array)
            record["values"].append({"file": name, "unit_id": k[0], "path": k[1], "array_name": k[2],
                                     "dtype": meta["dtype"], "shape": meta["shape"],
                                     "layer_index": meta.get("layer_index"), "location": meta.get("location")})
    else:
        for k, data, meta in sel.fetch_values(rows):
            name = _safe("__".join(str(x) for x in k if x))
            (args.out / name).write_bytes(data)
            record["values"].append({"file": name, "unit_id": k[0], "path": k[1], "bytes": len(data)})
    record["stats"], record["finished_at"] = sel.stats, time.time()
    (args.out / "selection.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] {len(record['values'])} values -> {args.out} ({sel.stats})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
