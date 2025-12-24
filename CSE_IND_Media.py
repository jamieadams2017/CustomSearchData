import os
import json
import time
import re
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials


# =========================
# CONFIG
# =========================
CSE_API_KEY = os.environ["CSE_API_KEY"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

CSE_CONFIGS = [
    {"cse_id": "628862614b5d44b5b", "query": "intitle:Bangladesh", "sheet": "IND_Media"},
    {"cse_id": "c0c3126bab16f48c6", "query": "intitle:Bangladesh", "sheet": "PAK_Media"},
]

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

PAGE_FETCH_LIMIT = 40   # limit HTML fetches per sheet (keeps runs fast)
HTTP_TIMEOUT = 20


# =========================
# GOOGLE SHEETS
# =========================
def get_gspread_client():
    creds = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return gspread.authorize(
        Credentials.from_service_account_info(creds, scopes=scopes)
    )


def get_or_create_worksheet(sh, title, rows=3000, cols=12):
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def ensure_headers(ws):
    # ✅ Added ThumbnailURL
    headers = [
        "RunTime",
        "Title",
        "Link",
        "SourceDomain",
        "PublishedDate",
        "ThumbnailURL",
        "Snippet",
    ]
    if ws.row_values(1) != headers:
        ws.clear()
        ws.append_row(headers)


def load_existing_links(ws):
    links = ws.col_values(3)  # Link column
    return set(l.strip() for l in links[1:] if l.strip())


# =========================
# HELPERS
# =========================
def source_domain(url):
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def to_yyyy_mm_dd(value):
    if not value:
        return ""
    value = value.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", value)
    return m.group(1) if m else ""


def extract_relative_time_from_snippet(snippet):
    """
    Handles: '19 hours ago ...', '3 days ago ...', '45 minutes ago ...'
    Returns (published_date, cleaned_snippet)
    """
    if not snippet:
        return "", snippet

    m = re.match(
        r"^(?P<num>\d+)\s+(?P<unit>minute|minutes|hour|hours|day|days)\s+ago\s*\.\.\.\s*(?P<rest>.*)",
        snippet.strip(),
        flags=re.IGNORECASE,
    )

    if not m:
        return "", snippet

    num = int(m.group("num"))
    unit = m.group("unit").lower()
    rest = m.group("rest").strip()

    if "minute" in unit:
        delta = timedelta(minutes=num)
    elif "hour" in unit:
        delta = timedelta(hours=num)
    else:
        delta = timedelta(days=num)

    published = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%d")
    return published, rest


def extract_jsonld_publish_date(html):
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for b in blocks:
        try:
            data = json.loads(b)
        except Exception:
            continue

        dates = []

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in ("datepublished", "datecreated", "dateissued"):
                        if isinstance(v, str):
                            dates.append(v)
                    walk(v)
            elif isinstance(obj, list):
                for i in obj:
                    walk(i)

        walk(data)

        for d in dates:
            parsed = to_yyyy_mm_dd(d)
            if parsed:
                return parsed

    return ""


def extract_meta_publish_date(html):
    patterns = [
        r'article:published_time["\']\s*content=["\']([^"\']+)',
        r'name=["\']pubdate["\']\s*content=["\']([^"\']+)',
        r'name=["\']publishdate["\']\s*content=["\']([^"\']+)',
        r'name=["\']date["\']\s*content=["\']([^"\']+)',
        r'dc\.date\.issued["\']\s*content=["\']([^"\']+)',
    ]

    for p in patterns:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            parsed = to_yyyy_mm_dd(m.group(1))
            if parsed:
                return parsed
    return ""


def fetch_publish_date(url, session):
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        html = r.text

        d = extract_jsonld_publish_date(html)
        if d:
            return d

        return extract_meta_publish_date(html)

    except Exception:
        return ""


def extract_thumbnail(item: dict) -> str:
    """
    Best-effort thumbnail extraction from CSE result item:
    - pagemap.cse_thumbnail[0].src (small)
    - pagemap.cse_image[0].src (bigger)
    - pagemap.metatags[0]['og:image'] / ['twitter:image']
    """
    pm = item.get("pagemap") or {}

    thumbs = pm.get("cse_thumbnail") or []
    if isinstance(thumbs, list) and thumbs:
        src = (thumbs[0] or {}).get("src")
        if src:
            return src

    imgs = pm.get("cse_image") or []
    if isinstance(imgs, list) and imgs:
        src = (imgs[0] or {}).get("src")
        if src:
            return src

    metas = pm.get("metatags") or []
    if isinstance(metas, list) and metas:
        meta0 = metas[0] or {}
        src = meta0.get("og:image") or meta0.get("twitter:image")
        if src:
            return src

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
            "dateRestrict": "d1",
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
                "thumbnail": extract_thumbnail(it),  # ✅ added
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

    runtime = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    for cfg in CSE_CONFIGS:
        print(f"Processing → {cfg['sheet']}")

        ws = get_or_create_worksheet(sh, cfg["sheet"])
        ensure_headers(ws)
        existing_links = load_existing_links(ws)

        results = cse_search(cfg["query"], cfg["cse_id"])
        rows = []
        fetch_count = 0

        for r in results:
            link = r["link"].strip()
            if not link or link in existing_links:
                continue

            domain = source_domain(link)

            # 1️⃣ Try page-based publish date
            published = ""
            if fetch_count < PAGE_FETCH_LIMIT:
                published = fetch_publish_date(link, session)
                fetch_count += 1

            # 2️⃣ Fallback to snippet relative time
            snippet = r["snippet"]
            if not published:
                rel_date, snippet = extract_relative_time_from_snippet(snippet)
                if rel_date:
                    published = rel_date

            rows.append([
                runtime,
                r["title"],
                link,
                domain,
                published,
                r.get("thumbnail", ""),
                snippet,
            ])

            existing_links.add(link)

        if rows:
            ws.append_rows(rows, value_input_option="RAW")
            print(f"  Added {len(rows)} rows")
        else:
            print("  No new rows")

    print("Done.")


if __name__ == "__main__":
    main()
