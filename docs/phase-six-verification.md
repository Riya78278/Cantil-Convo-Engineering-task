# Phase six verification

Documentation and deployment configuration are prepared locally for GitHub, Render and Neon. Live publication still requires connected provider accounts, creation of the requested public repository, selection of the Render plan and a Neon database connection. No hosted URL has been verified yet.

## Completed checks

- Existing Phase 1–5 and batch API tests: 65 passed.
- Preferred-export focused tests: 5 passed.
- Deployment configuration tests: 4 passed, including percent-encoded credentials and TLS/channel-binding options.
- Both browser JavaScript files passed syntax checks; Compose configuration passed validation.
- Docker image built with the pinned PDF dependency and a non-root runtime user.
- A fresh disposable PostgreSQL 16 container accepted startup through `DATABASE_URL`.
- Private ZIP import through standard input and identical reimport succeeded.
- An authenticated container run of v5.0, approval of the practice v5.1 amendment, and the second run matched local calculations exactly.
- The container preferred export matched the local practice calculation: 155 rows for 68 items.
- After web-container restart, the session, policies, saved run replay and export remained available.
- The deployment image contained no raw ZIP, PDF, CSV, database dump or environment files.
- The public-source staging audit excluded task data, practice outputs and secrets.
- The three user-renamed practice downloads were compared again: both 186-line decision files and the 155-line preferred file remained unchanged.

The first container test exposed a setup-only database URL translation issue, which was corrected and retested. The test harness also refreshes Docker's dynamically assigned port after restart. Neither change alters the procurement engine. Test containers and their temporary network were removed; the existing local application and database were not reset.

## Remaining verification

Run the hosted acceptance checks in `deployment.md` after GitHub/Render/Neon access is connected. The Docker rehearsal does not prove Neon-specific connectivity, Render provisioning, cloud TLS settings or the live public URL. The assessment's unknown blind-test data is not yet covered by the practice results.
