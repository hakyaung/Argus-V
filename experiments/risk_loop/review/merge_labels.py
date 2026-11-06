"""Merge pilot-01 (+bashfix) and full-01 (r1, r2) reviews into a step-level review-label DRAFT for v2 train/val.

Per step:
  review_action  1/0/'disputed'  pilot: rA&rB consensus (bashfix overrides its 11 packets); full: r1&r2 consensus
                 where pass 2 exists, else r1 (only trajectories r1 found clean and the rule found clean)
  action_kind    substantive | procedure_skip | output_falsification ('' when review_action != 1)
  review_report  1/0/'disputed'
Writes <out>/step_labels.csv, human_queue.json and summary.json. Status DRAFT: not gold until a human confirms.

  .venv/bin/python experiments/risk_loop/review/merge_labels.py --out artifacts/label-review-v2/labels-draft-v1
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LR = ROOT / "artifacts/label-review-v2"
SUBST = {"target_scope", "authorization_bypass", "denial_persistence", "forbidden_path_or_data", "exfiltration_channel", "other"}


def kind_of(types):
    t = {x for x in types if x}
    if t & SUBST:
        return "substantive"
    if "procedure_skip" in t:
        return "procedure_skip"
    if "false_claim" in t:
        return "output_falsification"
    return "substantive" if not t else "other"


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else None


def flags_by_step(traj_review):
    return {int(f["step_index"]): f for f in traj_review.get("flags", [])}


def steps_pilot(review_file):
    d = json.loads(review_file.read_text())
    return {int(s["step_index"]): s for s in d["steps"]}


def rule_info(rollout):
    d = json.loads((ROOT / "artifacts/rollouts-v2" / rollout / "rollout.json").read_text())
    tool_steps = [i for i, s in enumerate(d["steps"]) if s.get("kind") == "tool"]
    al = (d.get("operator_report") or {}).get("action_labels") or []
    rt = {i: a.get("violation_type") for i, a in zip(tool_steps, al)}
    return [(i, s.get("kind"), int(s.get("policy_label") or 0), rt.get(i)) for i, s in enumerate(d["steps"])]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows, queue = [], []
    agree_full, agree_rep = [], []

    def emit(rollout, split, fam, source, per_step):
        for i, kind, rule, rtype in rule_info(rollout):
            a, b = per_step(i)  # each: dict with action_violation/report_violation/type/confidence/evidence or None
            ra = int((a or {}).get("action_violation") or 0)
            rr = int((a or {}).get("report_violation") or 0)
            if b is None:
                act, rep, types = ra, rr, [(a or {}).get("type")]
            else:
                ba, br = int(b.get("action_violation") or 0), int(b.get("report_violation") or 0)
                act = ra if ra == ba else "disputed"
                rep = rr if rr == br else "disputed"
                types = [(a or {}).get("type"), b.get("type")]
            confs = [x.get("confidence") for x in (a, b) if x and x.get("confidence") in ("high", "medium", "low")]
            row = {"rollout": rollout, "step_index": i, "split": split, "family_id": fam, "kind": kind,
                   "rule_label": rule, "rule_type": rtype or "", "review_action": act,
                   "action_kind": kind_of(types) if act == 1 else "", "review_report": rep,
                   "min_confidence": min(confs, key=["high", "medium", "low"].index) if any(confs) else "",
                   "source": source, "double_reviewed": int(b is not None)}
            rows.append(row)
            rule_act = int(rule == 1 and rtype != "report_content")
            if act == "disputed" or (act == 1 and row["min_confidence"] == "low") or (b is not None and act != rule_act):
                queue.append(row | {"evidence": [x.get("evidence") for x in (a, b) if x and
                                                 (x.get("action_violation") or x.get("report_violation"))]})

    # pilot
    sel = json.loads((LR / "pilot-01/selection.json").read_text())["items"]
    bashfix = {p.stem for p in (LR / "pilot-01-bashfix/reviews/rA").glob("P*.json")}
    for it in sel:
        pid = it["packet_id"]
        base = LR / ("pilot-01-bashfix" if pid in bashfix else "pilot-01") / "reviews"
        A, B = steps_pilot(base / "rA" / f"{pid}.json"), steps_pilot(base / "rB" / f"{pid}.json")
        emit(it["rollout"], it["split"], it["family_id"], "pilot", lambda i, A=A, B=B: (A.get(i), B.get(i)))
    # full
    full = LR / "full-01"
    mapping = json.loads((full / "mapping.json").read_text())["trajectories"]
    r1, r2 = {}, {}
    for d, store in ((full / "reviews/r1", r1), (full / "pass2/reviews/r2", r2)):
        for f in d.glob("S*.json"):
            for t in json.loads(f.read_text())["trajectories"]:
                store[t["traj"]] = t
    p2sel = json.loads((full / "pass2/selection.json").read_text())
    missing2 = [q for q in p2sel if q not in r2]
    zero = {"action_violation": 0, "report_violation": 0}
    for q, info in mapping.items():
        f1 = flags_by_step(r1[q])
        if q in r2:
            f2 = flags_by_step(r2[q])
            for i, *_ in rule_info(info["rollout"]):
                a, b = f1.get(i, zero), f2.get(i, zero)
                agree_full.append((int(a.get("action_violation") or 0), int(b.get("action_violation") or 0)))
                agree_rep.append((int(a.get("report_violation") or 0), int(b.get("report_violation") or 0)))
            emit(info["rollout"], info["split"], info["family_id"], f"full:{p2sel[q]}",
                 lambda i, f1=f1, f2=f2: (f1.get(i, zero), f2.get(i, zero)))
        else:
            emit(info["rollout"], info["split"], info["family_id"], "full:r1_only", lambda i, f1=f1: (f1.get(i, zero), None))
    # r1 false-negative rate on the random pass-2 sample
    fn = [q for q, s in p2sel.items() if s == "C_random" and q in r2]
    fn_hits = [q for q in fn if any(int(x.get("action_violation") or 0) for x in r2[q].get("flags", []))]
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "step_labels.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    (args.out / "human_queue.json").write_text(json.dumps(queue, ensure_ascii=False, indent=1))
    pos = [r for r in rows if r["review_action"] == 1]
    summ = {
        "status": "DRAFT_LLM_REVIEW_NOT_GOLD", "steps": len(rows), "trajectories": len({r["rollout"] for r in rows}),
        "pass2_missing": len(missing2),
        "pass2_step_agreement": {"action_kappa": kappa(agree_full) if agree_full else None,
                                 "report_kappa": kappa(agree_rep) if agree_rep else None, "steps": len(agree_full)},
        "r1_false_negative_traj_rate_on_random10pct": (len(fn_hits) / len(fn)) if fn else None,
        "r1_false_negative_examples": fn_hits[:20],
        "review_action": dict(Counter(str(r["review_action"]) for r in rows)),
        "action_positive_by_kind_split": dict(Counter(f"{r['action_kind']}|{r['split']}" for r in pos)),
        "action_positive_traj_by_split": dict(Counter(r["split"] for r in {(x["rollout"], x["split"]): x for x in pos}.values())),
        "substantive_positive_families_by_split": {s: sorted({r["family_id"] for r in pos if r["action_kind"] == "substantive" and r["split"] == s})
                                                   for s in ("train", "validation")},
        "rule_vs_review_action": dict(Counter(f"rule={int(r['rule_label'] == 1 and r['rule_type'] != 'report_content')} review={r['review_action']}" for r in rows)),
        "review_report": dict(Counter(str(r["review_report"]) for r in rows)),
        "human_queue": len(queue),
    }
    (args.out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1))
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
