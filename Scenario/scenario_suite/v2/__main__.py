"""CLI: cd Scenario && python3 -m scenario_suite.v2 <command> (TASK P0-5)."""
import argparse
import json
import sys
from pathlib import Path

from . import diversity, export, ledger, lint, replay
from .materialize import CELLS
from .registry import DOMAINS, FAMILIES, list_ids, load_all, load_spec, spec_path, write_spec


def _ids(args, specs):
    if getattr(args, "all", False):
        return sorted(key for key, spec in specs.items() if spec.get("status") != "retired")
    if getattr(args, "family", None):
        return sorted(key for key, spec in specs.items() if spec["family_id"] == args.family
                      and spec.get("status") != "retired")
    return [item.strip() for item in (args.ids or "").split(",") if item.strip()]


def _write_report(path, report):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # A-9/A-10: keep earlier attempts of the same iteration
        attempt = 1
        while path.with_name(f"{path.stem}.attempt-{attempt}{path.suffix}").exists():
            attempt += 1
        path.rename(path.with_name(f"{path.stem}.attempt-{attempt}{path.suffix}"))
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _summary(report):
    ok = True
    for key, gates in report.items():
        failed = [gate for gate, item in gates.items() if item.get("result") != "PASS"]
        ok = ok and not failed
        print(f"{key}\t{'PASS' if not failed else 'FAIL ' + ','.join(failed)}")
        for gate in failed:
            for text in gates[gate].get("failures", [])[:4]:
                print(f"    {gate}: {text}")
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scenario_suite.v2", description="v2 scenario generation tools (operator-only)")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.add_argument("--check-acceptance", action="store_true")
    new = sub.add_parser("new")
    new.add_argument("--family", required=True, choices=sorted(FAMILIES))
    new.add_argument("--domain", required=True, choices=DOMAINS)
    for name in ("lint", "validate", "diversity"):
        command = sub.add_parser(name)
        command.add_argument("--ids")
        command.add_argument("--family", choices=sorted(FAMILIES))
        command.add_argument("--all", action="store_true")
        command.add_argument("--report")
        if name == "validate":
            command.add_argument("--no-tokens", action="store_true", help="debug only: skip V-10 token lengths (FAIL)")
    for name in ("demo", "public"):
        command = sub.add_parser(name)
        command.add_argument("--scenario", required=True)
        command.add_argument("--cell", required=True, choices=list(CELLS))
    record = sub.add_parser("ledger")
    record.add_argument("--iteration", type=int)
    record.add_argument("--from", dest="source")
    record.add_argument("--force", action="store_true")
    record.add_argument("--render", action="store_true", help="only regenerate LEDGER.md")
    out = sub.add_parser("export")
    out.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "status":
        report = {}
        if args.check_acceptance:
            report = diversity.check()
        problems, table, total = ledger.acceptance(diversity_report=report if args.check_acceptance else None)
        if args.json:
            print(json.dumps({"total_pilot_queued": total, "families": table, "acceptance_problems": problems},
                             ensure_ascii=False, indent=1))
        else:
            print(f"pilot_queued total {total}")
            print("family  target min  queued prog ret gap  domains            P(e/m/l)  var  shortcut-args          done")
            for row in table:
                placements = "/".join(str(row["placements"][key]) for key in ("early", "mid", "late"))
                print(f"{row['family']}     {row['target']:>3} {row['minimum']:>3}  {row['accepted']:>5} {row['in_progress']:>4} "
                      f"{row['retired']:>3} {row['gap']:>3}  {ledger._fmt(row['domains']):<18} {placements:<9} "
                      f"{row['variant_ratio']:<4} {','.join(row['shortcut_kinds']):<22} {'Y' if row['done'] else 'N'}")
            if args.check_acceptance:
                print("acceptance:", "PASS" if not problems else "FAIL")
                for item in problems:
                    print("  -", item)
        return 0 if not args.check_acceptance or not problems else 1
    if args.command == "new":
        spec, axes = ledger.skeleton(args.family, args.domain)
        path = write_spec(spec)
        print(json.dumps({"id": spec["id"], "path": str(path), "axes": axes,
                          "object_id_candidates": ledger.object_ids()}, ensure_ascii=False, indent=1))
        return 0
    if args.command in ("lint", "validate", "diversity"):
        specs = load_all()
        ids = _ids(args, specs)
        if args.command == "diversity":
            report = diversity.check(ids, specs)
            _write_report(args.report, report)
            for key, item in report.items():
                print(f"{key}\t{item['result']}\tmax_jaccard {item['max_jaccard']} ({item['nearest']})")
                for text in item["failures"] + [f"warning: {w}" for w in item["warnings"]]:
                    print(f"    {text}")
            return 0 if all(item["result"] == "PASS" for item in report.values()) else 1
        report = {}
        for key in ids:
            spec = specs[key]
            if args.command == "lint":
                report[key] = lint.lint(spec, spec_path(key))
            else:
                report[key] = replay.validate(spec, measure_tokens=not args.no_tokens)
                if args.no_tokens:
                    report[key]["V-10"]["result"] = "FAIL"
                    report[key]["V-10"]["failures"].append("token measurement skipped (--no-tokens)")
        _write_report(args.report, report)
        return 0 if _summary(report) else 1
    if args.command in ("demo", "public"):
        spec = load_spec(args.scenario)
        session = replay.open_cell(spec, args.cell)
        if args.command == "public":
            context = session.public_context()
            print(json.dumps({"public_context": context,
                              "files": {path: text for path, text in sorted(session.files.items())}},
                             ensure_ascii=False, indent=1))
            return 0
        from .materialize import plan_for, resolve_cell
        axes = resolve_cell(spec, args.cell)
        trace, _ = replay.run_actions(session, plan_for(spec, axes["P"], axes["O"]))
        print(json.dumps({"cell": axes, "trace": trace, "operator_report": session.operator_report()},
                         ensure_ascii=False, indent=1))
        return 0
    if args.command == "ledger":
        if args.render:
            ledger.render()
            return 0
        appended = ledger.record_iteration(args.iteration, args.source, force=args.force)
        for row in appended:
            print(f"{row['id']}\t{row['status']}\t{(row['failures'] or ['-'])[0]}")
        return 0
    if args.command == "export":
        problems = export.check() if args.check else export.export()
        for item in problems:
            print("-", item)
        print("export check:", "PASS" if not problems else "FAIL")
        return 0 if not problems else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
