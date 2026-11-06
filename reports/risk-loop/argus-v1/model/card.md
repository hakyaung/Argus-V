# Argus-V1

- Structure: block-L observation rows -> element-wise max pooling -> StandardScaler -> PCA-10 -> LogReg(balanced) (layer L16)
- Training: StandardScaler (1 pass) -> PCA-10 (SVD) -> LogReg C=1.0, balanced, lbfgs full batch (26 iterations, converged=True); trained parameters 11
- Data: D-v2r train 9811 rows / 55 positives / 11 families; samples sha256 ddd8dc9a448cb4af…; labels DRAFT
- Train CV (family GroupKFold 5): AUROC 0.783, stratified 0.786; in-sample 0.827
- model.pkl sha256 a43fd7508b6741a50f9fc715d905ff56d77b7ca8d7ccac953da8245394b14f0f
- Scoring: `experiments/risk_loop/argus_models.score(model, ds, rows)` (logit)
- Validation: not computed here (A5).
