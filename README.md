# Corvo procurement workspace

A policy-driven procurement application for chat requests, buyer review, versioned policy amendments, batch decisions and preferred-supplier exports. Built for the Cantill full-stack assessment.

## What is included

- One-item conversational intake with catalogue matching, clarification and explicit confirmation.
- Deterministic routing to contract, emergency or spot POs and RFQ, RFP, capex or new-item sourcing.
- Reviewed policy drafts, effective dates, approval history and replayable decision runs.
- Buyer overrides with reasons, atomic approvals, contract-ceiling checks and PO consolidation.
- Authenticated Employee, Buyer and Procurement roles.
- Decision CSV downloads and a dated preferred-supplier CSV export.

The repository contains application code, documentation and the initial executable policy configuration. The supplied ZIP, raw CSVs, contract PDFs, database dumps, passwords and practice exports are excluded. Obtain the task kit privately to run the supplied-data tests. No sample-data copy is bundled into the Docker image.

## Run locally with Docker

Requirements: Docker Desktop with Compose, and your private copy of the task kit. Run from the repository root:

```sh
cp .env.example .env
# Edit .env and replace POSTGRES_PASSWORD with a strong private password.
docker compose up --build -d
docker compose exec -T web python -m scripts.bootstrap import-kit - < '/absolute/path/to/task-kit.zip'
docker compose exec web python -m scripts.bootstrap create-user reviewer --name 'Procurement reviewer' --role procurement
```

The account command prompts for a password of at least 12 characters. Open http://127.0.0.1:8000 and sign in. Repeat account creation with distinct usernames and `--role employee` or `--role buyer` for separate roles. There are no default credentials or public sign-up routes.

If another copy of the app already uses port 8000, stop it or change only the left-hand host port in `compose.yaml`. `docker compose stop` stops services while retaining the database volume. Do not use `down -v` unless you intend to delete that local database.

## Run without Docker

Install Python 3.12 and PostgreSQL, including the `psql`, `createdb` and `dropdb` commands. Create a dedicated database and set libpq variables for it:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PGDATABASE=corvo
# Set PGHOST, PGPORT, PGUSER and PGPASSWORD as required by your local PostgreSQL.
createdb corvo
python -m scripts.bootstrap import-kit '/absolute/path/to/task-kit.zip'
python -m scripts.bootstrap create-user reviewer --name 'Procurement reviewer' --role procurement
python -m src.api.server --host 127.0.0.1 --port 8000
```

The existing developer database need not be recreated. Use its existing connection settings and accounts. Never run regression suites against the hosted database: integration tests require local database-creation privileges and make disposable databases.

## Use the application

1. Employees use **Request an item**, answer missing fields and confirm the summary.
2. Buyers use **Buyer review** to evaluate pending requests or upload a PR CSV, inspect reasoning, and approve or record an override.
3. Procurement uses **Policy management** to create a draft, compare changes, resolve extraction gaps, save a reviewed draft and approve a unique version.
4. On the policy page, **Check a batch against an approved policy** processes a CSV and downloads decisions. Selecting a version limits the effective-date timeline through that version.
5. **Export preferred suppliers** requires an explicit as-of date and offers a policy timeline selector. It downloads `preferred.csv` without creating orders or changing opening balances.

## Verify

```sh
python -B scripts/check_all.py '/absolute/path/to/task-kit.zip'
```

This runs all supplied-data regression suites and focused export/deployment tests. Public CI runs only synthetic tests and build checks; private task data must not be uploaded to GitHub Actions or public artifacts.

For an isolated hosting rehearsal (creates and removes its own Docker containers):

```sh
docker build -t corvo-procurement:phase6 .
python -B tests/check_container.py '/absolute/path/to/task-kit.zip'
```

## Hosting and submission

Use GitHub for the public source repository, Render for the Docker web service and Neon for a credential-protected PostgreSQL database. See [deployment instructions](docs/deployment.md), [architecture and AI disclosure](APPROACH.md), [assumptions](DECISIONS.md) and [export contract](docs/exports.md).

After the evaluator supplies the blind-test batch and amendment, run both timelines from the same opening baseline. Save only the final requested outputs in `submission/`:

```text
yourname__run1.csv
yourname__run2.csv
yourname__preferred.csv
```

Replace `yourname` with the candidate name. Practice exports are not final submissions. Freeze and record the tested commit before the evaluator's cutoff; afterward, do not modify application code. Auto-deploy is disabled in the Render Blueprint so submission-file pushes do not redeploy the app.

## Scope and limitations

Chat uses local parsing, not a general-purpose LLM. Unsupported wording requires clarification. Policy extraction supports documented text patterns plus a complete JSON rule editor; unrecognized rules require human review. Scanned PDFs need readable text. This is a small assessment application using a threaded Python HTTP server behind the host's HTTPS proxy, not a load-tested enterprise procurement deployment. Test results establish the tested cases, not guaranteed correctness on every unseen amendment.
