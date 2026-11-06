import sys, warnings
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
sys.path.insert(0, "src"); warnings.filterwarnings("ignore")
from argus_v.probes import load_probe_dataset
RUN = "artifacts/probe-reviewed-20261022T123507Z-237dc0/dataset/manifest.json"
for layer in (16, 23):
    tr = load_probe_dataset(RUN, split="train", layers=(layer,)); va = load_probe_dataset(RUN, split="validation", layers=(layer,))
    yt, yv = tr.labels.astype(int), va.labels.astype(int)
    lt = np.log([s.token_index + 1 for s in tr.snapshots])[:, None]; lv = np.log([s.token_index + 1 for s in va.snapshots])[:, None]
    g = np.array([r["family_id"] for r in tr.records])
    act = lambda: make_pipeline(StandardScaler(), LogisticRegression(C=0.001, max_iter=5000, class_weight="balanced"))
    oof = np.zeros(len(yt))
    for a, b in GroupKFold(5).split(tr.features, yt, g):
        oof[b] = act().fit(tr.features[a], yt[a]).decision_function(tr.features[b])
    sv = act().fit(tr.features, yt).decision_function(va.features)
    print(f"layer {layer}: corr(activation score, log prefix length) train OOF {np.corrcoef(oof, lt[:,0])[0,1]:.2f}, val {np.corrcoef(sv, lv[:,0])[0,1]:.2f}")
    # can activations predict prefix length itself? (how much the representation encodes length)
    from sklearn.linear_model import Ridge
    r = make_pipeline(StandardScaler(), Ridge(alpha=1000)).fit(tr.features, lt[:, 0])
    print(f"  ridge activation -> log prefix length: val R^2 {r.score(va.features, lv[:, 0]):.2f}")
    for name, xt, xv in (("length only", lt, lv), ("length + activation score", np.c_[lt, oof], np.c_[lv, sv]), ("activation score only", oof[:, None], sv[:, None])):
        m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)).fit(xt, yt)
        print(f"  {name:<28} val AUROC {roc_auc_score(yv, m.decision_function(xv)):.3f}")
    # activation within length strata: AUROC inside prefix-length tertiles of validation
    q = np.quantile(lv[:, 0], [1/3, 2/3]); bins = np.digitize(lv[:, 0], q)
    parts = [(int(yv[bins == k].sum()), roc_auc_score(yv[bins == k], sv[bins == k]) if 0 < yv[bins == k].sum() < (bins == k).sum() else None) for k in range(3)]
    print("  activation AUROC within validation length tertiles (violations, AUROC):", [(n, None if a is None else round(a, 3)) for n, a in parts])
