#!/bin/bash
# Gmail Scraper deployment notes (informational only).
#
# Production deploys are AUTOMATED via GitHub Actions on push to `main`.
# See .github/workflows/deploy.yml — uses Workload Identity Federation (WIF),
# no service-account JSON keys are required or committed. Org policy requires
# every action in the workflow to be pinned to a full-length commit SHA.
#
# The workflow deploys Cloud Run with --no-allow-unauthenticated: invoking
# the service requires roles/run.invoker. Cloud Scheduler is the only caller
# and authenticates with an OIDC token minted as
#   claude-service-account@claude-mcp-457317.iam.gserviceaccount.com
# (the same SA the service runs as; the workflow grants it run.invoker and
# updates the `gmail-scraper-5min` job in place with OIDC on every deploy).
#
# NOTE (runtime Gmail auth, known issue): gmail_scraper.py loads a service
# account KEY FILE (SERVICE_ACCOUNT_FILE, default ./service-account-key.json)
# and has no ADC fallback. The image ships no key and the deploy mounts no
# secret, so scrape POSTs currently fail at runtime with status "failed".
# See "Known issue" in README_CLOUDRUN.md before touching auth code.
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
# Manual scheduler / service invocation (rare)
# ---------------------------------------------------------------------------
# The scheduler job is created/updated by the deploy workflow (see the
# "Update Cloud Scheduler (OIDC)" step in .github/workflows/deploy.yml).
# Trigger an ad-hoc run through the scheduler:
#
#   gcloud scheduler jobs run gmail-scraper-5min \
#     --project=claude-mcp-457317 --location=us-central1
#
# Or call the service directly with an identity token (requires run.invoker):
#
#   curl -X POST "$SERVICE_URL/" \
#     -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
#     -H 'Content-Type: application/json' \
#     -d '{"incremental": true, "max_per_user": 10}'
#
echo "deploy.sh is informational only — see comments in this file."
echo "Production deploys run via .github/workflows/deploy.yml on push to main."
exit 0
