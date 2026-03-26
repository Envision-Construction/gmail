#!/bin/bash
# Gmail Scraper Cloud Run Deployment Script with Hourly Scheduler
set -e

# Configuration
PROJECT_ID="claude-mcp-457317"
SERVICE_NAME="gmail-scraper"
REGION="us-central1"
SERVICE_ACCOUNT_EMAIL="claude-service-account@claude-mcp-457317.iam.gserviceaccount.com"
SERVICE_ACCOUNT_KEY_FILE="$HOME/claude-mcp-457317-069a2a199017.json"
ADMIN_EMAIL="avi@envsn.com"
SCHEDULER_JOB_NAME="gmail-scraper-5min"

echo "=== Gmail Scraper Cloud Run Deployment ==="
echo "Project: $PROJECT_ID"
echo "Service: $SERVICE_NAME"
echo "Region: $REGION"
echo ""

# Step 1: Copy service account key to project directory
echo "Step 1: Copying service account key..."
cp "$SERVICE_ACCOUNT_KEY_FILE" ./service-account-key.json

# Step 2: Build and deploy
echo "Step 2: Building and deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --source . \
  --project=$PROJECT_ID \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --service-account=$SERVICE_ACCOUNT_EMAIL \
  --set-env-vars="PROJECT_ID=$PROJECT_ID,DATASET_ID=gmail_analytics,TABLE_ID=messages,ADMIN_EMAIL=$ADMIN_EMAIL" \
  --timeout=3600 \
  --memory=2Gi \
  --cpu=2 \
  --max-instances=1

# Step 3: Get the service URL
echo ""
echo "Step 3: Getting service URL..."
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
  --project=$PROJECT_ID \
  --region=$REGION \
  --format='value(status.url)')

echo "Service URL: $SERVICE_URL"

# Step 4: Set up Cloud Scheduler for hourly incremental scraping
echo ""
echo "Step 4: Setting up 5-minute Cloud Scheduler job..."

# Delete existing job if it exists
gcloud scheduler jobs delete $SCHEDULER_JOB_NAME \
  --project=$PROJECT_ID \
  --location=$REGION \
  --quiet 2>/dev/null || true

# Create new scheduler job (runs every 5 minutes)
gcloud scheduler jobs create http $SCHEDULER_JOB_NAME \
  --project=$PROJECT_ID \
  --location=$REGION \
  --schedule="*/5 * * * *" \
  --time-zone="America/New_York" \
  --uri="${SERVICE_URL}/" \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --message-body='{"incremental": true, "max_per_user": 100}' \
  --attempt-deadline=3600s \
  --description="5-minute incremental Gmail scrape to BigQuery"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Cloud Run Service:"
echo "  URL: $SERVICE_URL"
echo ""
echo "Cloud Scheduler Job:"
echo "  Name: $SCHEDULER_JOB_NAME"
echo "  Schedule: Every 5 minutes (*/5 * * * *)"
echo "  Timezone: America/New_York"
echo "  Mode: Incremental (only new messages)"
echo ""
echo "Manual Commands:"
echo ""
echo "  # Trigger scrape manually:"
echo "  curl -X POST $SERVICE_URL/ \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"incremental\": true, \"max_per_user\": 100}'"
echo ""
echo "  # Full scrape (all messages, not just new):"
echo "  curl -X POST $SERVICE_URL/ \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"incremental\": false, \"max_per_user\": 500}'"
echo ""
echo "  # Trigger scheduler job manually:"
echo "  gcloud scheduler jobs run $SCHEDULER_JOB_NAME --project=$PROJECT_ID --location=$REGION"
echo ""
