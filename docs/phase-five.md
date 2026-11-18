# Employee chat and buyer workspace

Phase 5 provides authenticated employee, buyer and Procurement roles. The root page contains sign-in, chat requests, buyer review and approved documents. Procurement users can also open the policy management page.

## Local setup

Use the database and Python environment described in Phase 2. Provision accounts in your private database using an interactive password prompt:

```sh
python3 -B -m src.workspace.auth employee --name "Employee" --role employee
python3 -B -m src.workspace.auth buyer --name "Buyer" --role buyer
python3 -B -m src.workspace.auth procurement --name "Procurement" --role procurement
python3 -B -m src.api.server --port 8000
```

Open http://127.0.0.1:8000 and sign in. Account passwords need at least 12 characters. The repository contains no built-in application accounts. Test suites provision synthetic accounts only in disposable databases.

Passwords are stored using scrypt with a random per-user salt. Sessions expire after 12 hours and are stored by token hash. Cookies use HttpOnly and SameSite=Strict. Set CORVO_SECURE_COOKIES=1 when hosting behind HTTPS. Five failed logins temporarily lock the account for five minutes. Deployment and HTTPS configuration belong to Phase 6.

## Employee flow

Describe one item per conversation, for example:

```text
Need 200 nitrile gloves for Chakan by next Friday
Normal
```

The assistant grounds descriptions in the item master, offers item codes when a description is ambiguous, asks for missing quantity, plant, need-by date or urgency, then displays a confirmation summary. It understands dates expressed as YYYY-MM-DD, today, tomorrow, in N days and weekdays. Weekday requests mean the next occurrence strictly after today in Asia/Kolkata. Urgent is interpreted as Emergency and explicitly shown in the summary.

Unmatched descriptions require an explicit “new item” reply. New items also require a unit and estimated unit price. Unit conflicts require correction rather than an assumed conversion. Employees can change quantities, item descriptions or codes, dates, urgency and plant before confirmation, or type “start over”.

Confirmation creates one PR even when retried or clicked concurrently. It evaluates pending confirmed requests through the shared engine and returns the proposed route. This remains subject to buyer approval. Employees see only their own conversations and requests.

Chat uses deterministic catalogue and language parsing. It handles the documented purchasing phrases and follow-up answers. More unusual phrasing can require additional clarification. The final summary is always shown before creating a request.

## Buyer flow

Evaluate pending chat requests or upload a PR batch CSV. Select a run, then review a requirement. The workspace shows all constituent PR lines, requirement value, route, supplier and actual PO unit price or sourcing suggestions, policy version, clause and source contract or historical PO. Supplier ranking factors and reference-price sources can be expanded.

Approval creates a stored PO or sourcing document and audit event. Requirements with the same planned PO in a run share one document. Repeat approvals return the existing document. A PR line cannot be awarded again through another run. The application creates local procurement records. Sending orders to external suppliers remains outside the task.

An override needs a written explanation and a fresh requirement revision. It preserves the engine's original decision. Overrides can change route, supplier and PO price. Contract overrides must match the selected contract terms. New items require catalogue onboarding before a PO award. Capex requires explicit approval acknowledgement. After overriding, review the new recommendation and approve separately.

## Ceiling accounting and reproducible runs

Batch simulations always use the imported opening balances. Approval records actual contract spending in a separate ledger. Pending live chat evaluation adds that spending to the reference consumption. Approval rechecks supplier status, contract validity, rate and remaining ceiling under a transaction lock. A failed approval leaves no partial document or ledger charge.

Each live evaluation groups currently pending confirmed requests. Earlier approved requests keep their original document and audit record. Newly arrived demand is evaluated as a new pending workset. Overridden requirements receive their own document rather than being merged automatically with a different original PO recommendation. These operational choices preserve approved records while batch scoring still applies complete-batch clubbing and consolidation.

## APIs

Authenticated session cookies are required for all data APIs. POST /api/login accepts username and password and sets the session cookie. POST /api/logout revokes it. GET /api/me returns the signed-in role. The backend derives audit actors from the session and ignores caller-supplied actor names.

| Endpoint | Role and function |
| --- | --- |
| POST /api/conversations | Any signed-in user starts a private chat draft |
| GET /api/conversations/{id} | Owner reads the draft |
| POST /api/conversations/{id}/message | Owner sends message plus current revision |
| POST /api/conversations/{id}/confirm | Owner confirms current revision |
| GET /api/my-requests | Owner sees confirmed requests and approvals |
| POST /api/buyer/evaluate | Buyer or Procurement evaluates pending requests |
| GET /api/buyer/runs | Buyer or Procurement lists saved evaluations |
| GET /api/buyer/runs/{id} | Buyer or Procurement reads requirements and audit |
| POST /api/buyer/override | Buyer or Procurement saves a reasoned override |
| POST /api/buyer/approve | Buyer or Procurement approves a requirement |
| GET /api/documents | Buyer or Procurement reads created documents |

Batch processing and replay require Buyer or Procurement. Policy changes require Procurement. Account provisioning uses the local CLI.

## Verification

```sh
python3 -B tests/check_phase_five.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
```

The tests cover chat extraction, follow-ups, ambiguous and new items, corrections, units, ownership, sessions, roles, concurrent confirmation, PO consolidation, override comments, actor attribution, capex acknowledgement, cross-run duplicate prevention, contract ledger consumption and replay. The older API suites now authenticate synthetic Procurement users to verify their original behavior under the new access controls.
