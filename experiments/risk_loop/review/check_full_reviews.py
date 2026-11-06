"""Coverage/format check of full-01 pass-1 reviews, plus a running tally (does not unblind beyond counts).

  .venv/bin/python experiments/risk_loop/review/check_full_reviews.py artifacts/label-review-v2/full-01 [r1]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    base = Path(sys.argv[1])
    rid = sys.argv[2] if len(sys.argv) > 2 else "r1"
    done, bad, n_traj, n_act, n_rep = [], [], 0, 0, 0
    for b in sorted((base / "bundles").glob("S*.json")):
        f = base / "reviews" / rid / b.name
        if not f.exists():
            continue
        bundle = json.loads(b.read_text())
        want = {t["traj"]: len(t["steps"]) for t in bundle["trajectories"]}
        try:
            rv = json.loads(f.read_text())
            got = {t["traj"]: t for t in rv["trajectories"]}
        except Exception as e:  # noqa: BLE001
            bad.append(f"{b.stem}: unreadable {e}")
            continue
        miss = set(want) - set(got)
        wrong = [q for q in want if q in got and int(got[q].get("steps_checked", -1)) != want[q]]
        oob = [(q, fl.get("step_index")) for q, t in got.items() for fl in t.get("flags", [])
               if q in want and not (0 <= int(fl.get("step_index", -1)) < want[q])]
        if miss or wrong or oob:
            bad.append(f"{b.stem}: missing {sorted(miss)[:5]} ({len(miss)}), steps_checked mismatch {wrong[:5]} ({len(wrong)}), bad step idx {oob[:3]}")
        done.append(b.stem)
        n_traj += len(got)
        for t in got.values():
            for fl in t.get("flags", []):
                n_act += int(fl.get("action_violation") or 0)
                n_rep += int(fl.get("report_violation") or 0)
    total = len(list((base / "bundles").glob("S*.json")))
    print(json.dumps({"reviewer": rid, "bundles_done": f"{len(done)}/{total}", "trajectories": n_traj,
                      "action_flags": n_act, "report_flags": n_rep, "problems": bad}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
