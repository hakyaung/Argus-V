# D-v2r (D-v2 relabelled with review DRAFT, substantive action target)

**STATUS: DRAFT labels — LLM blind review (labels-draft-v1), not human-confirmed.**

Built by `experiments/risk_loop/build_d_v2r.py`. samples.csv sha256 `ddd8dc9a448cb4af4fdc2e984d33cdc54e7f957279ad087077c2230d48dcbb0f`.
Labels: `artifacts/label-review-v2/labels-draft-v1/step_labels.csv` sha256 `5d5a9383d23b7e38267c99d30fe9a29d421b8fef410a7342df5667862e2e2493`.
Features: D-v2/obs_L{11,16,23}.npy via `d2_sample`. v1 rows keep their original rule labels.
Dropped rows: {'v2:procedure_skip': 162, 'v2:disputed': 80, 'v2:output_falsification': 39}. Dup groups with conflicting labels: 0.

- train: {"rows": 9811, "positives": 55, "positives_by_version": {"v1": 17, "v2": 38}, "positive_families": ["F01", "F03", "F04", "F06", "F08", "F10", "F15", "beneficiary_binding", "model_allowlist", "source_immutability", "tenant_access"], "positive_tools": {"place_print_order": 8, "select_model": 7, "aggregate_supplier_spend": 5, "classify_requests": 4, "issue_payment": 3, "release_outbound_wire": 3, "link_entry_badge": 3, "read_tenant_file": 2}, "violation_trajectories": 50, "normal_trajectories": 1219, "dedup_rows": 9809}
- validation: {"rows": 4408, "positives": 29, "positives_by_version": {"v1": 8, "v2": 21}, "positive_families": ["F02", "F05", "F09", "aggregate_batch_review", "identity_provisioning"], "positive_tools": {"invite_reviewer": 7, "apply_in_change_window": 4, "export_with_support_window": 4, "post_in_late_window": 3, "download_with_access_window": 3, "deliver_in_release_window": 2, "write_output": 2, "execute_refund_batch": 1}, "violation_trajectories": 28, "normal_trajectories": 545, "dedup_rows": 4408}
