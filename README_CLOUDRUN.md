# Gmail Scraper — Cloud Run

Domain-wide Gmail ingestion service. Pulls Workspace messages via the Gmail API
and writes them to BigQuery for the Envision context layer.

## Architecture

- **Cloud Run** — hosts the HTTP function (`run_scraper`); deployed with
  `--no-allow-unauthenticated`, so every invocation must carry an identity
  with `roles/run.invoker` on the service
- **BigQuery** — destination table `claude-mcp-457317.gmail_analytics.messages`
- **Gmail API** — read-only access via domain-wide delegation
- **Admin Directory API** — enumerates users in the Workspace domain
- **Cloud Scheduler** — invokes the service every 5 minutes (`gmail-scraper-5min`)
  with an OIDC token minted as the service account below
- **Workload Identity Federation (WIF)** — GitHub Actions auth to GCP, no JSON keys

## Deploy

Production deploys are fully automated. Push to `main`:

```bash
git push origin main
```

The `.github/workflows/deploy.yml` workflow then:

1. Authenticates to GCP via WIF (no key files involved). All workflow actions
   are pinned to full-length commit SHAs (org policy — unpinned actions are
   rejected before the workflow starts).
2. Deploys to Cloud Run as
   `claude-service-account@claude-mcp-457317.iam.gserviceaccount.com`, with
   `--no-allow-unauthenticated` (no public invocation).
3. Grants that service account `roles/run.invoker` on the service and fails
   the deploy if a public binding (`allUsers` / `allAuthenticatedUsers`) is
   ever present.
4. Updates the `gmail-scraper-5min` Cloud Scheduler job in place (creating it
   if missing) with OIDC authentication (see the "Update Cloud Scheduler
   (OIDC)" workflow step).

Watch a deploy:

```bash
gh run watch
```

> Do **not** run `gcloud run deploy` from a developer laptop. The source of
> truth for the live service is `main`, and the GitHub Actions pipeline is the
> only path that mutates production.

## Local development

Use Application Default Credentials — no service-account JSON file is needed
or wanted:

```bash
gcloud auth application-default login
gcloud config set project claude-mcp-457317

pip install -r requirements.txt
functions-framework --target=run_scraper --debug
```

Test locally:

```bash
curl -X POST localhost:8080 \
  -H 'Content-Type: application/json' \
  -d '{"incremental": true, "max_per_user": 10}'
```

## Service account & permissions

Runtime SA: `claude-service-account@claude-mcp-457317.iam.gserviceaccount.com`

- `roles/bigquery.dataEditor` — writes to BigQuery
- `roles/bigquery.jobUser` — runs BigQuery jobs
- `roles/run.invoker` on `gmail-scraper` — lets Cloud Scheduler invoke the
  service with an OIDC token asserting this identity (granted by the deploy
  workflow). Invocation requires minting an identity token as this SA — that
  includes Cloud Scheduler, the CI deploy identity, and any principal with
  token-creator/actAs rights on the SA. No unauthenticated caller can invoke
  it. (Follow-up hardening: a dedicated `gmail-scraper-invoker@` SA for the
  scheduler would shrink this surface, since this shared SA also carries
  BigQuery write and domain-wide delegation.)
- Domain-wide delegation scopes:
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/admin.directory.user.readonly`

If anything that resembles an SA JSON key ever ends up on disk, treat it as a
secret leak: rotate immediately in **GCP Console → IAM → Service Accounts → Keys**
and `git rm` / `git filter-repo` if it landed in the repo.

## Usage

Invocation requires authentication (`roles/run.invoker` on the service);
unauthenticated requests get `403`. For ad-hoc calls, send an identity token:

### Health check (GET)

```bash
curl https://YOUR-SERVICE-URL/ \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)"
```

```json
{
  "status": "healthy",
  "service": "gmail-scraper",
  "project": "claude-mcp-457317",
  "dataset": "gmail_analytics",
  "table": "messages",
  "mode": "incremental"
}
```

### Trigger scrape (POST)

```bash
curl -X POST https://YOUR-SERVICE-URL/ \
  -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H 'Content-Type: application/json' \
  -d '{"incremental": true, "max_per_user": 100}'
```

(Or trigger through the scheduler, which sends its own OIDC token:
`gcloud scheduler jobs run gmail-scraper-5min --project=claude-mcp-457317 --location=us-central1`.)

Parameters:

- `query` — Gmail search query (e.g. `after:2024/12/01`, `subject:RFI`)
- `max_per_user` — cap per mailbox (default `100`)
- `incremental` — only fetch new messages since last run (default `true`)

Response:

```json
{
  "status": "completed",
  "users_processed": 10,
  "total_emails": 500,
  "total_users": 10,
  "errors": []
}
```

## BigQuery schema

| Field | Type | Description |
|-------|------|-------------|
| message_id | STRING | Gmail message ID |
| thread_id | STRING | Gmail thread ID |
| user_email | STRING | Mailbox owner |
| from_address | STRING | Sender |
| to_address | STRING | Recipients |
| cc_address | STRING | CC recipients |
| bcc_address | STRING | BCC recipients |
| subject | STRING | Email subject |
| body_snippet | STRING | First 500 chars of body |
| body_text | STRING | Full plain text body |
| date_sent | TIMESTAMP | When sent |
| label_ids | STRING (REPEATED) | Gmail labels |
| is_unread | BOOLEAN | Unread flag |
| has_attachments | BOOLEAN | Has attachments |
| attachment_count | INTEGER | Number of attachments |
| size_estimate | INTEGER | Estimated size (bytes) |
| scraped_at | TIMESTAMP | Ingest time |

## Sample queries

```sql
-- Count emails by user
SELECT user_email, COUNT(*) AS email_count
FROM `claude-mcp-457317.gmail_analytics.messages`
GROUP BY user_email
ORDER BY email_count DESC;

-- Recent emails with attachments
SELECT user_email, from_address, subject, date_sent
FROM `claude-mcp-457317.gmail_analytics.messages`
WHERE has_attachments = TRUE
  AND date_sent > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
ORDER BY date_sent DESC;

-- Unread emails by user
SELECT user_email, COUNT(*) AS unread_count
FROM `claude-mcp-457317.gmail_analytics.messages`
WHERE is_unread = TRUE
GROUP BY user_email;
```

## Runtime Gmail credentials (resolved 2026-08-11)

`gmail_scraper.py` builds credentials from a service-account key file
(`SERVICE_ACCOUNT_FILE`) via `from_service_account_file`; there is no ADC
fallback, because Gmail domain-wide delegation impersonates each mailbox and
ADC cannot mint a DWD subject assertion.

The key is never committed and never baked into the image. The deploy mounts
it from Secret Manager (`gmail-scraper-sa-key`) at `/secrets/key.json` and
sets `SERVICE_ACCOUNT_FILE` to that path; see the deploy step in
`.github/workflows/deploy.yml`.

History: between 2026-05-06 and 2026-08-11 the deploy carried neither the
mount nor the env var, so every scheduled POST failed at runtime and no rows
landed in BigQuery. The 5-minute scheduler crash-looped for three months
because `alloydb_conn` was bound inside the `try`, so the `finally` raised
`UnboundLocalError` over the real `FileNotFoundError`. Both are fixed; the
mount now lives in the workflow, so a redeploy cannot silently strip it
(`env_vars`/`secrets` are passed as `--set-env-vars`/`--set-secrets`, which
replace rather than merge).

Remaining hardening option: rework `get_service_account_credentials()` for
keyless DWD (IAM `signJwt` as the runtime SA), which removes the key file
entirely. Owner decision with Workspace-admin implications; not required for
correct operation today.

Reading failures: `403` responses mean invocation auth; `status: failed`
responses mean runtime credentials.

## Troubleshooting

1. **`403` on invocation** — the caller lacks `roles/run.invoker` (or sent no
   identity token). The service does not allow unauthenticated invocation.
2. **Auth errors in scrape results** — see "Known issue" above; also verify
   domain-wide delegation is configured for the SA in the Workspace Admin
   console (Security → API controls → Domain-wide delegation).
3. **BigQuery errors** — confirm the SA has `bigquery.dataEditor` + `bigquery.jobUser`
   on `claude-mcp-457317`.
4. **Timeout errors** — Cloud Run timeout is 1h; reduce `max_per_user` or
   batch by query window.
5. **Scheduler attempt-deadline** — capped at 1800s (30m), see the
   "Update Cloud Scheduler (OIDC)" step in `.github/workflows/deploy.yml`.

## Downstream consumers

This pipeline feeds Envision-MCP:

- `query_gmail_analytics` MCP tool
- `grounding_sync_pipeline.py`
- `streaming/channels/gmail.py`
- `email_sync_locks`

Breakage here cascades — verify BigQuery row-count growth after any deploy.
