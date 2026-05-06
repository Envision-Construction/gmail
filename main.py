import functions_framework
import json
import os
from gmail_scraper import main as scraper_main, backfill_alloydb

@functions_framework.http
def run_scraper(request):
    """HTTP Cloud Function to trigger the Gmail scraper.

    Routes:
    - GET /: Health check
    - POST /: Trigger scraper with optional parameters
    - POST / with {"action": "backfill"}: Backfill AlloyDB from BigQuery

    POST body for scrape (default):
    {
        "query": "subject:RFI",     # Gmail search query
        "max_per_user": 100,        # Max emails per user
        "incremental": true         # Only fetch new emails (default: true)
    }

    POST body for backfill:
    {
        "action": "backfill",
        "user_email": "flow@envsn.com"   # Optional: backfill single user
    }
    """
    # Handle health check
    if request.method == 'GET':
        return json.dumps({
            'status': 'healthy',
            'service': 'gmail-scraper',
            'project': os.getenv('PROJECT_ID', 'claude-mcp-457317'),
            'dataset': os.getenv('DATASET_ID', 'gmail_analytics'),
            'table': os.getenv('TABLE_ID', 'messages'),
            'alloydb': 'enabled' if os.getenv('ALLOYDB_URL') else 'disabled',
            'mode': 'incremental'
        }), 200, {'Content-Type': 'application/json'}

    try:
        request_json = request.get_json(silent=True) or {}

        # Route: backfill AlloyDB from BigQuery
        if request_json.get('action') == 'backfill':
            user_email = request_json.get('user_email')
            print(f"Starting backfill: user_email={user_email or 'ALL'}")
            results = backfill_alloydb(user_email_filter=user_email)
            return json.dumps(results, default=str), 200, {'Content-Type': 'application/json'}

        # Route: normal scrape (default)
        query = request_json.get('query', '')
        max_per_user = request_json.get('max_per_user', 100)
        incremental = request_json.get('incremental', True)

        print(f"Starting scraper: query='{query}', max_per_user={max_per_user}, incremental={incremental}")

        results = scraper_main(
            query=query,
            max_per_user=max_per_user,
            incremental=incremental
        )

        return json.dumps(results, default=str), 200, {'Content-Type': 'application/json'}

    except Exception as e:
        print(f"Error running scraper: {e}")
        import traceback
        traceback.print_exc()
        return json.dumps({
            'status': 'error',
            'error': str(e)
        }), 500, {'Content-Type': 'application/json'}
