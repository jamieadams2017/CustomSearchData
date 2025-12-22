import os
import json
import time
import re
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials


# =========================
# CONFIG
# =========================
CSE_API_KEY = os.environ["CSE_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# Your two CSEs and destination tabs
CSE_CONFIGS = [
    {"cse_id": "628862614b5d44b5b", "query": "Bangladesh", "sheet": "IND_Media"},
    {"cse_id": "c0c3126bab16f48c6", "query": "Bangladesh", "sheet": "PAK_Media"},
]

# Networking
UA = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)
HTTP_TIMEOUT = 20
PAGE_FETCH_LIMIT = 40  # max pages per run to fetch HTML for publish date (keeps runs fast)


# =========================
# GOOGLE SHEETS
# =========================
def get_gspread_client():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def get_or_create_worksheet(sh, title, rows=3000, cols=10):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_headers(ws):
    # No Query column. Added SourceDomain + PublishedDate.
    headers = ["RunTime", "Title", "Link", "SourceDomain", "PublishedDate", "Snippet"]
    if ws.row_values(1) != headers:
        ws.clear()
        ws.append_row(headers)


def load_existing_links(ws):
    # Link column is 3
    links = ws.col_values(3)
    return set(l.strip() for l in links[1:] if l.strip())


# =========================
# HELPERS
# =========================
def source_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def to_date_yyyy_mm_dd(value: str) -> str:
    """
    Best-effort parse of a date/time string to YYYY-MM-DD.
    Handles ISO-like strings: 2025-12-22T11:56:47Z, 2025-12-22, etc.
    """
    if not value:
        return ""

    s = value.strip()

    # Quick accept: YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m:
        return s

    # Extract a leading YYYY-MM-DD from longer strings
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)

    # Some sites do: 22 Dec 2025 etc — avoid heavy deps; keep it simple.
    # If you need broader parsing, tell me and I’ll add python-dateutil parsing.
    return ""


def extract_jsonld_publish_date(html: str) -> str:
    """
    Look for JSON-LD blocks and try to find datePublished/dateCreated.
    """
    # Grab all JSON-LD script contents
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for b in blocks:
        b = b.strip()
        if not b:
            continue
        # Some pages have multiple JSON objects/arrays; try to load robustly.
        try:
            data = json.loads(b)
        except Exception:
            # Sometimes invalid JSON due to trailing commas, etc. Skip.
            continue

        candidates = []

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = k.lower()
                    if lk in ("datepublished", "datecreated", "dateissued"):
                        if isinstance(v, str):
                            candidates.append(v)
                    walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)

        walk(data)

        for c in candidates:
            d = to_date_yyyy_mm_dd(c)
            if d:
                return d

    return ""


def extract_meta_publish_date(html: str) -> str:
    """
    Try common meta patterns:
    - property="article:published_time"
    - name="pubdate" / "publishdate" / "date" / "DC.date.issued" etc
    """
    patterns = [
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']pubdate["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']publishdate["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']date["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']dc\.date\.issued["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:updated_time["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for p in patterns:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            d = to_date_yyyy_mm_dd(m.group(1))
            if d:
                return d
    return ""


def fetch_publish_date(url: str, session: requests.Session) -> str:
    """
    Fetch HTML and extract publish date via JSON-LD/meta.
    Returns YYYY-MM-DD or "" if not found.
    """
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        html = r.text

        d = extract_jsonld_publish_date(html)
        if d:
            return d

        d = extract_meta_publish_date(html)
        if d:
            return d

        return ""
    except Exception:
        return ""


# =========================
# CSE SEARCH
# =========================
def cse_search(query, cse_id, max_results=100):
    results = []
    start = 1

    while start <= 91 and len(results) < max_results:
        params = {
            "key": CSE_API_KEY,
            "cx": cse_id,
            "q": query,
            "num": 10,
            "start": start,
            "dateRestrict": "d1",  # last ~24 hours (CSE approximation)
        }

        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=30)

        if r.status_code == 429:
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
        time.sleep(0.2)

    return results[:max_results]


# =========================
# MAIN
# =========================
def main():
    gc = get_gspread_client()
    sh = gc.open_by_key(SPREADSHEET_ID)

    fetched_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Session for publish-date page fetches
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})

    for cfg in CSE_CONFIGS:
        print(f"Processing → {cfg['sheet']}")

        ws = get_or_create_worksheet(sh, cfg["sheet"])
        ensure_headers(ws)
        existing_links = load_existing_links(ws)

        results = cse_search(cfg["query"], cfg["cse_id"], max_results=100)

        rows_to_add = []
        html_fetch_count = 0

        for r in results:
            link = (r.get("link") or "").strip()
            if not link or link in existing_links:
                continue

            domain = source_domain(link)

            published = ""
            # Only fetch HTML for a limited number of new links to keep the workflow fast
            if html_fetch_count < PAGE_FETCH_LIMIT:
                published = fetch_publish_date(link, sess)
                html_fetch_count += 1

            rows_to_add.append([
                fetched_date,
                r.get("title", ""),
                link,
                domain,
                published,
                r.get("snippet", ""),
            ])
            existing_links.add(link)

        if rows_to_add:
            ws.append_rows(rows_to_add, value_input_option="RAW")
            print(f"  Added {len(rows_to_add)} rows. (publish-date fetches: {html_fetch_count})")
        else:
            print("  No new rows.")

    print("Done.")


if __name__ == "__main__":
    main()
