#!/bin/bash
# Cloud Scheduler Setup Script for Gmail Scraper
# This creates an hourly scheduled job that triggers the incremental scraper
set -e

# Configuration
PROJECT_ID="claude-mcp-457317"
REGION="us-central1"
JOB_NAME="gmail-scraper-5min"
SERVICE_NAME="gmail-scraper"

echo "=== Cloud Scheduler Setup for Gmail Scraper ==="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Job Name: $JOB_NAME"
echo ""

# Get the Cloud Run service URL
echo "Step 1: Getting Cloud Run service URL..."
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
  --project=$PROJECT_ID \
  --region=$REGION \
  --format='value(status.url)')

if [ -z "$SERVICE_URL" ]; then
  echo "ERROR: Could not get Cloud Run service URL. Make sure the service is deployed."
  exit 1
fi

echo "Service URL: $SERVICE_URL"
echo ""

# Create or update the scheduler job
echo "Step 2: Creating/Updating Cloud Scheduler job..."

# Delete existing job if it exists (to update it)
gcloud scheduler jobs delete $JOB_NAME \
  --project=$PROJECT_ID \
  --location=$REGION \
  --quiet 2>/dev/null || true

# Create the scheduler job
# Runs every 5 minutes
gcloud scheduler jobs create http $JOB_NAME \
  --project=$PROJECT_ID \
  --location=$REGION \
  --schedule="*/5 * * * *" \
  --time-zone="America/New_York" \
  --uri="${SERVICE_URL}/" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"incremental": true, "max_per_user": 100}' \
  --attempt-deadline=1800s \
  --description="Hourly incremental Gmail scrape to BigQuery"

echo ""
echo "=== Cloud Scheduler Setup Complete ==="
echo ""
echo "Job Details:"
echo "  Name: $JOB_NAME"
echo "  Schedule: Every hour at minute 0 (0 * * * *)"
echo "  Timezone: America/New_York"
echo "  Target: $SERVICE_URL"
echo ""
echo "To manually trigger the job:"
echo "  gcloud scheduler jobs run $JOB_NAME --project=$PROJECT_ID --location=$REGION"
echo ""
echo "To view job status:"
echo "  gcloud scheduler jobs describe $JOB_NAME --project=$PROJECT_ID --location=$REGION"
echo ""
echo "To view recent runs:"
echo "  gcloud scheduler jobs list --project=$PROJECT_ID --location=$REGION"
echo ""
