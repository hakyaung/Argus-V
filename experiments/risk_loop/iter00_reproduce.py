"""Iteration 0 / A4: reproduce the TASK.md §1 starting point through the frozen harness only.

Candidates (no tuning on validation; settings fixed in advance from TASK.md §1 / seed scripts):
  C-00a  length+step baseline (harness built-in logistic on log(prefix_length), step_index)
  C-00b  L23 pre_action row, StandardScaler + LogisticRegression(C=0.001, balanced, max_iter=5000)
  C-00c  L16 pre_action row, same model

Run from repo root:
  PYTHONPATH=src .venv/bin/python3 experiments/risk_loop/iter00_reproduce.py
Outputs: artifacts/risk-loop/iter-00/ (refuses to overwrite existing files).
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.risk_loop import harness as H  # noqa: E402

OUT = REPO / "artifacts" / "risk-loop" / "iter-00"
SEED = 0
EXPECTED = {"length_only": 0.777, "C-00b": 0.670, "C-00c": 0.713}
TOL = 0.02


def logreg_fp(X_fit, y_fit, X_score):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.001, class_weight="balanced", max_iter=5000))
    m.fit(X_fit, y_fit)
    return m.decision_function(X_score)


def length_step_fp(X_fit, y_fit, X_score):
    # same form as harness._logreg_scores (baseline definition)
    return H._logreg_scores(X_fit, y_fit, X_score)


def length_step_X(ds, rows):
    return np.c_[np.log(ds.prefix_length(rows)), ds.step_index(rows)]


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_new(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.write_text(text)


def provenance():
    def run(cmd):
        try:
            return subprocess.run(cmd, cwd=REPO, capture_output=True, check=True).stdout
        except Exception:
            return b""
    import sklearn
    ds_root = H.DATASETS_DIR / "D-v1"
    return {
        "git_commit": run(["git", "rev-parse", "HEAD"]).decode().strip(),
        "uncommitted_diff_sha256": hashlib.sha256(run(["git", "diff", "HEAD"])).hexdigest(),
        "dataset": "D-v1",
        "dataset_hash_txt_sha256": H.file_sha256(ds_root / "hash.txt"),
        "samples_csv_sha256": H.file_sha256(ds_root / "samples.csv"),
        "harness_sha256": H.harness_sha256(),
        "script_sha256": H.file_sha256(Path(__file__)),
        "seed": SEED,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "sklearn": sklearn.__version__,
    }


def cv_block(fp, X, ds, rows):
    r = H.grouped_cv(fp, X, ds.y(rows), ds.groups(rows), n_splits=5,
                     strata=ds.prefix_length(rows), dup_groups=ds.dup_groups(rows))
    r = dict(r)
    r.pop("oof")
    r["n"] = len(rows)
    r["positives"] = int(ds.y(rows).sum())
    return r


def main():
    for p in [OUT / "cv_report.json", OUT / "summary.json"]:
        if p.exists():
            raise FileExistsError(f"refusing to overwrite {p}")
    ds = H.load("D-v1")
    tr, va = list(ds.train_idx), list(ds.val_idx)
    tr_dd = ds.dedup(tr)
    prov = provenance()

    cands = {
        "C-00a": {"desc": "length+step baseline (logistic, StandardScaler, max_iter=1000)",
                  "inputs": ["prefix_length", "step_index"], "feat": length_step_X, "fp": length_step_fp,
                  "layer": None},
        "C-00b": {"desc": "L23 pre_action row, StandardScaler + LogisticRegression(C=0.001, balanced, max_iter=5000)",
                  "inputs": ["activation:L23"], "feat": lambda d, r: d.features(23, r), "fp": logreg_fp, "layer": 23},
        "C-00c": {"desc": "L16 pre_action row, StandardScaler + LogisticRegression(C=0.001, balanced, max_iter=5000)",
                  "inputs": ["activation:L16"], "feat": lambda d, r: d.features(16, r), "fp": logreg_fp, "layer": 16},
    }

    cv_report = {"provenance": prov, "protocol": "GroupKFold(5) on family_id, dup groups kept in one fold; "
                 "strata = prefix_length tertiles of evaluated rows", "attempts": []}
    evals = {}
    for cid, c in cands.items():
        print(f"[{cid}] {c['desc']}", flush=True)
        Xtr = np.asarray(c["feat"](ds, tr))
        pos = {r: i for i, r in enumerate(tr)}
        Xdd = Xtr[[pos[r] for r in tr_dd]]
        cv_all = cv_block(c["fp"], Xtr, ds, tr)
        cv_dd = cv_block(c["fp"], Xdd, ds, tr_dd)
        print(f"  CV all auroc={cv_all['auroc']:.3f} strat={cv_all['stratified_auroc']}  "
              f"dedup auroc={cv_dd['auroc']:.3f} strat={cv_dd['stratified_auroc']}", flush=True)
        inputs_check = H.check_inputs(c["inputs"])
        cv_report["attempts"].append({"candidate": cid, "description": c["desc"], "inputs": c["inputs"],
                                      "inputs_check": inputs_check, "cv_train_all": cv_all,
                                      "cv_train_dedup": cv_dd})
        # fit on all train, score validation (one validation look per candidate)
        Xva = np.asarray(c["feat"](ds, va))
        sv = np.asarray(c["fp"](Xtr, ds.y(tr), Xva), dtype=np.float64).ravel()
        # train-fit (in-sample) AUROC, to document overfitting
        s_tr_in = np.asarray(c["fp"](Xtr, ds.y(tr), Xtr), dtype=np.float64).ravel()
        ev = H.evaluate_validation(dict(zip(va, sv.tolist())), ds, bootstrap=2000, seed=SEED)
        ev["train_in_sample_auroc"] = H.auroc(s_tr_in, ds.y(tr))
        ev["candidate"] = cid
        ev["description"] = c["desc"]
        ev["inputs"] = c["inputs"]
        ev["provenance"] = prov
        evals[cid] = ev
        write_new(OUT / "eval" / f"{cid}.json", json.dumps(jsonable(ev), indent=2))
        print(f"  VAL auroc={ev['auroc']:.3f} CI=[{ev['auroc_ci']['ci_low']:.3f},{ev['auroc_ci']['ci_high']:.3f}] "
              f"len_only={ev['baselines']['length_only']['auroc']:.3f} len+step={ev['baselines']['length+step']['auroc']:.3f} "
              f"gates={ev['gates']}", flush=True)
        cv_report["attempts"][-1]["validation_auroc"] = ev["auroc"]

    write_new(OUT / "cv_report.json", json.dumps(jsonable(cv_report), indent=2))

    # reproduction check
    len_only = evals["C-00b"]["baselines"]["length_only"]["auroc"]
    repro = {
        "length_only": {"observed": len_only, "expected": EXPECTED["length_only"]},
        "C-00b": {"observed": evals["C-00b"]["auroc"], "expected": EXPECTED["C-00b"]},
        "C-00c": {"observed": evals["C-00c"]["auroc"], "expected": EXPECTED["C-00c"]},
    }
    for v in repro.values():
        v["abs_diff"] = abs(v["observed"] - v["expected"])
        v["within_tolerance"] = v["abs_diff"] <= TOL
    repro["all_within_tolerance"] = all(v["within_tolerance"] for k, v in repro.items() if isinstance(v, dict))
    repro["tolerance"] = TOL
    summary = {"provenance": prov, "reproduction": repro, "validation_looks": len(evals),
               "candidates": {cid: {"val_auroc": e["auroc"], "val_auroc_ci": [e["auroc_ci"]["ci_low"], e["auroc_ci"]["ci_high"]],
                                    "gates": e["gates"]} for cid, e in evals.items()}}
    write_new(OUT / "summary.json", json.dumps(jsonable(summary), indent=2))

    for att in cv_report["attempts"]:
        cid = att["candidate"]
        write_new(OUT / "candidates" / cid / "card.md", card(cid, cands[cid], att, evals[cid], prov, repro))
    print(json.dumps(jsonable(repro), indent=2))


def f3(x):
    return "n/a" if x is None else f"{x:.3f}"


def ci(d):
    return f"[{f3(d.get('ci_low'))}, {f3(d.get('ci_high'))}]"


def card(cid, c, att, ev, prov, repro):
    L = [f"# {cid} — Iteration 0 reproduction (A4)", "",
         "Hypothesis: TASK.md §4 Iteration 0 step 3 (reproduce §1 starting point); no tuning on validation.", "",
         f"- Description: {c['desc']}",
         f"- Inputs: {att['inputs']} — allowlist check: {'PASS' if att['inputs_check'].get('pass') else 'FAIL'} ({att['inputs_check']})",
         f"- Layer: {c['layer']} (C-01 pre_action row at manifest token_index)" if c["layer"] is not None else "- Layer: none (metadata baseline)",
         "- Data version: D-v1 (train 1039 rows for fitting; validation 217 rows scored once)",
         f"- Seed: {prov['seed']}; git {prov['git_commit']}; diff sha256 {prov['uncommitted_diff_sha256'][:16]}; "
         f"harness sha256 {prov['harness_sha256'][:16]}; samples.csv sha256 {prov['samples_csv_sha256'][:16]}",
         f"- Libraries: python {prov['python']}, numpy {prov['numpy']}, sklearn {prov['sklearn']}", "",
         "## Train grouped CV (GroupKFold 5 on family_id, dup groups kept together)", "",
         "| subset | n | pos | AUROC | AP | length-stratified AUROC | per-fold AUROC |", "|---|---|---|---|---|---|---|"]
    for name, k in (("all", "cv_train_all"), ("dedup", "cv_train_dedup")):
        r = att[k]
        folds = ", ".join(f3(f["auroc"]) for f in r["folds"])
        L.append(f"| {name} | {r['n']} | {r['positives']} | {f3(r['auroc'])} | {f3(r['ap'])} | {f3(r['stratified_auroc'])} | {folds} |")
    b = ev["baselines"]
    L += ["", "## Validation (one look)", "",
          f"- Train in-sample AUROC: {f3(ev['train_in_sample_auroc'])}",
          f"- AUROC {f3(ev['auroc'])} {ci(ev['auroc_ci'])}; AP {f3(ev['ap'])} {ci(ev['ap_ci'])}",
          f"- Baselines: length_only {f3(b['length_only']['auroc'])} {ci(b['length_only']['auroc_ci'])}; "
          f"length+step {f3(b['length+step']['auroc'])} {ci(b['length+step']['auroc_ci'])}",
          f"- G1 delta vs length+step: {f3(ev['G1']['delta'])} {ci(ev['G1'])} -> {'PASS' if ev['G1']['pass'] else 'FAIL'}",
          f"- Delta vs length_only: {f3(ev['delta_vs_length_only']['delta'])} {ci(ev['delta_vs_length_only'])}",
          f"- G2 length-stratified AUROC: {f3(ev['G2']['stratified_auroc'])} {ci(ev['G2'])} -> {'PASS' if ev['G2']['pass'] else 'FAIL'}",
          f"- G3 dedup (n={ev['G3']['n']}, pos={ev['G3']['positives']}): cand {f3(ev['G3']['candidate_auroc'])} vs base "
          f"{f3(ev['G3']['baseline_auroc'])}, delta {f3(ev['G3']['delta'])} {ci(ev['G3'])} -> {'PASS' if ev['G3']['pass'] else 'FAIL'}",
          "", "| budget | threshold | snapshot recall | normal traj FPR | first-violation-before detection | lead (actions) |",
          "|---|---|---|---|---|---|"]
    for bm in ev["budgets"]:
        lead = f"mean {f3(bm.get('lead_actions_mean'))} / median {f3(bm.get('lead_actions_median'))}"
        L.append(f"| {bm['budget']} | {f3(bm.get('threshold'))} | {f3(bm.get('snapshot_recall'))} | "
                 f"{f3(bm.get('normal_trajectory_fpr'))} | {bm['first_violation_detected']}/{bm['violation_trajectories']} = {f3(bm.get('first_violation_detection_rate'))} | {lead} |")
    L += ["", f"- Gates: {ev['gates']} (G5 audits are A5's responsibility)", "",
          "## Per-family validation", "", "| family | n | pos | AUROC |", "|---|---|---|---|"]
    for pf in ev["per_family"]:
        L.append(f"| {pf['family_id']} | {pf['n']} | {pf['positives']} | {f3(pf['auroc'])} |")
    L += ["", "## Reproduction check (±0.02)", ""]
    for k, v in repro.items():
        if isinstance(v, dict):
            L.append(f"- {k}: observed {f3(v['observed'])} vs expected {v['expected']} (|diff| {f3(v['abs_diff'])}) -> "
                     f"{'within' if v['within_tolerance'] else 'OUTSIDE'} tolerance")
    L += ["", "## Known limitations", "",
          "- Validation has only 12 positives / 9 violation trajectories / 41 normal trajectories: CIs are wide.",
          "- Fixed hyperparameters taken from the §1 diagnosis; no train-CV selection was performed in Iteration 0.",
          "- Activation scores are strongly length-correlated (TASK.md §1); G1/G2 decide whether anything is beyond length.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    main()
