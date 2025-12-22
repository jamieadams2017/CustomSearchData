import os
import json
import time
import requests
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials

CSE_API_KEY = os.environ["CSE_API_KEY"]
CSE_ID = os.environ["CSE_ID"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")

# Comma-separated queries in env, or single QUERY
QUERIES = os.environ.get("QUERIES")
if QUERIES:
    QUERIES = [q.strip() for q in QUERIES.split(",") if q.strip()]
else:
    QUERIES = [os.environ.get("QUERY", "site:example.com")]

# credentials.json content stored in secret as JSON string
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]


def cse_search(query: str, max_results: int = 100):
    """
    Fetch up to max_results from Google Programmable Search Engine (CSE).
    Uses dateRestrict=d1 to bias to last 1 day (approx last 24 hours).
    """
    results = []
    start = 1
    while start <= 91 and len(results) < max_results:
        params = {
            "key": CSE_API_KEY,
            "cx": CSE_ID,
            "q": query,
            "start": start,
            "num": 10,
            "dateRestrict": "d1",  # last 1 day
        }
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=30)
        if r.status_code == 429:
            # simple backoff
            time.sleep(5)
            continue
        r.raise_for_status()
        data = r.json()

        items = data.get("items", [])
        if not items:
            break

        for it in items:
            results.append({
                "title": it.get("title", ""),
                "link": it.get("link", ""),
                "snippet": it.get("snippet", ""),
            })

        start += 10
        time.sleep(0.2)  # polite pacing

    return results[:max_results]


def get_gspread_client():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def ensure_headers(ws):
    headers = ["Date", "Query", "Title", "Link", "Snippet"]
    existing = ws.row_values(1)
    if existing != headers:
        ws.clear()
        ws.append_row(headers)


def load_existing_links(ws):
    # assumes Link is column 4
    links = ws.col_values(4)
    # remove header
    return set(l.strip() for l in links[1:] if l.strip())


def main():
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet(SHEET_NAME)

    ensure_headers(ws)
    existing_links = load_existing_links(ws)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    rows_to_add = []
    for q in QUERIES:
        items = cse_search(q, max_results=100)
        for it in items:
            link = (it.get("link") or "").strip()
            if not link or link in existing_links:
                continue
            rows_to_add.append([now_utc, q, it.get("title", ""), link, it.get("snippet", "")])
            existing_links.add(link)

    if rows_to_add:
        ws.append_rows(rows_to_add, value_input_option="RAW")
        print(f"Added {len(rows_to_add)} new rows.")
    else:
        print("No new rows to add.")


if __name__ == "__main__":
    main()

