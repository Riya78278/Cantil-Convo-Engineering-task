# PostgreSQL data import

Use PostgreSQL 14 or later with the psql client on PATH and Python 3.10 or later. The importer uses standard PostgreSQL connection settings (PGHOST, PGPORT, PGDATABASE, PGUSER and .pgpass). Use a dedicated private database. The database user needs permission to create the corvo schema and its tables.

Run from the project root, with PostgreSQL running:

```sh
python3 -m venv .venv
.venv/bin/pip install -r src/imports/requirements.txt
createdb corvo_dev
export PGDATABASE=corvo_dev
.venv/bin/python src/imports/load.py kit "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
.venv/bin/python src/imports/load.py batch "/path/to/new_batch.csv"
```

The kit command reads the source archive in memory. It validates the CSVs, compares registered terms against every signed PDF, records PDF filenames and SHA-256 hashes, and adds the PDF-only agreements with zero opening consumption. Prices, quantities, ceilings and balances use PostgreSQL numeric columns. Master records and contract opening balances form a fixed baseline. A changed set of reference files requires a separate database for an independent baseline. Additional PR batches use the batch command in the same database.

Each result reports the SHA-256 batch identifier, input row count and number of lines flagged during intake. Each staged PR retains its input fields and original row number, so duplicate PR keys remain distinguishable. The nullable issue column records intake problems. A blank issue means ready for item matching and evaluation. Unknown free-text requests and their estimates are evaluated after matching in Phase 3.

Both commands use a single write transaction and a shared import lock. Identical batch bytes map to the same batch. Repackaging the same reference files in a ZIP preserves the baseline fingerprint. The PDF extractor handles the kit's text-based signed contract template and rejects unrecognized terms or conflicting schedules.

Example reconciliation query:

```sql
SELECT batch_id, count(*) AS input_lines, count(issue) AS flagged
FROM corvo.pr_lines GROUP BY batch_id;
```

To run integration tests, use a PostgreSQL role that can create temporary test databases. The suite creates and drops its own randomly named databases and leaves the working database intact.

```sh
.venv/bin/python -B tests/check_phase_two.py "/path/to/Cantill_FullStack_Task_Kit_vf(1).zip"
```
