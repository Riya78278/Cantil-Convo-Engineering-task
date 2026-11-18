# Publish on GitHub and deploy to Render with Neon

## 1. Prepare the public repository

Run the complete checks locally using your private kit, then inspect the staged files before the first push. The task ZIP, source CSV/PDF files, practice results, environment files and database dumps must remain private. The executable v5.0 policy configuration is application configuration; no raw policy PDF is published. Tests refer to a privately provided kit path and do not include its contents.

Create a public GitHub repository for this project and push the reviewed source. The repository name and owner must come from the connected GitHub account. Do not invent a hosted URL or mark deployment complete before the service is verified.

## 2. Create the Neon database

Create a dedicated Neon project/database in a region close to the Render web service. Use a PostgreSQL 16 database for parity with local Compose. In Neon's Connect dialog, choose the direct (non-pooled) connection string. Preserve its TLS and channel-binding parameters. Store the URL privately; do not paste it into source files, screenshots or issue comments.

The direct endpoint avoids introducing transaction-pool behavior into this small application's setup/migration commands. Database credentials protect the data; the standard Neon endpoint is internet-reachable. If network-only access is required, configure an appropriate network restriction separately rather than assuming credentials provide network isolation.

## 3. Bootstrap the hosted database privately

From the local project, use the tested Python environment and `psql` client. Read the connection URL without echoing it or saving it in shell history:

```sh
read -s 'DATABASE_URL?Paste the Neon direct database URL: '
export DATABASE_URL
python -m scripts.bootstrap import-kit '/absolute/path/to/task-kit.zip'
python -m scripts.bootstrap create-user reviewer --name 'Procurement reviewer' --role procurement
unset DATABASE_URL
```

The `read` syntax above is for macOS zsh. Bootstrap imports the supplied files directly into Neon over its encrypted connection; they are never pushed to GitHub or baked into the container. Repeat with separate Employee/Buyer accounts as needed. Passwords are requested interactively. Do not run `scripts/check_all.py` against Neon: the regression harness is for local disposable databases.

Bootstrap starts a fresh deployment at v5.0. It does not copy local practice amendments, sessions or approvals. Apply the actual evaluation amendment through the hosted policy screen when required. Repeat import of the same baseline is idempotent; a different baseline is rejected.

## 4. Deploy Render

Connect Render to the public GitHub repository and create a Blueprint from `render.yaml`. The Blueprint defines a Docker web service, no Render database. Review the service plan and cost before provisioning. It currently specifies the 0.5 CPU / 512 MB plan in Singapore. Change the region before creation if needed to match Neon.

When Render requests `DATABASE_URL`, supply the Neon direct URL in the secret environment field. `PGSSLMODE=require` and `CORVO_SECURE_COOKIES=1` are set by the Blueprint. Render supplies `PORT`; the startup helper binds to `0.0.0.0` at that port and idempotently initializes schema. The health check requests `/`; authenticated database operations must still be checked in the smoke test below.

The image runs as a non-root user and contains only the source, SQL schema and setup scripts. All persistent state lives in Neon, not the ephemeral Render filesystem. Automatic deploys are disabled. Do not expose database credentials in build logs or frontend JavaScript.

## 5. Hosted acceptance checks

1. Wait for Render's deployment to finish successfully and open its returned HTTPS URL.
2. Sign in with the provisioned Procurement account. Confirm the session cookie is Secure and unauthenticated data requests are rejected.
3. Upload the private sample PR CSV and compare the v5.0 export with the validated local baseline.
4. Exercise chat confirmation, buyer review and policy draft creation using disposable practice requests. Do not approve unwanted live orders.
5. For a practice amendment, compare v5.1 output and preferred CSV with the local rehearsal. Keep actual evaluator data/runs separate.
6. Restart the service and verify accounts, policies and saved runs remain available.
7. Record the Git commit and the verified app URL. Give evaluators login details through their private channel, never in the public README.

## 6. Freeze and submit

Verify the organizer's exact cutoff because the provided email contains inconsistent weekday/date labels. Freeze the tested commit before that cutoff. Afterward use only the existing upload, review and export features; do not change application code. Push the three requested final CSVs in `submission/`. With auto-deploy disabled, those pushes do not redeploy the app. Do not submit practice outputs as blind-test outputs.

## Operations

Use the provider's private backup/restore facilities and test restores to a separate database. Never commit backups. Database or service suspension can delay the first connection, so check the app before the evaluation window. Keep Render and Neon credentials out of chat and source control. Rotate credentials if accidentally exposed.

References: [Render Blueprint specification](https://render.com/docs/blueprint-spec), [Render Docker deployment](https://render.com/docs/docker), [Neon connection guidance](https://neon.com/docs/connect/connect-from-any-app).
