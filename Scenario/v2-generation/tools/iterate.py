"""K-role driver for one iteration (TASK §5.6). cwd = Scenario/.

  python3 v2-generation/tools/iterate.py select                     # next family + IDs by TASK §5.2
  python3 v2-generation/tools/iterate.py check  N ID,ID,...          # plan.md + lint/validate/diversity reports
  python3 v2-generation/tools/iterate.py review N reviews.json       # review.md (precheck 1-7 + reviewer 8-9)
  python3 v2-generation/tools/iterate.py close  N                    # ledger, export(+check), regression, LEDGER.md

Drafts may already exist (authored in advance per family); "created" for the selection rule counts only
scenarios already recorded in ledger.jsonl, so families are still visited in the TASK §5.2 order.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIO = HERE.parents[1]
REPO = SCENARIO.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCENARIO))
from scenario_suite.v2 import export, ledger  # noqa: E402
from scenario_suite.v2.registry import FAMILIES, load_all  # noqa: E402
import review_precheck  # noqa: E402

VALIDATION = ("F02", "F05", "F09", "F13")
LIVE = ("draft", "lint_passed", "replay_passed", "pilot_queued", "revise", "pilot_passed", "collected")
ITER = SCENARIO / "v2-generation" / "iterations"


def folder(number):
    path = ITER / f"iter-{int(number):02d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def select(specs=None):
    specs = specs or load_all()
    latest = ledger.latest_rows()
    best = None
    for family, (_, target, _, _, _) in FAMILIES.items():
        recorded = [row for row in latest.values() if row["family_id"] == family]
        created = sum(1 for row in recorded if row["status"] in LIVE)
        revise = sorted(row["id"] for row in recorded if row["status"] == "revise")
        gap = target - created
        if gap <= 0 and not revise:
            continue
        pending = sorted((spec for key, spec in specs.items() if spec["family_id"] == family and key not in latest
                          and spec.get("status") == "draft"),
                         key=lambda spec: (spec["provenance"].get("family_index", 99), spec["id"]))
        new = [spec["id"] for spec in pending][:max(0, min(4, gap))]
        if not new and not revise:
            continue
        key = (-gap, 0 if family in VALIDATION else 1, family)
        if best is None or key < best[0]:
            best = (key, family, gap, new, revise)
    if best is None:
        return None
    _, family, gap, new, revise = best
    return {"family": family, "gap": gap, "new": new, "revise": revise}


def write_plan(number, family, ids, note=""):
    specs = load_all()
    table, _ = ledger.family_table(specs)
    row = next(item for item in table if item["family"] == family) if family != "ALL" else None
    lines = [f"# iter-{int(number):02d} 계획", "", f"계열: {family}", ""]
    if row:
        lines += [f"선택 근거(TASK §5.2): 원장 기준 갭 = 목표 {row['target']} − 기록된 진행·통과 수. 이번 반복 새 시나리오 최대 4개 + 같은 계열 revise.", ""]
    if note:
        lines += [note, ""]
    lines += ["| ID | i | 도메인 | base P/W | 변형 P/W | 지름길 인자 | 쌍둥이 필드 | 참고 v1 | 예상 위험 |", "|---|---|---|---|---|---|---|---|---|"]
    from scenario_suite.v2.diversity import shortcut_args
    for identifier in ids:
        spec = specs[identifier]
        cell6 = next(entry for entry in spec["collection"]["matrix"] if entry["cell"] == 6)
        variant = f"{cell6['P']}/{cell6['W']}" if cell6["P"] != spec["decision_point"]["placement"] else "없음(cell6=D-L2-base)"
        sources = ", ".join(spec.get("supersedes_v1", [])) or "; ".join(spec["provenance"].get("design_sources", []))[:60]
        risk = "D-1/D-5 교차 검사, V-10b 길이 균형" if spec["decision_point"]["placement"] != "late" else "late prelude 6+, max_steps 14"
        lines.append(f"| {identifier} | {spec['provenance'].get('family_index')} | {spec['domain']} | "
                     f"{spec['decision_point']['placement']}/{spec['collection']['length_band']} | {variant} | "
                     f"{', '.join(sorted(shortcut_args(spec)))} | {spec['conditions']['X']['twin_delta']['field']} | {sources} | {risk} |")
    (folder(number) / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(command, cwd=SCENARIO, env=None):
    return subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, env=env)


def check(number, ids):
    target = folder(number)
    joined = ",".join(ids)
    results = {}
    for name in ("lint", "validate", "diversity"):
        done = run(f"python3 -m scenario_suite.v2 {name} --ids {joined} --report {target / (name + '.json')}")
        results[name] = done.returncode == 0
        print(done.stdout.strip())
    return results


def write_review(number, reviews_path):
    """review.md: machine table (TASK §5.5 checklist 1-9) + one-line reasons. Items 1-7 from the precheck unless the
    reviewer overrides them; 8-9 from the reviewer (who saw only `public` output and the JSON)."""
    reviews = json.loads(Path(reviews_path).read_text(encoding="utf-8"))
    lines = [f"# iter-{int(number):02d} R 검토", "",
             "검토 근거: `python3 -m scenario_suite.v2 public --scenario <ID> --cell pilotD` 출력과 시나리오 JSON만 사용했다(생성 메모 미사용). "
             "항목 1~7은 `tools/review_precheck.py`의 기계 근거를 검토자가 확인했고, 8(현실성·비강압)·9(계열 메커니즘)는 검토자 판단이다.", "",
             "| ID | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 근거 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    details = []
    for identifier, review in reviews.items():
        marks = review_precheck.precheck(load_all()[identifier])
        for key, value in review.get("override", {}).items():
            marks[key] = value
        marks["8"], marks["9"] = review["8"], review["9"]
        row = " | ".join(marks[str(index)][0] for index in range(1, 10))
        lines.append(f"| {identifier} | {row} | {review.get('summary', '')} |")
        details.append(f"### {identifier}")
        details += [f"- {index}. {marks[str(index)][0]} — {marks[str(index)][1]}" for index in range(1, 10)]
        if review.get("history"):
            details.append(f"- 이력: {review['history']}")
        if review.get("warnings"):
            details.append(f"- 경고: {review['warnings']}")
        details.append("")
    (folder(number) / "review.md").write_text("\n".join(lines + ["", "## 항목별 근거", ""] + details) + "\n", encoding="utf-8")


def regression():
    parts, ok = [], True
    done = run("python3 -m unittest discover -s tests")
    tail = done.stderr.strip().splitlines()
    count = next((line for line in tail if line.startswith("Ran ")), "?")
    unittest_ok = done.returncode == 0 and tail and tail[-1].startswith("OK")
    ok &= bool(unittest_ok)
    parts.append(f"unittest {count.replace('Ran ', '').split(' in ')[0]} {'OK' if unittest_ok else 'FAIL'}")
    finger = run("python3 v2-generation/tools/v1_fingerprint.py | diff - v2-generation/baseline/v1-fingerprint.json")
    ok &= finger.returncode == 0 and not finger.stdout.strip()
    parts.append("v1 지문 동일" if finger.returncode == 0 and not finger.stdout.strip() else "v1 지문 DIFF")
    bridge = run(".venv/bin/python -m pytest tests/test_scenario_suite_bridge.py tests/test_scenario_suite_bridge_v2.py -q", cwd=REPO)
    failed = re.findall(r"FAILED (\S+)", bridge.stdout)
    summary = bridge.stdout.strip().splitlines()[-1] if bridge.stdout.strip() else "?"
    known = "tests/test_scenario_suite_bridge.py::test_plan_all_reruns_finished_and_brief_names_the_task"
    bridge_ok = failed == [known]
    ok &= bridge_ok
    parts.append(f"브리지 pytest {summary.split(' in ')[0]}" + ("(기존 실패 1건만)" if bridge_ok else f" 새 실패 {failed}"))
    plan = run("PYTHONPATH=src .venv/bin/python argus_hook/scenario_rollout.py plan --harness v2 --scenarios suite-v2 --seeds 1 "
               "--output artifacts/rollouts-v2 --dry-run", cwd=REPO)
    first = plan.stdout.strip().splitlines()[0] if plan.stdout.strip() else plan.stderr.strip()[-200:]
    ok &= plan.returncode == 0
    parts.append(f"plan dry-run '{first}'")
    return ("PASS: " if ok else "FAIL: ") + " · ".join(parts), ok


def close(number):
    appended = ledger.record_iteration(int(number), folder(number))
    for row in appended:
        print(row["id"], row["status"], (row["failures"] or ["-"])[0])
    problems = export.export()
    print("export --check:", "PASS" if not problems else problems)
    line, ok = regression()
    (folder(number) / "regression.txt").write_text(line + "\nexport --check: " + ("PASS" if not problems else "; ".join(problems))
                                                   + f"\n기록 시각: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
                                                   encoding="utf-8")
    meta = ledger.load_meta()
    meta.setdefault("iterations", {}).setdefault(str(int(number)), {})["regression"] = line
    ledger.save_meta(meta)
    ledger.render()
    print(line)
    return ok and not problems


def main(argv):
    command = argv[0]
    if command == "select":
        print(json.dumps(select(), ensure_ascii=False))
    elif command == "plan":
        write_plan(argv[1], argv[2], argv[3].split(","), argv[4] if len(argv) > 4 else "")
    elif command == "check":
        print(check(argv[1], argv[2].split(",")))
    elif command == "review":
        write_review(argv[1], argv[2])
    elif command == "close":
        sys.exit(0 if close(argv[1]) else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
