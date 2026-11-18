# Implementation approach

## Architecture

The browser uses HTML, CSS and JavaScript with same-origin JSON APIs. Python handles chat extraction, policy review, exact-decimal procurement decisions and authenticated buyer workflows. PostgreSQL stores reference data, immutable run snapshots, policy versions, sessions, approval ledgers and audit events. The `psql` client is invoked through standard input; database secrets are supplied as environment variables, not committed or included in command arguments.

The deployment packages the existing application in a non-root Python 3.12 container. Render terminates HTTPS. Neon holds the private task data. Render receives a secret `DATABASE_URL`; the startup helper maps it to the libpq environment without changing the decision engine. The app uses its own password-based accounts, not Neon user authentication. No task-data files are served as static assets.

## Decision flow

Input validation preserves every original PR line and records rejection reasons. Valid requests are matched to catalogue items, grouped according to the applicable policy's plant and date rules, valued, then routed by ordered declarative conditions. Contract eligibility and the shared ceiling are checked before an award. Supplier ranking uses exact decimal weights and deterministic tie-breaking. The award price can differ from the valuation source; the output reason preserves this distinction.

Batch runs begin at the imported opening balances. Their snapshots include input CSV text, reference data and the applicable policy timeline, allowing repeatable replay. Live buyer approvals use a separate ledger, reject duplicate awards and serialize ceiling checks within a database transaction. They do not alter the baseline used for assessment batch runs.

## Policies as data

`src/engine/policy_v5.json` initializes the policy. Approved policies are stored in PostgreSQL. Thresholds, grouping windows, ordered predicates, ranking weights and effective dates can be changed through reviewed drafts without editing code. Runtime evaluation uses the most recent effective approved policy within the selected timeline. Amendments can therefore change the outcome of the same PR batch while the deployed code remains fixed.

The document parser recognizes supported amendment patterns. It exposes unresolved text and a full validated policy editor instead of silently claiming arbitrary document understanding. Procurement reviews the source, fixes extraction gaps, and approves a unique version. New business concepts outside the supported condition vocabulary are a known limitation; no rule engine can promise interpretation of arbitrary future instructions.

## Supplier export

The preferred CSV reuses the decision engine's ranking method, with an explicit as-of date. It emits at most three eligible suppliers per catalogue item, sequential ranks and supplier-specific reference prices. It excludes category top-ups and does not mutate database state.

## Security and data separation

Passwords use salted scrypt; session tokens are stored hashed and expire. Role checks protect data and mutations. Hosted cookies use HttpOnly, SameSite=Strict and Secure. Audit identities come from authenticated sessions. Policy and buyer approval paths use transactional conflict checks. Git and Docker exclusions keep private inputs, exports, environment files and database dumps out of published source and images. Only explicitly required final output CSVs belong in `submission/`.

Neon data is private through credentials and encrypted connections; its ordinary endpoint is internet-reachable. This is not the same as a private-network-only database. Hosting and database providers must be configured and verified before describing the app as live.

## Validation strategy

Tests cover input integrity, signed-contract reconciliation, worked examples, threshold/date boundaries, policy amendments, supplier eligibility, ranking, reference prices, deterministic replay, authentication, concurrent approvals, duplicate prevention and CSV downloads. Disposable local databases isolate integration checks. Before the hosting setup, both saved practice decision files matched fresh calculations field-for-field; the preferred export matched 155 rows for 68 eligible items on the practice date. These checks are a rehearsal, not a guarantee of the unseen evaluation results.

## AI tools used

OpenAI Codex assisted with requirements analysis, implementation, debugging, documentation and test execution. Runtime chat and policy parsing are deterministic local Python code. The application does not call an external LLM API or send task data to an AI provider during use. No API key is needed for chat or policy processing.

## Deliberate tradeoffs

The application prioritizes deterministic, inspectable decisions and a small deployable dependency set. It uses one item per chat and a simple threaded server rather than a full conversational agent or enterprise application framework. PostgreSQL subprocess calls trade throughput for a straightforward dependency footprint. High-volume production use would require connection pooling, workload limits, operational monitoring and further load testing. Ambiguity choices are recorded in `DECISIONS.md`.
