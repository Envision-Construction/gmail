# gmail — INTERCONNECTIONS

Cloud Run / Cloud Functions HTTP service (`functions_framework` entry `run_scraper` in `main.py`) that fans out per-user Gmail reads → BigQuery sink. Scrape job, not an MCP/API peer.

## Inbound
- **Cloud Scheduler** `gmail-scraper-5min` (us-central1, `*/5 * * * *`, America/New_York) → `POST $SERVICE_URL/` with body `{"incremental": true, "max_per_user": 100}`. Created by `cloudbuild.yaml` step `create-scheduler` and re-applied by `setup_scheduler.sh` / `deploy.sh`.
- **HTTP `GET /`** — health check (returns project/dataset/table).
- **HTTP `POST /`** — manual trigger (overrides `query`, `max_per_user`, `incremental`).
- Cloud Run service deployed `--allow-unauthenticated`. No IAM gate on the scheduler call; anyone with the URL can trigger a scrape.

## Outbound
- **Admin SDK Directory API** (`admin.directory.user.readonly`) — `get_all_users(ADMIN_EMAIL)` enumerates every Workspace user via DWD impersonation of `avi@envsn.com`.
- **Gmail API** (`gmail.readonly`) — for each enumerated user, SA impersonates that user (`credentials.with_subject(user_email)`) and pages `users.messages.list` + `messages.get(format=full)`.
- **BigQuery** sink — `claude-mcp-457317.gmail_analytics.messages` (17-col schema in `ensure_table_exists`). Dedup via `get_existing_message_ids` per `user_email`; incremental cursor via `MAX(date_sent)` → Gmail `after:<epoch>` filter.

## Data dependencies
- BigQuery `gmail_analytics.messages` is both source-of-truth for the cursor and the write target — losing the table = full re-scrape of every mailbox.
- No AlloyDB / Spanner / GCS path. `body_text` truncated to 65,535 chars before insert.

## Auth surfaces
- **Service account**: `claude-service-account@claude-mcp-457317.iam.gserviceaccount.com` (Cloud Run runtime + scopes-on-demand via JSON key file `service-account-key.json` copied in by `deploy.sh` from `$HOME/claude-mcp-457317-069a2a199017.json`).
- **DWD scopes used**: `gmail.readonly`, `admin.directory.user.readonly`, `bigquery`. SA must have domain-wide delegation for Gmail + Admin scopes — distinct from personal-context's SA `101345639247391420856` which (per portfolio/auth.md) has only `contacts`+`directory` DWD grants.
- **Admin impersonation principal**: env `ADMIN_EMAIL=avi@envsn.com` (defaulted in code AND baked into `cloudbuild.yaml` / `deploy.sh`). Project-memory rule applies — this service is hard-pinned to `avi@envsn.com` for the directory enumeration; the per-user Gmail reads then impersonate every primary email returned.

## Deploy contract
- `cloudbuild.yaml` is the canonical path: builds via `--source .`, deploys Cloud Run (us-central1, 2Gi/2cpu, max-instances=1, timeout 3600), then deletes+recreates the scheduler. No GitHub trigger verified — assume manual `gcloud builds submit` until `gcloud builds triggers list` confirms.
- `deploy.sh` does the same end-to-end from a workstation but additionally copies a local SA key into the build context (key-file auth path).
- `setup_scheduler.sh` is scheduler-only re-apply (idempotent delete+create); comment claims "hourly" but actually `*/5 * * * *`.
- All three scripts hardcode `gmail-scraper-5min`, `us-central1`, `claude-mcp-457317`. Drift risk: `cloudbuild.yaml` uses `--allow-unauthenticated`; if rotated to authenticated, scheduler needs an OIDC token block — not currently configured.

## Cross-repo claims
- **portfolio/data.md row 6** lists `envision_data` / `envision_audit` as the project's BQ datasets and names `gmail_search_bigquery` (slackwrapper) as a consumer — but `gmail_analytics.messages` (this service's sink) is **not enumerated** in that table. Either the portfolio is incomplete or `gmail_search_bigquery` reads a different dataset; flag for portfolio reconciliation.
- **portfolio/auth.md** asserts Gmail scopes are NOT in the personal-context DWD grant. This service uses a separate SA (`claude-service-account`) which therefore must hold the Gmail + Admin Directory DWD grants independently — second DWD-authorized client in the project.
- **portfolio/runtime.md** shows `MCP -- DWD service account --> Gmail` and `COMMS --> Gmail` edges; this gmail-scraper is a third, parallel Gmail consumer that bypasses Envision-MCP entirely and writes its own BQ table.
- No edges to Envision-MCP, slackwrapper, central-command, or personal-context. Standalone ingestor.
