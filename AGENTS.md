# AGENTS.md

Repo orientation for coding agents (Codex, Claude Code, etc.). Envision Construction, portfolio tier 1 service.

## What this is

Domain-wide Gmail ingestion service: a Cloud Run HTTP job that enumerates every Google Workspace user
(Admin Directory API), impersonates each via domain-wide delegation, scrapes Gmail messages, and writes
them to BigQuery (`claude-mcp-457317.gmail_analytics.messages`) for the Envision context layer.
It is a scheduled scrape job, not an API for other services. See `README.md` and `README_CLOUDRUN.md`.

## Stack and entry points

- Python 3.10 (pinned by digest in `Dockerfile`), no framework beyond `functions-framework`.
- `main.py`: HTTP entry `run_scraper` (GET / = health check, POST / = trigger scrape with optional
  `query`, `max_per_user`, `incremental` JSON body).
- `gmail_scraper.py`: all logic (auth, user enumeration, Gmail paging, BigQuery sink, dedup).
- Dependencies: `requirements.txt` only. No pyproject, no Makefile, no test suite, no linter config.

## Commands (verified against README_CLOUDRUN.md and Dockerfile)

```bash
pip install -r requirements.txt
functions-framework --target=run_scraper --debug   # local run, port 8080
curl -X POST localhost:8080 -H 'Content-Type: application/json' -d '{"max_per_user": 10}'
```

There are no tests to run. If you add logic, adding the first tests is welcome but keep them offline
(mock the Google API clients); this code talks to live Workspace and BigQuery otherwise.

## Deploy

Push to `main` deploys. `.github/workflows/deploy.yml` authenticates via Workload Identity Federation,
does a source deploy of Cloud Run service `gmail-scraper` (us-central1) with `--no-allow-unauthenticated`,
grants the runtime SA `roles/run.invoker` (and fails if a public `allUsers`/`allAuthenticatedUsers`
binding is present), then updates-in-place (or creates) the Cloud Scheduler job `gmail-scraper-5min`
(POST every 5 minutes with an OIDC token, `{"incremental": true, "max_per_user": 100}`, attempt
deadline 1800s). The scheduler step never deletes the job, so a failed deploy cannot strand the cadence. Org policy requires every action
in the workflow to be pinned to a full-length commit SHA — an unpinned `@vN` reference kills the run
before any step executes (this silently blocked all deploys 2026-04 → 2026-08).
`deploy.sh` is informational-only notes; `setup_scheduler.sh` and `cloudbuild.yaml` no longer exist
(the Cloud Build routing from PR #4 never ran green and was removed 2026-08-02). Never deploy by hand.

## Constraints and gotchas

- Auth is service-account KEY FILE only: `gmail_scraper.py` loads `SERVICE_ACCOUNT_FILE`
  (default `./service-account-key.json`) via `from_service_account_file`, then `with_subject()` for
  domain-wide delegation. There is NO ADC fallback: without the key file, every POST fails
  (GET health check still works). The `Dockerfile` copies only `main.py` and `gmail_scraper.py`,
  no key is baked into the image — so the deployed scrape path currently fails at runtime
  (`status: failed`, HTTP 200). See "Known issue" in `README_CLOUDRUN.md`; fixing runtime auth is
  an owner decision, not a drive-by fix.
- NEVER commit keys. `.gitignore` already blocks `service-account-key.json` and `*-key.json`; keep it so.
- Env vars (defaults in `gmail_scraper.py`, set for prod in `deploy.yml`): `PROJECT_ID`, `DATASET_ID`,
  `TABLE_ID`, `ADMIN_EMAIL`, `SERVICE_ACCOUNT_FILE`. Container listens on port 8080.
- Required scopes: `gmail.readonly` + `admin.directory.user.readonly` (domain-wide delegation) and
  BigQuery write roles. See `README_CLOUDRUN.md` prerequisites.
- The service requires authenticated invocation (`--no-allow-unauthenticated`, hardened 2026-08-02
  security sweep): only identities with `roles/run.invoker` can call it, and Cloud Scheduler sends an
  OIDC token as `claude-service-account@claude-mcp-457317.iam.gserviceaccount.com`. The deploy fails
  if a public (`allUsers`/`allAuthenticatedUsers`) binding reappears. Treat any loosening of this
  posture as a deliberate decision with the owner, never a convenience change.
- Data sensitivity: the BigQuery table holds full email bodies for the entire Workspace domain.
  Never dump row contents into logs, PR descriptions, or fixtures.
- BigQuery schema (17 columns) is created at runtime by `ensure_table_exists`; if you change it,
  update the schema table in `README_CLOUDRUN.md` to match.
- Incremental mode dedups against existing `message_id`s and uses `MAX(date_sent)` as the cursor;
  full rescrapes are triggered with `"incremental": false`.
- Every deploy updates the scheduler job in place (flag-specified fields are overwritten; the job is
  created if missing), so console edits to those fields will not survive a deploy; change the
  "Update Cloud Scheduler (OIDC)" step in `.github/workflows/deploy.yml` instead.

## Planning context

`.planning/codebase/INTERCONNECTIONS.md` (local, untracked) maps inbound/outbound integrations;
this repo has no `.planning/STATE.md` or `ROADMAP.md`.
