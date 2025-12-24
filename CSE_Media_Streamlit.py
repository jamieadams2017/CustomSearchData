import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ======================================================
# PAGE CONFIG
# ======================================================
st.set_page_config(page_title="CSE Media Monitoring", layout="wide")

# ======================================================
# CONFIG
# ======================================================
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]

# Put all the sheet tab names you want to show here.
# If a tab doesn't exist in the spreadsheet, the app will show a warning and skip it.
SHEET_TABS = [
    "IND_Media",
    "PAK_Media",
]

# ======================================================
# GOOGLE SHEETS LOADER (TOML-NATIVE SECRETS)
# ======================================================
@st.cache_data(ttl=300)
def load_sheet(sheet_name: str) -> pd.DataFrame:
    creds_info = dict(st.secrets["GSHEETS_SA"])
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )

    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        # Return empty DF with a marker; caller will handle the warning.
        return pd.DataFrame({"__missing_sheet__": [sheet_name]})

    records = ws.get_all_records()
    df = pd.DataFrame(records)

    # Ensure expected columns exist (safe defaults)
    for col in ["Title", "Snippet", "Link", "SourceDomain", "PublishedDate", "RunTime", "ThumbnailURL"]:
        if col not in df.columns:
            df[col] = ""

    # Parse dates
    df["PublishedDate"] = pd.to_datetime(df["PublishedDate"], errors="coerce")
    df["RunTime"] = pd.to_datetime(df["RunTime"], errors="coerce")

    # Clean strings
    df["Title"] = df["Title"].fillna("").astype(str)
    df["Snippet"] = df["Snippet"].fillna("").astype(str)
    df["Link"] = df["Link"].fillna("").astype(str)
    df["SourceDomain"] = df["SourceDomain"].fillna("").astype(str).str.strip()
    df["ThumbnailURL"] = df["ThumbnailURL"].fillna("").astype(str).str.strip()

    return df


# ======================================================
# STYLES (NO SIDEBAR, KPI + CARDS)
# ======================================================
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; }

    .filter-box {
        background-color: #f5f7fa;
        padding: 1rem;
        border-radius: 10px;
        margin-bottom: 1rem;
    }

    .kpi-wrap {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
        margin-bottom: 1rem;
    }

    .kpi-label {
        font-size: 0.95rem;
        color: #555;
        margin-bottom: 0.25rem;
    }

    .kpi-value {
        font-size: 2.2rem;
        font-weight: 700;
        line-height: 1.1;
        color: #111;
    }

    .card {
        background-color: #ffffff;
        padding: 1rem;
        border-radius: 12px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }

    /* --- Horizontal media layout --- */
    .media-card {
      display: flex;
      gap: 14px;
      align-items: stretch;
    }

    .media-thumb {
      flex: 0 0 160px;
      width: 160px;
      height: 120px;
      border-radius: 10px;
      overflow: hidden;
      background: #eef1f4;
    }

    .media-thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .media-content {
      flex: 1;
      min-width: 0;
    }

    .media-title {
      font-size: 1rem;
      font-weight: 650;
      margin-bottom: 0.35rem;
      color: #111;
      line-height: 1.25;
    }

    .media-snippet {
      font-size: 0.9rem;
      color: #444;
      margin-bottom: 0.6rem;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .media-meta {
      font-size: 0.82rem;
      color: #666;
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }

    .media-link {
      font-weight: 600;
      text-decoration: none;
    }

    a { text-decoration: none; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ======================================================
# HEADER
# ======================================================
st.title("CSE Media Monitoring")

# ======================================================
# GLOBAL FILTER BAR (TOP)
# ======================================================
with st.container():
    st.markdown('<div class="filter-box">', unsafe_allow_html=True)

    c1, c2 = st.columns([1.2, 1.8])

    with c1:
        date_range = st.date_input("Published date range", [], format="YYYY-MM-DD")

    with c2:
        search_text = st.text_input("Search (title/snippet/domain)", "", placeholder="Type keywords...")

    st.markdown("</div>", unsafe_allow_html=True)

# ======================================================
# TABS
# ======================================================
tabs = st.tabs(SHEET_TABS)

for sheet_name, tab in zip(SHEET_TABS, tabs):
    with tab:
        df = load_sheet(sheet_name)

        # Missing worksheet handling
        if "__missing_sheet__" in df.columns:
            st.warning(
                f'Sheet tab "{sheet_name}" was not found in the spreadsheet. '
                "Create it (exact same name) or remove it from SHEET_TABS."
            )
            continue

        if df.empty:
            st.info("No data available.")
            continue

        # Domain dropdown options from this tab's data
        domain_options = sorted([d for d in df["SourceDomain"].unique().tolist() if d])

        # Tab-specific domain multiselect
        dom_col1, dom_col2 = st.columns([2, 1])
        with dom_col1:
            selected_domains = st.multiselect(
                "Filter by source domain",
                options=domain_options,
                default=[],
                placeholder="Select one or more domains...",
                key=f"domains_{sheet_name}",
            )
        with dom_col2:
            clear = st.button("Clear", use_container_width=True, key=f"clear_{sheet_name}")
            if clear:
                selected_domains = []

        filtered = df.copy()

        # Date filter
        if len(date_range) == 2:
            start, end = date_range
            filtered = filtered[
                (filtered["PublishedDate"] >= pd.to_datetime(start)) &
                (filtered["PublishedDate"] <= pd.to_datetime(end))
            ]

        # Domain filter
        if selected_domains:
            filtered = filtered[filtered["SourceDomain"].isin(selected_domains)]

        # Text search filter (Title + Snippet + Domain)
        if search_text.strip():
            q = search_text.strip().lower()
            haystack = (
                filtered["Title"].str.lower() + " " +
                filtered["Snippet"].str.lower() + " " +
                filtered["SourceDomain"].str.lower()
            )
            filtered = filtered[haystack.str.contains(q, na=False)]

        # ✅ Sort strictly by PublishedDate (newest first), blanks last
        filtered = filtered.sort_values(
            by=["PublishedDate"],
            ascending=False,
            na_position="last",
        )

        # KPI counters
        news_count = int(len(filtered))
        domain_count = int(filtered["SourceDomain"].nunique())

        k1, k2, k3 = st.columns([1, 1, 2])

        with k1:
            st.markdown(
                f"""
                <div class="kpi-wrap">
                  <div class="kpi-label">News (filtered)</div>
                  <div class="kpi-value">{news_count}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with k2:
            st.markdown(
                f"""
                <div class="kpi-wrap">
                  <div class="kpi-label">Source domains (filtered)</div>
                  <div class="kpi-value">{domain_count}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with k3:
            export_df = filtered.copy()
            export_df["PublishedDate"] = export_df["PublishedDate"].dt.strftime("%Y-%m-%d")
            export_df["RunTime"] = export_df["RunTime"].dt.strftime("%Y-%m-%d")

            csv_bytes = export_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="⬇️ Download filtered CSV",
                data=csv_bytes,
                file_name=f"{sheet_name}_filtered.csv",
                mime="text/csv",
                use_container_width=True,
                key=f"dl_{sheet_name}",
            )

        if filtered.empty:
            st.warning("No results match the selected filters/search.")
            continue

        # Cards (3 per row) — thumbnail left, content right
        for i in range(0, len(filtered), 3):
            cols = st.columns(3)
            for col, (_, r) in zip(cols, filtered.iloc[i:i+3].iterrows()):
                with col:
                    pub = r["PublishedDate"].date() if pd.notnull(r["PublishedDate"]) else "—"
                    thumb = (r.get("ThumbnailURL", "") or "").strip()

                    thumb_html = f'<img src="{thumb}" />' if thumb else ""

                    st.markdown(
                        f"""
                        <div class="card">
                            <div class="media-card">
                                <div class="media-thumb">{thumb_html}</div>
                                <div class="media-content">
                                    <div class="media-title">{r['Title']}</div>
                                    <div class="media-snippet">{r['Snippet']}</div>
                                    <div class="media-meta">
                                        <span>🗓 {pub}</span>
                                        <span>🌐 {r['SourceDomain']}</span>
                                        <a class="media-link" href="{r['Link']}" target="_blank">Read article ↗</a>
                                    </div>
                                </div>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
