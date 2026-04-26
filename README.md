# Gmail Scraper (Context Layer Ingestion)

This service ingests domain-wide Gmail into BigQuery for the Envision context
layer.

Deploys are automated: push to `main` triggers `.github/workflows/deploy.yml`
which uses Workload Identity Federation (no service-account JSON keys) to ship
to Cloud Run, then re-creates the `gmail-scraper-5min` scheduler.

For local dev, use `gcloud auth application-default login` — never copy SA
key files into this repo.

Operational details, BigQuery schema, and downstream MCP consumers live in
`README_CLOUDRUN.md`.
