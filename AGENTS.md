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
does a source deploy of Cloud Run service `gmail-scraper` (us-central1), then runs `./setup_scheduler.sh`,
which deletes and recreates the Cloud Scheduler job `gmail-scraper-5min` (POST every 5 minutes,
`{"incremental": true, "max_per_user": 100}`, attempt deadline 1800s).
`deploy.sh` and `cloudbuild.yaml` are older manual paths kept in-repo; do not use them for routine work,
the CI workflow is the current mechanism (see recent `fix(ci)` commits). Never deploy by hand.

## Constraints and gotchas

- Auth is service-account KEY FILE only: `gmail_scraper.py` loads `SERVICE_ACCOUNT_FILE`
  (default `./service-account-key.json`) via `from_service_account_file`, then `with_subject()` for
  domain-wide delegation. There is NO ADC fallback: without the key file, every POST fails
  (GET health check still works). The `Dockerfile` copies only `main.py` and `gmail_scraper.py`,
  no key is baked into the image.
- NEVER commit keys. `.gitignore` already blocks `service-account-key.json` and `*-key.json`; keep it so.
- Env vars (defaults in `gmail_scraper.py`, set for prod in `deploy.yml`): `PROJECT_ID`, `DATASET_ID`,
  `TABLE_ID`, `ADMIN_EMAIL`, `SERVICE_ACCOUNT_FILE`. Container listens on port 8080.
- Required scopes: `gmail.readonly` + `admin.directory.user.readonly` (domain-wide delegation) and
  BigQuery write roles. See `README_CLOUDRUN.md` prerequisites.
- The service is deployed `--allow-unauthenticated` (see `deploy.yml` flags): anyone with the URL can
  trigger a scrape. Do not paste the service URL into public places, and treat auth-posture changes as
  a deliberate decision with the owner, not a drive-by fix.
- Data sensitivity: the BigQuery table holds full email bodies for the entire Workspace domain.
  Never dump row contents into logs, PR descriptions, or fixtures.
- BigQuery schema (17 columns) is created at runtime by `ensure_table_exists`; if you change it,
  update the schema table in `README_CLOUDRUN.md` to match.
- Incremental mode dedups against existing `message_id`s and uses `MAX(date_sent)` as the cursor;
  full rescrapes are triggered with `"incremental": false`.
- Every deploy recreates the scheduler job, so scheduler edits made in the console will be overwritten;
  change `setup_scheduler.sh` instead.

## Planning context

`.planning/codebase/INTERCONNECTIONS.md` (local, untracked) maps inbound/outbound integrations;
this repo has no `.planning/STATE.md` or `ROADMAP.md`.
