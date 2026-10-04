# gmail review standards

gmail is the scheduled Cloud Run job that copies every Workspace mailbox into a BigQuery table through
domain-wide delegation. It is not an API other services call and not a mail client: it never sends or
changes mail, and downstream services read the table directly.

## Invariants that need explanation
- A scrape failure shows up in the response body, never in the HTTP status. Why: `main` in
  `gmail_scraper.py` catches every exception and `run_scraper` in `main.py` answers 200 with
  `status: failed`, so the scheduler records success while no rows land. Source: `main.py`,
  `gmail_scraper.py`. A violating diff adds an alert, retry policy, probe or doc that treats a 200 as
  proof that mail was ingested.
- Invocation auth and runtime auth are two separate credentials. Why: a caller passes Cloud Run IAM
  with an OIDC identity token, while the scraper signs delegation assertions with a mounted key file; a
  403 points at the first and `status: failed` at the second. Source: "Runtime Gmail credentials" in
  `README_CLOUDRUN.md`. A violating diff deletes the key because the workflow already authenticates
  through federation, or loosens invocation to work around a credential error.
- The incremental cursor comes from the sender's `Date` header. Why: `process_message` derives
  `date_sent` with `parse_email_date`, and `get_last_scrape_timestamp` takes its per-user maximum as the
  Gmail `after:` bound, so changing how `date_sent` is parsed or stored shifts which mail every later
  incremental run fetches. Source: `gmail_scraper.py`. A violating diff changes date parsing or timezone
  handling without stating how existing cursors move.
- AlloyDB is an optional second sink. Why: it activates only when `ALLOYDB_URL` is set, the deploy
  workflow does not set it, and each AlloyDB failure is caught and logged so BigQuery ingestion goes on.
  Source: `get_alloydb_connection` and `insert_to_alloydb` in `gmail_scraper.py`. A violating diff lets
  an AlloyDB error abort the scrape, or treats AlloyDB as the system of record.

## Generated or vendored (review the generator, not the output)
- None. Every tracked file is hand-written; `deploy.sh` is notes, not a deploy path.
