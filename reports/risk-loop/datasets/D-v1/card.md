# D-v1 dataset card

Source manifest: `artifacts/probe-reviewed-20261022T123507Z-237dc0/dataset/manifest.json` (sha256 5b91554765c135daade7b2e019aa5466e5e2f2ba58c9d25357b28e5e978e8277)
labels_revision: review-progress-cbd682a0bdbf2a21, split_revision: scenario-splits-abd6515ef404a2b7
Rows: 1692 (train/validation features: 1256 x 24 layers x 2880, float32; test: IDs only, no features, labels empty, prefix_hash not read)
Feature row: C-01 pre_action activation at manifest token_index (post_block_pre_final_norm).
condition/seed are AUDIT-ONLY (forbidden model input).

| split | rows | positives | families | families w/ pos | trajectories | violation traj | normal traj | dup fraction | after-first-violation pos | median prefix_length (neg / pos) |
|---|---|---|---|---|---|---|---|---|---|---|
| train | 1039 | 88 | 24 | 18 | 198 | 60 | 138 | 0.264 | 28 | 2045 / 2276 |
| validation | 217 | 12 | 6 | 4 | 50 | 9 | 41 | 0.267 | 3 | 1932 / 2416.5 |
| test | 436 | (sealed) | 10 | (sealed) | 85 | (sealed) | (sealed) | (not computed) | (sealed) | (sealed) |

dup fraction = share of rows that are NOT the first row of their prefix-hash group within the split (D-v1-dedup = rows with is_dup_representative==true).

## Length distribution per label (train/validation)

| split | label | q1 | median | q3 |
|---|---|---|---|---|
| train | 0 | 1740 | 2045 | 2388 |
| train | 1 | 2023.25 | 2276 | 2478 |
| validation | 0 | 1676 | 1932 | 2286 |
| validation | 1 | 2075.25 | 2416.5 | 2822.5 |

## Positives per family (train/validation)

| split | family | rows | positives |
|---|---|---|---|
| train | artifact_authenticity | 50 | 2 |
| train | artifact_entitlement | 35 | 0 |
| train | artifact_provenance_reconciliation | 45 | 10 |
| train | dependency_lock_integrity | 28 | 3 |
| train | executable_allowlist | 64 | 6 |
| train | job_ownership_mutex | 34 | 3 |
| train | nonexportable_key_boundary | 42 | 10 |
| train | optimistic_concurrency | 49 | 3 |
| train | principal_impersonation | 23 | 1 |
| train | processing_location | 38 | 8 |
| train | project_access | 52 | 13 |
| train | protected_control_write | 4 | 0 |
| train | pseudonym_reidentification_boundary | 43 | 2 |
| train | public_link_acl | 27 | 0 |
| train | purpose_limited_identifier_join | 40 | 1 |
| train | read_only_task_side_effect | 48 | 0 |
| train | recipient_identity_verification | 41 | 0 |
| train | recipient_scope | 68 | 1 |
| train | recording_consent_gate | 60 | 3 |
| train | revocation_over_cached_grant | 72 | 13 |
| train | separation_of_duties | 44 | 4 |
| train | tls_chain_identity | 29 | 1 |
| train | unapproved_network_access | 43 | 0 |
| train | unauthorized_data_destination | 60 | 4 |
| validation | certificate_revocation_evidence | 34 | 0 |
| validation | document_signature_verification | 31 | 4 |
| validation | minimum_disclosure | 54 | 1 |
| validation | minimum_group_disclosure | 35 | 0 |
| validation | retention_hold_delete | 33 | 1 |
| validation | trusted_freshness_attestation | 30 | 6 |

Prefix hashes shared across train/validation: 0

```json
{
 "train": {
  "rows": 1039,
  "positives": 88,
  "families": 24,
  "families_with_positives": 18,
  "trajectories": 198,
  "violation_trajectories": 60,
  "normal_trajectories": 138,
  "dup_fraction_non_representative": 0.2637,
  "fraction_rows_in_shared_prefix_groups": 0.4273,
  "dedup_rows": 765,
  "dedup_positives": 66,
  "after_first_violation_positives": 28,
  "prefix_length_median_neg": 2045,
  "prefix_length_median_pos": 2276.0,
  "prefix_length_quartiles_neg": [
   1740.0,
   2045.0,
   2388.0
  ],
  "prefix_length_quartiles_pos": [
   2023.25,
   2276.0,
   2478.0
  ],
  "step_index_median_neg": 2,
  "step_index_median_pos": 3.0
 },
 "validation": {
  "rows": 217,
  "positives": 12,
  "families": 6,
  "families_with_positives": 4,
  "trajectories": 50,
  "violation_trajectories": 9,
  "normal_trajectories": 41,
  "dup_fraction_non_representative": 0.2673,
  "fraction_rows_in_shared_prefix_groups": 0.4654,
  "dedup_rows": 159,
  "dedup_positives": 12,
  "after_first_violation_positives": 3,
  "prefix_length_median_neg": 1932,
  "prefix_length_median_pos": 2416.5,
  "prefix_length_quartiles_neg": [
   1676.0,
   1932.0,
   2286.0
  ],
  "prefix_length_quartiles_pos": [
   2075.25,
   2416.5,
   2822.5
  ],
  "step_index_median_neg": 2,
  "step_index_median_pos": 3.0
 },
 "test": {
  "rows": 436,
  "families": 10,
  "trajectories": 85
 },
 "prefix_hashes_shared_across_train_validation": 0
}
```
