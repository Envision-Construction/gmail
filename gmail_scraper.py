from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import bigquery
import json
import os
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Service account configuration
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE', 'service-account-key.json')

# Scopes definitions
BQ_SCOPES = ['https://www.googleapis.com/auth/bigquery']
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
ADMIN_SCOPES = ['https://www.googleapis.com/auth/admin.directory.user.readonly']

# BigQuery configuration
PROJECT_ID = os.getenv('PROJECT_ID', 'claude-mcp-457317')
DATASET_ID = os.getenv('DATASET_ID', 'gmail_analytics')
TABLE_ID = os.getenv('TABLE_ID', 'messages')

def get_service_account_credentials(scopes):
    """Get base service account credentials with specific scopes."""
    return service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)

def get_bigquery_client():
    """Get BigQuery client using service account identity."""
    credentials = get_service_account_credentials(BQ_SCOPES)
    return bigquery.Client(project=PROJECT_ID, credentials=credentials)

def ensure_table_exists(client):
    """Ensure the BigQuery table exists with proper schema."""
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    schema = [
        bigquery.SchemaField("message_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("thread_id", "STRING"),
        bigquery.SchemaField("user_email", "STRING"),
        bigquery.SchemaField("from_address", "STRING"),
        bigquery.SchemaField("to_address", "STRING"),
        bigquery.SchemaField("cc_address", "STRING"),
        bigquery.SchemaField("bcc_address", "STRING"),
        bigquery.SchemaField("subject", "STRING"),
        bigquery.SchemaField("body_snippet", "STRING"),
        bigquery.SchemaField("body_text", "STRING"),
        bigquery.SchemaField("date_sent", "TIMESTAMP"),
        bigquery.SchemaField("label_ids", "STRING", mode="REPEATED"),
        bigquery.SchemaField("is_unread", "BOOLEAN"),
        bigquery.SchemaField("has_attachments", "BOOLEAN"),
        bigquery.SchemaField("attachment_count", "INTEGER"),
        bigquery.SchemaField("size_estimate", "INTEGER"),
        bigquery.SchemaField("scraped_at", "TIMESTAMP"),
    ]

    table = bigquery.Table(table_ref, schema=schema)

    try:
        client.get_table(table_ref)
        print(f"Table {table_ref} already exists")
    except Exception:
        table = client.create_table(table)
        print(f"Created table {table_ref}")

    return table_ref

def get_existing_message_ids(client, user_email=None):
    """Get set of existing message IDs from BigQuery to avoid duplicates."""
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    if user_email:
        query = f"""
            SELECT DISTINCT message_id
            FROM `{table_ref}`
            WHERE user_email = @user_email
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_email", "STRING", user_email)
            ]
        )
    else:
        query = f"SELECT DISTINCT message_id FROM `{table_ref}`"
        job_config = bigquery.QueryJobConfig()

    try:
        results = client.query(query, job_config=job_config).result()
        return {row.message_id for row in results}
    except Exception as e:
        print(f"Error fetching existing message IDs: {e}")
        return set()

def get_last_scrape_timestamp(client, user_email=None):
    """Get the timestamp of the most recently scraped email."""
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    if user_email:
        query = f"""
            SELECT MAX(date_sent) as last_date
            FROM `{table_ref}`
            WHERE user_email = @user_email
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_email", "STRING", user_email)
            ]
        )
    else:
        query = f"SELECT MAX(date_sent) as last_date FROM `{table_ref}`"
        job_config = bigquery.QueryJobConfig()

    try:
        results = client.query(query, job_config=job_config).result()
        for row in results:
            return row.last_date
    except Exception as e:
        print(f"Error fetching last scrape timestamp: {e}")
    return None

def build_incremental_query(base_query, last_timestamp):
    """Build Gmail query that only fetches emails after the last scraped timestamp."""
    if last_timestamp:
        # Add 1 second buffer and format for Gmail query
        # Gmail 'after:' uses epoch seconds
        after_timestamp = int(last_timestamp.timestamp())
        incremental_filter = f"after:{after_timestamp}"

        if base_query:
            return f"{base_query} {incremental_filter}"
        return incremental_filter
    return base_query

def get_header_value(headers, name):
    """Extract a header value from message headers."""
    for header in headers:
        if header['name'].lower() == name.lower():
            return header['value']
    return None

def get_body_text(payload):
    """Extract plain text body from message payload."""
    if 'body' in payload and 'data' in payload['body']:
        return base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')

    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'body' in part and 'data' in part['body']:
                return base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
            elif 'parts' in part:
                text = get_body_text(part)
                if text:
                    return text
    return None

def parse_email_date(date_str):
    """Parse email date string to datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None

def process_message(message, user_email):
    """Process a Gmail message into a BigQuery row."""
    headers = message.get('payload', {}).get('headers', [])
    payload = message.get('payload', {})

    # Count attachments
    attachment_count = 0
    has_attachments = False
    if 'parts' in payload:
        for part in payload['parts']:
            if part.get('filename'):
                attachment_count += 1
                has_attachments = True

    # Parse date
    date_str = get_header_value(headers, 'Date')
    date_sent = parse_email_date(date_str)

    # Check if unread
    label_ids = message.get('labelIds', [])
    is_unread = 'UNREAD' in label_ids

    # Get body text (truncate to avoid BigQuery limits)
    body_text = get_body_text(payload)
    if body_text and len(body_text) > 65535:
        body_text = body_text[:65535]

    return {
        'message_id': message['id'],
        'thread_id': message.get('threadId'),
        'user_email': user_email,
        'from_address': get_header_value(headers, 'From'),
        'to_address': get_header_value(headers, 'To'),
        'cc_address': get_header_value(headers, 'Cc'),
        'bcc_address': get_header_value(headers, 'Bcc'),
        'subject': get_header_value(headers, 'Subject'),
        'body_snippet': message.get('snippet', '')[:500] if message.get('snippet') else None,
        'body_text': body_text,
        'date_sent': date_sent.isoformat() if date_sent else None,
        'label_ids': label_ids,
        'is_unread': is_unread,
        'has_attachments': has_attachments,
        'attachment_count': attachment_count,
        'size_estimate': message.get('sizeEstimate'),
        'scraped_at': datetime.now(timezone.utc).isoformat(),
    }

def insert_to_bigquery(client, table_ref, rows):
    """Insert rows into BigQuery table."""
    if not rows:
        return 0

    errors = client.insert_rows_json(table_ref, rows)
    if errors:
        print(f"BigQuery insert errors: {errors}")
        return 0
    return len(rows)

def get_all_users(admin_email):
    """Get all users in the Google Workspace domain."""
    # Use Admin SDK scopes for this operation
    credentials = get_service_account_credentials(ADMIN_SCOPES)
    delegated_creds = credentials.with_subject(admin_email)
    admin_service = build('admin', 'directory_v1', credentials=delegated_creds)

    users = []
    page_token = None

    while True:
        results = admin_service.users().list(
            customer='my_customer',
            maxResults=500,
            pageToken=page_token
        ).execute()

        users.extend(results.get('users', []))
        page_token = results.get('nextPageToken')

        if not page_token:
            break

    return [user['primaryEmail'] for user in users]

def scrape_user_emails(user_email, query='', max_results=100, existing_ids=None):
    """Scrape emails for a specific user, skipping already-scraped messages."""
    # Use Gmail scopes for this operation
    credentials = get_service_account_credentials(GMAIL_SCOPES)
    delegated_creds = credentials.with_subject(user_email)
    gmail_service = build('gmail', 'v1', credentials=delegated_creds)

    messages = []
    page_token = None
    fetched = 0
    skipped = 0
    existing_ids = existing_ids or set()

    try:
        while fetched < max_results:
            results = gmail_service.users().messages().list(
                userId='me',
                q=query,
                maxResults=min(100, max_results - fetched + 50),  # Fetch extra to account for skips
                pageToken=page_token
            ).execute()

            if 'messages' in results:
                for msg in results['messages']:
                    if fetched >= max_results:
                        break

                    # Skip if already in BigQuery
                    if msg['id'] in existing_ids:
                        skipped += 1
                        continue

                    # Get full message details
                    message = gmail_service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='full'
                    ).execute()
                    messages.append(message)
                    fetched += 1

            page_token = results.get('nextPageToken')
            if not page_token:
                break

    except Exception as e:
        print(f"Error scraping {user_email}: {str(e)}")

    if skipped > 0:
        print(f"  -> Skipped {skipped} already-scraped messages")

    return messages

def main(query='', max_per_user=100, incremental=True):
    """Main function to scrape emails and store in BigQuery.

    Args:
        query: Gmail search query (e.g., 'subject:RFI')
        max_per_user: Maximum new emails to scrape per user
        incremental: If True, only fetch emails newer than last scrape
    """
    ADMIN_EMAIL = os.getenv('ADMIN_EMAIL', 'avi@envsn.com')

    results = {
        'status': 'started',
        'mode': 'incremental' if incremental else 'full',
        'users_processed': 0,
        'total_emails': 0,
        'skipped_duplicates': 0,
        'errors': []
    }

    try:
        # Initialize BigQuery
        print("Initializing BigQuery client...")
        bq_client = get_bigquery_client()
        table_ref = ensure_table_exists(bq_client)

        # Get all users
        print(f"Fetching all users (admin: {ADMIN_EMAIL})...")
        all_users = get_all_users(ADMIN_EMAIL)
        print(f"Found {len(all_users)} users")
        results['total_users'] = len(all_users)

        # Scrape emails for each user
        for user_email in all_users:
            print(f"Scraping emails for {user_email}...")

            try:
                # Get existing message IDs for this user (for deduplication)
                existing_ids = get_existing_message_ids(bq_client, user_email)
                print(f"  -> Found {len(existing_ids)} existing messages in BigQuery")

                # Build incremental query if enabled
                effective_query = query
                if incremental:
                    last_timestamp = get_last_scrape_timestamp(bq_client, user_email)
                    if last_timestamp:
                        effective_query = build_incremental_query(query, last_timestamp)
                        print(f"  -> Incremental mode: fetching emails after {last_timestamp}")
                    else:
                        print(f"  -> No previous scrape found, doing full scrape")

                # Scrape new emails
                emails = scrape_user_emails(
                    user_email,
                    query=effective_query,
                    max_results=max_per_user,
                    existing_ids=existing_ids
                )
                print(f"  -> Found {len(emails)} new emails")

                # Process and insert to BigQuery
                if emails:
                    rows = [process_message(msg, user_email) for msg in emails]
                    inserted = insert_to_bigquery(bq_client, table_ref, rows)
                    print(f"  -> Inserted {inserted} rows to BigQuery")
                    results['total_emails'] += inserted

                results['users_processed'] += 1

            except Exception as e:
                error_msg = f"Error processing {user_email}: {str(e)}"
                print(error_msg)
                results['errors'].append(error_msg)

        results['status'] = 'completed'
        results['completed_at'] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        results['status'] = 'failed'
        results['error'] = str(e)
        print(f"Fatal error: {e}")

    return results

if __name__ == '__main__':
    main()
