import streamlit as st
import re
import html
import sqlite3
import pandas as pd
import os
import time
from datetime import datetime, timedelta

from master_runner import run_all_scrapers

from dashboard_add_company import render_add_company_panel   # near your other imports

render_add_company_panel()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "master_jobs.db")
STATUS_PATH = os.path.join(BASE_DIR, "status.txt")

PREVIEW_COUNT = 2   # listings shown on the card before you expand it

# --- how likely is this title an actual internship? -----------------------
# The scrapers match r'\bintern', which also matches "Internal" and
# "International" -- 37% of the current database is Internal Audit Managers
# and International Trade Compliance roles. Scoring sorts those to the bottom
# instead of letting them take up the visible slots on each card.
STRONG = re.compile(r"\b(intern|interns|internship|internships|co-op|coop|co op)\b")
FALSE_FRIEND = re.compile(r"\b(internal|international|internally|internationally)\b")
SENIORITY = re.compile(
    r"\b(manager|mgr|director|senior|sr\.?|principal|staff|supervisor|"
    r"chief|vp|president|executive|head of|trainer|recruiter|recruiting)\b")
SEASON = re.compile(r"\b(summer|fall|autumn|spring|winter)\b|\b20\d\d\b")
ADVANCED = re.compile(r"\b(phd|ph\.d|doctoral|postdoc|mba|jd|law|md)\b")
GRADUATE = re.compile(r"\b(graduate|masters|master's)\b")
RELEVANT = re.compile(
    r"\b(mechanical|manufacturing|aerospace|aeronautic|astronautic|propulsion|"
    r"structures|structural|thermal|design|test|testing|systems|materials|"
    r"robotics|avionics|mechatronic|industrial|hardware|cad|engineer|"
    r"engineering|production|quality|integration|flight|vehicle)\b")


def internship_score(title):
    """0-100ish. Higher = more likely a real internship worth your time."""
    # Underscores and slashes are word characters to regex, so "Program_
    # Internship" defeats \binternship\b. Normalize separators to spaces
    # before matching.
    t = re.sub(r"[_/\\|+&–—-]+", " ", title.lower())
    score = 0

    if STRONG.search(t):
        score += 100
    elif FALSE_FRIEND.search(t):
        return 0          # "International Trade Compliance Manager 3"

    if SENIORITY.search(t):
        score -= 70       # "Recruiting Coordinator, Intern Program"
    if ADVANCED.search(t):
        score -= 35       # PhD/MBA tracks, not a sophomore ME
    if GRADUATE.search(t):
        score -= 15
    if SEASON.search(t):
        score += 15       # "Summer 2027 ..." reads like a real cycle posting
    if RELEVANT.search(t):
        score += 12

    return max(score, 0)


def score_tier(score):
    if score >= 100:
        return "core"       # confident internship
    if score >= 40:
        return "maybe"      # internship-ish, or senior-flavoured
    return "unlikely"       # almost certainly a false positive



st.set_page_config(page_title="Internship Monitor", layout="wide")

# --- heartbeat ------------------------------------------------------------
st.sidebar.title("System Status")
if os.path.exists(STATUS_PATH):
    hours_since = (time.time() - os.path.getmtime(STATUS_PATH)) / 3600
    with open(STATUS_PATH) as f:
        last_run_time = f.read().strip()
    if hours_since < 26:
        st.sidebar.success("🟢 Auto-Scraper: **Active**")
    else:
        st.sidebar.error("🔴 Auto-Scraper: **Offline** (Check Terminal)")
    st.sidebar.caption(f"Last automated check:\n{last_run_time}")
else:
    st.sidebar.error("🔴 Auto-Scraper: **Offline**")
    st.sidebar.caption("Run master_runner.py to activate.")

st.sidebar.divider()
n_cols = st.sidebar.slider("Cards per row", 2, 4, 3)
sort_by = st.sidebar.radio("Sort companies by", ["Most openings", "Newest posting", "A–Z"])
new_only = st.sidebar.toggle("Only show new (last 24h)")
hide_unlikely = st.sidebar.toggle("Hide likely false positives", value=True,
    help="Buries titles that only matched on 'Internal' or 'International'.")

st.title("USA Internship Monitor")

if st.session_state.get("show_success_toast"):
    st.toast("Database updated and refreshed successfully!", icon="✅")
    st.session_state.show_success_toast = False

b1, b2, _ = st.columns([1, 1, 4])
with b1:
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.session_state.show_success_toast = True
        st.rerun()
with b2:
    if st.button("🚀 Run Scrapers Now", use_container_width=True):
        with st.spinner("Scraping job boards... this can take several minutes."):
            run_all_scrapers()
        st.cache_data.clear()
        st.session_state.show_success_toast = True
        st.rerun()


@st.cache_data(ttl=60)
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        "SELECT company, title, location, url, date_added FROM jobs "
        "ORDER BY date_added DESC", conn)
    conn.close()
    return df


df = load_data()

if df.empty:
    st.info("No internships found yet. Click 'Run Scrapers Now' to start scraping!")
    st.stop()

CUTOFF = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
df["is_new"] = df["date_added"] >= CUTOFF
df["score"] = df["title"].apply(internship_score)
df["tier"] = df["score"].apply(score_tier)
# strongest match first; newest wins ties
df = df.sort_values(["score", "date_added"], ascending=[False, False])

view = df[df["is_new"]] if new_only else df
if hide_unlikely:
    view = view[view["tier"] != "unlikely"]

if view.empty:
    st.info("Nothing new in the last 24 hours.")
    st.stop()

# --- order the cards ------------------------------------------------------
stats = (view.groupby("company")
             .agg(openings=("title", "count"),
                  core=("tier", lambda s: (s == "core").sum()),
                  newest=("date_added", "max"),
                  new_count=("is_new", "sum"))
             .reset_index())

if sort_by == "Most openings":
    stats = stats.sort_values(["core", "openings", "company"],
                              ascending=[False, False, True])
elif sort_by == "Newest posting":
    stats = stats.sort_values("newest", ascending=False)
else:
    stats = stats.sort_values("company")

total_new = int(df["is_new"].sum())
st.caption(
    f"**{len(view)}** opening{'s' if len(view) != 1 else ''} across "
    f"**{len(stats)}** compan{'ies' if len(stats) != 1 else 'y'}"
    + (f"  ·  🆕 {total_new} added in the last 24h" if total_new else "")
)
st.write("")


LOCATIONS_SHOWN = 4   # sites listed before collapsing to "+N more"


def format_location(raw):
    """
    Multi-site Workday postings resolve to 40+ locations joined by ' | ', which
    makes one card taller than the whole grid. Collapse to the first few and
    keep the full list in a hover tooltip.

    Returns (display_text, full_text).
    """
    if not raw:
        return "", ""

    parts = [p.strip() for p in str(raw).split("|") if p.strip()]

    cleaned = []
    for p in parts:
        # "USA - Seattle, WA" -> "Seattle, WA"; the country adds nothing here
        for prefix in ("USA - ", "United States - ", "US - "):
            if p.startswith(prefix):
                p = p[len(prefix):]
                break
        if p not in cleaned:          # the same site often repeats
            cleaned.append(p)

    full = " · ".join(cleaned)
    if len(cleaned) <= LOCATIONS_SHOWN:
        return full, full

    hidden = len(cleaned) - LOCATIONS_SHOWN
    shown = " · ".join(cleaned[:LOCATIONS_SHOWN]) + f"  +{hidden} more"
    return shown, full


def render_job(job):
    tag = " 🆕" if job["is_new"] else ""
    warn = " ⚠️" if job["tier"] == "unlikely" else ""
    st.markdown(f"**[{job['title'].strip()}]({job['url']})**{tag}{warn}")

    shown, full = format_location(job["location"])
    if shown == full:
        st.caption(f"📍 {shown}")
    else:
        # title= gives the full site list on hover without a taller card
        st.markdown(
            f"<span title=\"{html.escape(full, quote=True)}\" "
            f"style=\"font-size:0.82rem; opacity:0.65;\">📍 {html.escape(shown)}</span>",
            unsafe_allow_html=True,
        )


# --- card grid ------------------------------------------------------------
# Fixed columns per row that wrap onto new rows, instead of one column per
# company. Card width no longer shrinks as you add companies.
companies = stats.to_dict("records")

for row_start in range(0, len(companies), n_cols):
    row = companies[row_start:row_start + n_cols]
    cols = st.columns(n_cols)          # always n_cols, so the last row aligns
    for col, meta in zip(cols, row):
        name = meta["company"]
        jobs = view[view["company"] == name]
        with col:
            with st.container(border=True):
                badge = f" · 🆕 {int(meta['new_count'])}" if meta["new_count"] else ""
                st.markdown(f"##### {name}")
                core = int((jobs["tier"] == "core").sum())
                st.caption(f"{core} internship{'s' if core != 1 else ''}"
                           f" · {meta['openings']} listing"
                           f"{'s' if meta['openings'] != 1 else ''}{badge}")

                # jobs is already sorted by score, so head() is the best
                # matches -- not merely the most recent.
                for _, job in jobs.head(PREVIEW_COUNT).iterrows():
                    render_job(job)

                remaining = jobs.iloc[PREVIEW_COUNT:]
                if not remaining.empty:
                    weak = int((remaining["tier"] == "unlikely").sum())
                    label = f"Show {len(remaining)} more"
                    if weak:
                        label += f" ({weak} may not be internships)"
                    with st.expander(label):
                        for _, job in remaining.iterrows():
                            render_job(job)