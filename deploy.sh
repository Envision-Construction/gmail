#!/bin/bash
# Gmail Scraper deployment notes (informational only).
#
# Production deploys are AUTOMATED via GitHub Actions on push to `main`.
# See .github/workflows/deploy.yml — uses Workload Identity Federation (WIF),
# no service-account JSON keys are required or committed.
#
# Cloud Run uses the service account via attached identity:
#   claude-service-account@claude-mcp-457317.iam.gserviceaccount.com
# Secrets (e.g. SA tokens needed for domain-wide delegation) are read at
# runtime from Secret Manager, not from disk.
#
# DO NOT run `gcloud run deploy` from a developer laptop in production —
# the source of truth is `main`, and the deploy pipeline is the only path
# that updates the live service.
#
# ---------------------------------------------------------------------------
# Local development
# ---------------------------------------------------------------------------
# Use Application Default Credentials (no key files):
#
#   gcloud auth application-default login
#   gcloud config set project claude-mcp-457317
#
# Then run the function locally:
#
#   functions-framework --target=run_scraper --debug
#
# Test:
#
#   curl -X POST localhost:8080 \
#     -H 'Content-Type: application/json' \
#     -d '{"incremental": true, "max_per_user": 10}'
#
# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------
# To deploy, push to main:
#
#   git push origin main
#
# Watch the run:
#
#   gh run watch
#
# ---------------------------------------------------------------------------
# Manual scheduler updates (rare)
# ---------------------------------------------------------------------------
# The scheduler is created/updated by the deploy workflow via setup_scheduler.sh.
# Trigger an ad-hoc run:
#
#   gcloud scheduler jobs run gmail-scraper-5min \
#     --project=claude-mcp-457317 --location=us-central1
#
echo "deploy.sh is informational only — see comments in this file."
echo "Production deploys run via .github/workflows/deploy.yml on push to main."
exit 0
