"""Run iterations N.. in TASK §5.2 order until done or a non-pass appears (K role automation).

python3 v2-generation/tools/loop.py START [MAX_ITER]
Review verdicts come from the independent R agents (REVIEWS dir: one JSON per ID); first-round history from ROUND1.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import iterate  # noqa: E402

REVIEWS = Path("/tmp/argus_v2/reviews")
ROUND1 = Path("/tmp/argus_v2/reviews_round1")


def reviews_for(ids, number):
    out = {}
    for identifier in ids:
        review = json.loads((REVIEWS / f"{identifier}.json").read_text(encoding="utf-8"))
        first = ROUND1 / f"{identifier}.json"
        if first.exists():
            old = json.loads(first.read_text(encoding="utf-8"))
            bad = [key for key in ("8", "9") if old[key][0] == "N"] + [key for key, value in old.get("override", {}).items()
                                                                     if value[0] == "N"]
            if bad:
                review["history"] = (f"1차 독립 검토에서 항목 {', '.join(sorted(set(bad)))} N("
                                     + " / ".join(old[key][1] for key in ("8", "9") if old[key][0] == "N")[:300]
                                     + ") → G 수정(같은 반복 안 수정 1회) 후 별도 검토자가 재검토")
        out[identifier] = review
    path = iterate.folder(number) / "reviews.input.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def main(argv):
    number = int(argv[0])
    last = int(argv[1]) if len(argv) > 1 else 60
    while number <= last:
        choice = iterate.select()
        if choice is None:
            print("nothing left to select")
            return 0
        ids = choice["revise"] + choice["new"]
        iterate.write_plan(number, choice["family"], ids,
                           f"원장 기준 갭 {choice['gap']}. 새 시나리오 {len(choice['new'])}개, revise {len(choice['revise'])}개.")
        results = iterate.check(number, ids)
        iterate.write_review(number, reviews_for(ids, number))
        ok = iterate.close(number)
        statuses = {row["id"]: row["status"] for row in iterate.ledger.rows() if row.get("iteration") == number}
        print(f"iter-{number:02d} {choice['family']} {statuses} checks={results} regression_ok={ok}", flush=True)
        if not ok or any(status != "pilot_queued" for status in statuses.values()):
            print("stopping for manual handling")
            return 1
        number += 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
