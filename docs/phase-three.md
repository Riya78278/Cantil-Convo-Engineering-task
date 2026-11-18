# Procurement engine and batch API

The pure Python engine reads versioned policy data and an immutable reference snapshot. Its pipeline validates intake, matches catalogue items, groups demand, values requirements, reserves contract ceiling within the run, selects routes and suppliers, then consolidates planned POs. Each returned requirement carries input row numbers, exact valuation, the applied clause and supplier score factors.

The default policy is in src/engine/policy_v5.json. On API startup it is installed in corvo.policies if absent. Later runs read policy documents from the database. The rule interpreter supports ordered all/any conditions over item type, category, plant, value, urgency, contract availability, history and new-item status. Its configuration also contains grouping, consolidation and lookback windows and ranking weights. Phase 4 will add the document/chat amendment approval interface.

## Local run

Use the Phase 2 connection environment and imported database, then run from the project root:

```sh
python3 -m src.api.server --port 8000
curl --fail-with-body --cookie session.cookies -H 'Content-Type: text/csv' --data-binary @/path/to/batch.csv http://127.0.0.1:8000/api/process-batch
```

Phase 5 requires an authenticated Buyer or Procurement session. For curl, obtain a session cookie through /api/login and use a private cookie jar. The browser workspace handles this automatically.

The endpoint accepts a raw UTF-8 CSV body of up to 10 MiB. JSON returns run_id, decisions, requirements and closing_contract_balances. Add an Accept: text/csv header for the decision CSV. Invalid CSV structures return HTTP 400. Invalid rows remain present as NOT_PROCESSED. The local HTTP adapter is for development and integration tests. Deployment serving and buyer authentication belong to later phases.

Every completed run records the input CSV, reference snapshot, policy documents and results in PostgreSQL. Identical inputs and snapshots produce the same run ID. Reference balances are never changed by evaluation. Closing balances describe simulated consumption, enabling independent blind-test runs.

## Verification

```sh
python3 -B tests/check_phase_three.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
python3 -B tests/check_batch_api.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
```

The engine tests compare all 14 worked examples and all three preferred supplier rankings. Additional cases cover exact thresholds, calendar windows, 24-hour consolidation, inclusive contract dates, shared ceilings, active suppliers, history boundaries, ambiguous matching, new-item estimates, policy changes, decimal arithmetic and replay. The API tests create disposable PostgreSQL databases and make actual HTTP requests.

PO IDs are generated deterministically in requirement creation order. The sample's representative PO IDs differ from this full-batch run. Tests verify grouping and consolidation relationships rather than requiring identical numbering. Worked example 6 shows one representative value for two different requirements. Each output retains its own group's value. Example 14 likewise spans two groups on one PO.

No full answer key exists for the sample. Passing the supplied examples and synthetic cases does not verify every unseen description or policy amendment. Matching currently normalizes industrial abbreviations and punctuation, preserves numeric order and requires an unambiguous token match. New unmatched items use the supplied estimate. Unresolved ambiguous descriptions require review.
