from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import bigquery
import json
import os
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

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

# AlloyDB configuration (optional — dual-write when set)
ALLOYDB_URL = os.getenv('ALLOYDB_URL', '')

def get_service_account_credentials(scopes):
    """Get base service account credentials with specific scopes."""
    return service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=scopes)


# ---------------------------------------------------------------------------
# AlloyDB helpers (dual-write, optional)
# ---------------------------------------------------------------------------

def _parse_alloydb_url(url):
    """Parse a postgres:// URL into pg8000 connect kwargs."""
    parsed = urlparse(url)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password,
    }


def get_alloydb_connection():
    """Return a pg8000 connection to AlloyDB, or None if not configured."""
    if not ALLOYDB_URL:
        return None
    try:
        import pg8000.native
        params = _parse_alloydb_url(ALLOYDB_URL)
        return pg8000.native.Connection(**params)
    except Exception as e:
        print(f"AlloyDB connection failed (non-fatal): {e}")
        return None


def ensure_alloydb_table(conn):
    """Create gmail_messages table in AlloyDB if it doesn't exist."""
    ddl = """
    CREATE TABLE IF NOT EXISTS gmail_messages (
        message_id      TEXT PRIMARY KEY,
        thread_id       TEXT,
        user_email      TEXT,
        from_address    TEXT,
        to_address      TEXT,
        cc_address      TEXT,
        bcc_address     TEXT,
        subject         TEXT,
        body_snippet    TEXT,
        body_text       TEXT,
        date_sent       TIMESTAMPTZ,
        label_ids       TEXT[],
        is_unread       BOOLEAN,
        has_attachments BOOLEAN,
        attachment_count INTEGER,
        size_estimate   INTEGER,
        scraped_at      TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_gmail_messages_user_email
        ON gmail_messages (user_email);
    CREATE INDEX IF NOT EXISTS idx_gmail_messages_date_sent
        ON gmail_messages (date_sent DESC);
    """
    try:
        conn.run(ddl)
        print("AlloyDB: gmail_messages table ensured")
    except Exception as e:
        print(f"AlloyDB: table creation skipped (may already exist): {e}")


def insert_to_alloydb(conn, rows):
    """Insert rows into AlloyDB gmail_messages with ON CONFLICT DO NOTHING."""
    if not rows or not conn:
        return 0

    inserted = 0
    for row in rows:
        try:
            # Convert label_ids list to PostgreSQL array literal
            label_ids = row.get('label_ids') or []
            if isinstance(label_ids, list):
                label_ids_pg = '{' + ','.join(f'"{l}"' for l in label_ids) + '}'
            else:
                label_ids_pg = '{}'

            conn.run(
                """
                INSERT INTO gmail_messages
                    (message_id, thread_id, user_email, from_address, to_address,
                     cc_address, bcc_address, subject, body_snippet, body_text,
                     date_sent, label_ids, is_unread, has_attachments,
                     attachment_count, size_estimate, scraped_at)
                VALUES
                    (:mid, :tid, :ue, :fa, :ta,
                     :cc, :bcc, :subj, :bs, :bt,
                     :ds::timestamptz, :lids::text[], :ur, :ha,
                     :ac, :se, :sa::timestamptz)
                ON CONFLICT (message_id) DO NOTHING
                """,
                mid=row.get('message_id'),
                tid=row.get('thread_id'),
                ue=row.get('user_email'),
                fa=row.get('from_address'),
                ta=row.get('to_address'),
                cc=row.get('cc_address'),
                bcc=row.get('bcc_address'),
                subj=row.get('subject'),
                bs=row.get('body_snippet'),
                bt=row.get('body_text'),
                ds=row.get('date_sent'),
                lids=label_ids_pg,
                ur=row.get('is_unread'),
                ha=row.get('has_attachments'),
                ac=row.get('attachment_count'),
                se=row.get('size_estimate'),
                sa=row.get('scraped_at'),
            )
            inserted += 1
        except Exception as e:
            print(f"AlloyDB: insert failed for {row.get('message_id')}: {e}")

    return inserted

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
        'total_alloydb_emails': 0,
        'skipped_duplicates': 0,
        'errors': []
    }

    try:
        # Initialize BigQuery
        print("Initializing BigQuery client...")
        bq_client = get_bigquery_client()
        table_ref = ensure_table_exists(bq_client)

        # Initialize AlloyDB (optional dual-write)
        alloydb_conn = None
        if ALLOYDB_URL:
            print("Initializing AlloyDB connection...")
            alloydb_conn = get_alloydb_connection()
            if alloydb_conn:
                ensure_alloydb_table(alloydb_conn)
                print("AlloyDB dual-write enabled")
            else:
                print("AlloyDB connection failed — continuing with BigQuery only")

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

                # Process and insert to BigQuery + AlloyDB
                if emails:
                    rows = [process_message(msg, user_email) for msg in emails]
                    inserted = insert_to_bigquery(bq_client, table_ref, rows)
                    print(f"  -> Inserted {inserted} rows to BigQuery")
                    results['total_emails'] += inserted

                    # Dual-write to AlloyDB (non-fatal)
                    if alloydb_conn:
                        try:
                            alloydb_inserted = insert_to_alloydb(alloydb_conn, rows)
                            print(f"  -> Inserted {alloydb_inserted} rows to AlloyDB")
                            results['total_alloydb_emails'] += alloydb_inserted
                        except Exception as e:
                            print(f"  -> AlloyDB write failed (non-fatal): {e}")

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
    finally:
        # Close AlloyDB connection
        if alloydb_conn:
            try:
                alloydb_conn.close()
            except Exception:
                pass

    return results


def backfill_alloydb(user_email_filter=None):
    """One-time backfill: copy all rows from BQ gmail_analytics.messages to AlloyDB.

    Args:
        user_email_filter: Optional email to filter (e.g. 'flow@envsn.com').
                          If None, backfills ALL users.
    Returns:
        Dict with backfill statistics.
    """
    if not ALLOYDB_URL:
        return {'status': 'skipped', 'reason': 'ALLOYDB_URL not set'}

    results = {'status': 'started', 'total': 0, 'inserted': 0, 'errors': []}

    try:
        bq_client = get_bigquery_client()
        alloydb_conn = get_alloydb_connection()
        if not alloydb_conn:
            return {'status': 'failed', 'reason': 'AlloyDB connection failed'}

        ensure_alloydb_table(alloydb_conn)

        # Query BQ for all messages
        table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        if user_email_filter:
            query = f"SELECT * FROM `{table_ref}` WHERE user_email = @user_email"
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("user_email", "STRING", user_email_filter)
                ]
            )
            print(f"Backfilling AlloyDB for user: {user_email_filter}")
        else:
            query = f"SELECT * FROM `{table_ref}`"
            job_config = bigquery.QueryJobConfig()
            print("Backfilling AlloyDB for ALL users")

        bq_rows = list(bq_client.query(query, job_config=job_config).result())
        results['total'] = len(bq_rows)
        print(f"Found {len(bq_rows)} rows in BigQuery to backfill")

        # Process in batches of 100
        batch_size = 100
        for i in range(0, len(bq_rows), batch_size):
            batch = bq_rows[i:i + batch_size]
            rows = []
            for bq_row in batch:
                row = {
                    'message_id': bq_row.get('message_id'),
                    'thread_id': bq_row.get('thread_id'),
                    'user_email': bq_row.get('user_email'),
                    'from_address': bq_row.get('from_address'),
                    'to_address': bq_row.get('to_address'),
                    'cc_address': bq_row.get('cc_address'),
                    'bcc_address': bq_row.get('bcc_address'),
                    'subject': bq_row.get('subject'),
                    'body_snippet': bq_row.get('body_snippet'),
                    'body_text': bq_row.get('body_text'),
                    'date_sent': bq_row.get('date_sent').isoformat() if bq_row.get('date_sent') else None,
                    'label_ids': list(bq_row.get('label_ids') or []),
                    'is_unread': bq_row.get('is_unread'),
                    'has_attachments': bq_row.get('has_attachments'),
                    'attachment_count': bq_row.get('attachment_count'),
                    'size_estimate': bq_row.get('size_estimate'),
                    'scraped_at': bq_row.get('scraped_at').isoformat() if bq_row.get('scraped_at') else None,
                }
                rows.append(row)

            inserted = insert_to_alloydb(alloydb_conn, rows)
            results['inserted'] += inserted
            print(f"  Batch {i // batch_size + 1}: inserted {inserted}/{len(batch)}")

        results['status'] = 'completed'
        results['completed_at'] = datetime.now(timezone.utc).isoformat()
        print(f"Backfill complete: {results['inserted']}/{results['total']} rows")

    except Exception as e:
        results['status'] = 'failed'
        results['error'] = str(e)
        print(f"Backfill error: {e}")
    finally:
        if alloydb_conn:
            try:
                alloydb_conn.close()
            except Exception:
                pass

    return results

if __name__ == '__main__':
    main()
