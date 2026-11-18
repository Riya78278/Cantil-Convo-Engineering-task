# Blind-test exports

One UTF-8 CSV per output with a header, conventional CSV field quoting and plain decimal INR amounts. Preserve input order and identifiers in both decision exports. Use an empty cell when a field does not apply.

```csv
pr_no,pr_line,resolved_item_code,group_id,po_id,decision,route,supplier_id,unit_price,group_value_inr,suggested_supplier_ids,policy_version,reason
```

- Preserve one row per input line, including rejected lines. Use the matched item code or NEW for a confirmed new item. Clubbed lines share group_id and repeat the whole group's valuation.
- PO decisions use CONTRACT_PO, EMERGENCY_PO or SPOT_PO. Include po_id, supplier_id and actual unit_price. A consolidated PO shares its ID across the relevant groups.
- SOURCING_REQUEST uses RFQ, RFP, CAPEX_SOURCING or NEW_ITEM_SOURCING. List suggested supplier IDs in rank order separated by semicolons. Leave PO fields empty. New items have no suggestions.
- For NOT_PROCESSED, preserve the input key and explain the rejection. Leave decision, unassigned IDs, amounts and suggestions empty. Preserve a known resolved code when available.
- Record policy_version and a reason with clause and supporting document identifiers where applicable. Record the valuation source separately in the reasoning if it differs from the award price source.

```csv
item_code,rank,supplier_id,reference_price
```

For the requested as-of date, export up to three eligible preferred suppliers per item in ascending rank from 1. An empty list contributes no rows. Category top-ups are for sourcing suggestions only.

Run 1 uses v5.0. Run 2 uses the approved amendment from its effective date over the identical opening data. Replace yourname with the candidate name and push submission/yourname__run1.csv, submission/yourname__run2.csv and submission/yourname__preferred.csv.

## Preferred supplier download

On Policy management, scroll to **Export preferred suppliers**. Enter the evaluation's as-of date, choose the policy timeline, and click **Download preferred suppliers CSV**. The downloaded file is `preferred.csv`; rename it for the practice or final submission. Do not substitute today's date for a date specified by the evaluator.

For the current practice, use 2026-11-01 and timeline 5.1. Policies are selected by effective date within that timeline. The export covers all catalogue items with eligible suppliers, in item-code and rank order, up to three rows per item. Items without eligible suppliers produce no rows. Prices use the engine's supplier-specific latest historical PO or contract reference, formatted to two decimal places. Category top-ups are excluded.

The authenticated, read-only endpoint is `GET /api/preferred-suppliers.csv?as_of=2026-11-01&policy_version=5.1`. Buyer and Procurement roles may access it; the screen is on the Procurement policy page. It does not change baseline balances, approvals, policies or saved batch results. The as-of date is mandatory; unknown versions and dates with no effective policy are rejected. Tests: `tests/check_preferred_export.py` and the preferred-export HTTP test in `tests/check_phase_five.py`.
