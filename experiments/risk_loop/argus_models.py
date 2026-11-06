"""Argus-V model registry. Naming rule: every structural upgrade gets the next version (Argus-V1, Argus-V2, ...).
Earlier pre-versioning candidates were deleted (2026-11-06); their results stay in LEDGER.md as history only.

Argus-V1  frozen gpt-oss-20b block-L output (residual stream) at the step's observation rows (n x 2880)
          -> element-wise MAX pooling over rows (2880)
          -> StandardScaler -> PCA-10 -> LogisticRegression(C=1, L2, class_weight=balanced, lbfgs) -> sigmoid
          Default layer L16. Trained parameters: 10 weights + 1 bias (scaler/PCA are fitted statistics).

Public API (never touches validation by itself):
  fit(name, ds, rows, layer=None) -> model dict;  score(model, ds, rows) -> np.ndarray;  cv_fit_predict(name, ds, layer)
"""
from __future__ import annotations

import csv
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.risk_loop import harness as h  # noqa: E402

D2 = ROOT / "artifacts/risk-loop/datasets/D-v2"
GENERIC_TOOLS = {"read_file", "write_output", "submit_result", "notify_requester", "bash"}


def is_domain_tool(tool: str) -> bool:
    """Scenario-specific business action (not read/write/submit/notify/bash/request_* grant requests)."""
    return tool not in GENERIC_TOOLS and not tool.startswith("request_")


@lru_cache(maxsize=1)
def obs_index() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for i, o in enumerate(csv.DictReader(open(D2 / "obs_rows.csv"))):
        out.setdefault(int(o["sample"]), []).append(i)
    return out


@lru_cache(maxsize=4)
def _obs(layer: int) -> np.ndarray:
    return np.load(D2 / f"obs_L{layer:02d}.npy", mmap_mode="r")


def bags(ds: h.Dataset, rows, layer: int) -> list[np.ndarray]:
    """Observation rows (n_obs x 2880, float32) of each dataset row; test rows are refused by the dataset."""
    rows = ds._check_tv(rows)
    idx, A = obs_index(), _obs(layer)
    return [np.asarray(A[idx[int(ds.samples[r]["d2_sample"])]], dtype=np.float32) for r in rows]

POOL = {"max": lambda b: b.max(0), "mean": lambda b: b.mean(0)}
MODELS = {
    "Argus-V1": {"pool": "max", "layer": 16, "pca": 10, "C": 1.0,
                 "desc": "block-L observation rows -> element-wise max pooling -> StandardScaler -> PCA-10 -> LogReg(balanced)"},
}


def _X(cfg, ds, rows, layer):
    return np.stack([POOL[cfg["pool"]](b) for b in bags(ds, rows, layer)])


def _fit_X(cfg, X, y, layer):
    sc = StandardScaler().fit(X)
    p = PCA(n_components=cfg["pca"], random_state=0).fit(sc.transform(X))
    lr = LogisticRegression(C=cfg["C"], class_weight="balanced", max_iter=3000).fit(p.transform(sc.transform(X)), y)
    return {"cfg": cfg, "layer": layer, "scaler": sc, "pca": p, "lr": lr}


def _score_X(m, X):
    return m["lr"].decision_function(m["pca"].transform(m["scaler"].transform(X)))


def fit(name, ds, rows, layer=None):
    cfg = MODELS[name]
    layer = layer or cfg["layer"]
    return _fit_X(cfg, _X(cfg, ds, rows, layer), ds.y(rows), layer)


def score(model, ds, rows):
    return _score_X(model, _X(model["cfg"], ds, rows, model["layer"]))


def cv_fit_predict(name, ds, layer=None):
    """fit_predict for harness.grouped_cv; X is a column of dataset row ids."""
    cfg = MODELS[name]
    layer = layer or cfg["layer"]

    def fp(Xa, ya, Xb):
        ra, rb = Xa[:, 0].astype(int).tolist(), Xb[:, 0].astype(int).tolist()
        return _score_X(_fit_X(cfg, _X(cfg, ds, ra, layer), np.asarray(ya), layer), _X(cfg, ds, rb, layer))
    return fp
