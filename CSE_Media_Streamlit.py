import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# ======================================================
# PAGE CONFIG
# ======================================================
st.set_page_config(
    page_title="FIMI Media Monitor",
    layout="wide",
)

# ======================================================
# CONFIG
# ======================================================
SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]

# Sheet tabs to display (must exist in Google Sheet)
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
    ws = sh.worksheet(sheet_name)

    records = ws.get_all_records()
    df = pd.DataFrame(records)

    if df.empty:
        return df

    # Normalize dates
    df["PublishedDate"] = pd.to_datetime(df["PublishedDate"], errors="coerce")
    df["RunTime"] = pd.to_datetime(df["RunTime"], errors="coerce")

    return df


# ======================================================
# STYLES (NO SIDEBAR)
# ======================================================
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; }

    .filter-box {
        background-color: #f5f7fa;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 1.5rem;
    }

    .card {
        background-color: #ffffff;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.08);
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }

    .card-title {
        font-size: 1rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
    }

    .card-snippet {
        font-size: 0.9rem;
        color: #444;
        margin-bottom: 0.8rem;
    }

    .card-meta {
        font-size: 0.8rem;
        color: #666;
    }

    a {
        text-decoration: none;
        font-weight: 500;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ======================================================
# HEADER
# ======================================================
st.title("🇧🇩 Bangladesh Media Monitor")

# ======================================================
# FILTER BAR (TOP, NO SIDEBAR)
# ======================================================
with st.container():
    st.markdown('<div class="filter-box">', unsafe_allow_html=True)

    c1, c2 = st.columns(2)

    with c1:
        date_range = st.date_input(
            "Published date range",
            [],
            format="YYYY-MM-DD",
        )

    with c2:
        domain_filter = st.text_input(
            "Filter by source domain (e.g. prothomalo.com)",
            "",
        )

    st.markdown("</div>", unsafe_allow_html=True)

# ======================================================
# TABS (ONE PER SHEET TAB)
# ======================================================
tabs = st.tabs(SHEET_TABS)

for sheet_name, tab in zip(SHEET_TABS, tabs):
    with tab:
        df = load_sheet(sheet_name)

        if df.empty:
            st.info("No data available.")
            continue

        filtered = df.copy()

        # Date filter
        if len(date_range) == 2:
            start, end = date_range
            filtered = filtered[
                (filtered["PublishedDate"] >= pd.to_datetime(start)) &
                (filtered["PublishedDate"] <= pd.to_datetime(end))
            ]

        # Domain filter
        if domain_filter:
            filtered = filtered[
                filtered["SourceDomain"]
                .str.contains(domain_filter.strip(), case=False, na=False)
            ]

        filtered = filtered.sort_values(
            by=["PublishedDate", "RunTime"],
            ascending=False,
        )

        if filtered.empty:
            st.warning("No results match the selected filters.")
            continue

        # ==================================================
        # CARD GRID (3 PER ROW)
        # ==================================================
        for i in range(0, len(filtered), 3):
            cols = st.columns(3)
            for col, (_, r) in zip(cols, filtered.iloc[i:i+3].iterrows()):
                with col:
                    st.markdown(
                        f"""
                        <div class="card">
                            <div>
                                <div class="card-title">{r['Title']}</div>
                                <div class="card-snippet">{r['Snippet']}</div>
                            </div>
                            <div class="card-meta">
                                🗓 {r['PublishedDate'].date() if pd.notnull(r['PublishedDate']) else "—"}
                                &nbsp;&nbsp;•&nbsp;&nbsp;
                                🌐 {r['SourceDomain']}<br>
                                <a href="{r['Link']}" target="_blank">Read article ↗</a>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
