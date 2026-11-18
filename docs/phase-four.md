# Policy amendments and approval

Phase 4 adds a local Procurement screen at the API root. Use the Phase 2 database connection settings and run:

```sh
python3 -B -m src.api.server --port 8000
```

Open http://127.0.0.1:8000/policy. Sign in with the Procurement role, select a base version, enter the new version and effective date, then describe an amendment or upload a PDF, TXT, MD or full-policy JSON file. Create the draft to view a plain-language comparison. Statements that could not be interpreted stay visible beside the source text.

Review ordered rules, change their routes, conditions or priority, and add rules using the editor. The complete policy editor supports dates, windows and ranking weights as well as rules. Apply JSON changes before saving. Check the review acknowledgement after resolving the source statements, save the reviewed draft, inspect the final comparison, then approve.

## Supported text interpretation

The local interpreter handles the following patterns without an API key. Put each statement on a separate line or separate it with a semicolon.

```text
Version 5.1
Effective from 2026-11-01
Set spot limit to INR 150000
Set emergency limit to 4 lakh
Set RFP threshold to 15 lakh
Set clubbing window to 10 days
Set need-by tolerance to 5 days
Set consolidation window to 12 hours
Set purchase history window to 300 days
Set preferred supplier history window to 500 days
Move clause 5.3 before clause 5.2
Set clause 5.5 suggestions to 4
Add clause 9.1 before clause 5.2: for plant CHK1 and category Safety and value at least 10000, require RFQ, suggest 3
```

The RFQ upper limit and RFP lower limit share one boundary. Changing either through text changes both and exposes both changes in the preview. Currency amounts support INR, Rs, rupee symbols, commas, lakh, crore and million. Full policy JSON uses the schema shown in src/engine/policy_v5.json.

The parser supports these sentence patterns. Other prose requires explicit correction in the editor. Text-based PDFs are supported, with a 50-page limit. Scanned PDFs require a text version. Original extracted text, source filename and source hash are retained. Unrecognized statements remain in the audit record even after the reviewer resolves them in the draft. Approval records that acknowledgement.

## Persistence and concurrent editing

Drafts have revision numbers. Saving a stale revision or approving a changed policy baseline returns HTTP 409. Start a fresh draft against the current approved policy list if a competing amendment was approved. The policy version must be unique. When two versions share an effective date, later approval wins for that date. Choosing the earlier version for a batch preserves its earlier timeline.

Each approval is atomic and repeat approval is idempotent. The approved document is immutable through these endpoints. The engine reads approved policy data on each request, so approval requires no server restart. Requirement dates determine which approved version applies. Future effective dates do not affect earlier requirements.

Phase 5 binds reviewer names to the authenticated session. Client-supplied actor names are ignored by the API. Production serving belongs to the deployment phase.

## API

| Method and path | Behavior |
| --- | --- |
| GET /api/policies | Approved policy timeline and saved drafts |
| POST /api/policy-drafts | Create from text or a base64-encoded document |
| GET /api/policy-drafts/{id} | Source, candidate, comparison, revision and audit |
| POST /api/policy-drafts/{id}/save | Save candidate, actor, revision and reviewed acknowledgement |
| POST /api/policy-drafts/{id}/approve | Approve the specified revision |
| POST /api/process-batch?policy_version=5.0 | Run CSV against the timeline through a selected version |
| POST /api/replay-run | Replay the stored run_id using its exact input and snapshot |

Create accepts actor, base_version, version, effective_date and either text or file_base64 plus filename. Save accepts actor, revision, candidate and reviewed. Approval accepts actor and revision. File payloads are JSON. Batch payloads use text/csv.

## Tests

```sh
python3 -B tests/check_phase_four.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
```

Tests cover threshold and window parsing, new plant/category rules, rule ordering, PDF reading, unknown statements, malformed configurations, draft isolation, approval, same-date version ordering, effective dates, persisted state, audit events, stale edits, concurrent approvals and original-run replay. The suite creates disposable databases. Browser testing additionally exercises the visible create → review → save → approve flow.

Source files and policy documents remain outside the public repository. The server stores imported documents and runs in the private database.
