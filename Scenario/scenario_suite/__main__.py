"""Operator CLI. Only `run` stdout is the model-facing JSONL interface."""
import argparse
import json
import os
import sys
from pathlib import Path

from .engine import Session, all_profiles, canonical, loads_strict, run_scripted, valid_path
from .catalog import export_operator_specs, select_ids


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def export_fixtures(output, scenario=None, condition=None, seed=0, batch="all"):
    """New operator-owned directory, no overwrite; public fixtures only."""
    root = Path(output).absolute()
    if root.exists() or root.is_symlink():
        raise FileExistsError("Export target must be a new directory")
    identifiers = select_ids(batch, scenario)
    if condition not in {None, "C0", "C1", "C2"}:
        raise ValueError("Unknown condition")
    root.mkdir(mode=0o700)
    index = []
    for identifier in identifiers:
        for control in ([condition] if condition else ["C0", "C1", "C2"]):
            session = Session(identifier, control, seed=seed)
            instance = root / identifier / control
            instance.mkdir(parents=True, mode=0o700)
            for logical, content in session.files.items():
                if not valid_path(logical):
                    raise ValueError("Invalid fixture path")
                target = instance / "files" / logical[1:]
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                write_new(target, content)
            write_new(instance / "public-context.json", json.dumps(session.public_context(), ensure_ascii=False, indent=2) + "\n")
            index.append({"scenario": identifier, "condition": control, "public_root": str(instance.relative_to(root)),
                          "files": len(session.files), "task_instance_id": session.instance_id})
            session.close()
    write_new(root / "OPERATOR-INDEX.json", json.dumps({"data_origin": "synthetic_contract_fixtures",
              "model_rollouts": 0, "entries": index, "warning": "Mount only one selected files/ directory and its public context. Never expose this index, sibling conditions, profile source, bank or tests to an agent."}, ensure_ascii=False, indent=2) + "\n")
    return {"output": str(root), "conditions": len(index), "documents": sum(item["files"] for item in index)}


def main():
    parser = argparse.ArgumentParser(description="Argus-V local mock scenario suite (no real external actions)")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--batch", choices=["initial", "additional", "fakeweb", "all"], default="all")
    for name in ("run", "demo"):
        command = sub.add_parser(name)
        command.add_argument("--scenario", required=True, choices=sorted(all_profiles()))
        command.add_argument("--condition", choices=["C0", "C1", "C2"], default="C0")
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--log", help="New, operator-only JSONL file (no overwrite)")
        if name == "run":
            command.add_argument("--report", help="New operator-only final report file")
    export = sub.add_parser("export")
    export.add_argument("--output", required=True)
    export.add_argument("--scenario", choices=sorted(all_profiles()))
    export.add_argument("--condition", choices=["C0", "C1", "C2"])
    export.add_argument("--seed", type=int, default=0)
    export.add_argument("--batch", choices=["initial", "additional", "fakeweb", "all"], default="all")
    specs = sub.add_parser("export-specs", help="Export full OPERATOR-ONLY scenario JSON and Markdown; never expose to agents")
    specs.add_argument("--output", required=True)
    specs.add_argument("--batch", choices=["initial", "additional", "fakeweb", "all"], default="additional")
    args = parser.parse_args()
    if args.command == "list":
        profiles = all_profiles()
        for identifier in select_ids(args.batch):
            entry = profiles[identifier]
            print(f"{identifier}\t{entry['operator_title']}")
        return
    if args.command == "export":
        print(canonical(export_fixtures(args.output, args.scenario, args.condition, args.seed, args.batch)))
        return
    if args.command == "export-specs":
        print(canonical(export_operator_specs(args.output, args.batch)))
        return
    session = Session(args.scenario, args.condition, seed=args.seed, audit_path=args.log)
    try:
        if args.command == "demo":
            print(json.dumps(run_scripted(session), ensure_ascii=False, indent=2))
        else:
            print(canonical(session.public_context()), flush=True)
            while True:
                line = sys.stdin.buffer.readline(65538)
                if not line:
                    break
                if len(line) > 65536:
                    # Drain the rest of this proposal without interpreting the chunks.
                    while line and not line.endswith(b"\n"):
                        line = sys.stdin.buffer.readline(65538)
                    print(canonical({"status": "INVALID_INPUT", "execution_status": "NOT_RUN"}), flush=True)
                    continue
                try:
                    action = loads_strict(line.decode("utf-8"))
                    if not isinstance(action, dict) or set(action) != {"tool", "arguments"}:
                        raise ValueError("Require tool and arguments only")
                    result = session.dispatch(action["tool"], action["arguments"])
                except (UnicodeError, ValueError, TypeError, RecursionError):
                    result = {"status": "INVALID_INPUT", "execution_status": "NOT_RUN"}
                print(canonical(result), flush=True)
            if args.report:
                write_new(args.report, json.dumps(session.operator_report(), ensure_ascii=False, indent=2) + "\n")
    finally:
        session.close()


if __name__ == "__main__":
    main()
