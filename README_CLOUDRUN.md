# Gmail Scraper — Cloud Run

Domain-wide Gmail ingestion service. Pulls Workspace messages via the Gmail API
and writes them to BigQuery for the Envision context layer.

## Architecture

- **Cloud Run** — hosts the HTTP function (`run_scraper`)
- **BigQuery** — destination table `claude-mcp-457317.gmail_analytics.messages`
- **Gmail API** — read-only access via domain-wide delegation
- **Admin Directory API** — enumerates users in the Workspace domain
- **Cloud Scheduler** — invokes the service every 5 minutes (`gmail-scraper-5min`)
- **Secret Manager** — holds any credentials required for delegated impersonation
- **Workload Identity Federation (WIF)** — GitHub Actions auth to GCP, no JSON keys

## Deploy

Production deploys are fully automated. Push to `main`:

```bash
git push origin main
```

The `.github/workflows/deploy.yml` workflow then:

1. Authenticates to GCP via WIF (no key files involved).
2. Deploys to Cloud Run as
   `claude-service-account@claude-mcp-457317.iam.gserviceaccount.com`.
3. Re-creates the `gmail-scraper-5min` Cloud Scheduler job via
   `setup_scheduler.sh`.

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
- Domain-wide delegation scopes:
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/admin.directory.user.readonly`

If anything that resembles an SA JSON key ever ends up on disk, treat it as a
secret leak: rotate immediately in **GCP Console → IAM → Service Accounts → Keys**
and `git rm` / `git filter-repo` if it landed in the repo.

## Usage

### Health check (GET)

```bash
curl https://YOUR-SERVICE-URL/
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
  -H 'Content-Type: application/json' \
  -d '{"incremental": true, "max_per_user": 100}'
```

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

## Troubleshooting

1. **Auth errors** — verify domain-wide delegation is configured for the SA in
   the Workspace Admin console (Security → API controls → Domain-wide delegation).
2. **BigQuery errors** — confirm the SA has `bigquery.dataEditor` + `bigquery.jobUser`
   on `claude-mcp-457317`.
3. **Timeout errors** — Cloud Run timeout is 1h; reduce `max_per_user` or
   batch by query window.
4. **Scheduler attempt-deadline** — capped at 1800s (30m), see
   `setup_scheduler.sh`.

## Downstream consumers

This pipeline feeds Envision-MCP:

- `query_gmail_analytics` MCP tool
- `grounding_sync_pipeline.py`
- `streaming/channels/gmail.py`
- `email_sync_locks`

Breakage here cascades — verify BigQuery row-count growth after any deploy.
