import streamlit as st
import re
import html
import sqlite3
import pandas as pd
import os
import time
from datetime import datetime, timedelta

from master_runner import run_all_scrapers, SCRAPERS
from dashboard_add_company import render_add_company_panel
from runtime_mode import is_local
from git_push import push_results
from auth import render_unlock, unlocked
from scoring import internship_score, score_tier, fit_score, fit_label

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "master_jobs.db")
STATUS_PATH = os.path.join(BASE_DIR, "status.txt")

PREVIEW_COUNT = 2   # listings shown on the card before you expand it
DRY_RUN_ALERT = 3   # consecutive runs with 0 results before a scraper is flagged

# Scoring lives in scoring.py so this file and discord_notify.py cannot drift
# apart: internship_score answers "is this a real internship", fit_score
# answers "is it a good fit for a mechanical engineering undergrad".


def age_text(date_added, now=None):
    """'today' / 'yesterday' / '6 days ago' from a stored date_added string.

    date_added is when a scraper FIRST saw the posting, which is the best
    proxy available for how long it has been open -- boards rarely expose a
    real posted-on date. An old posting is often already filled.
    """
    days = age_days(date_added, now)
    if days is None:
        return ""
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def age_days(date_added, now=None):
    try:
        seen = datetime.strptime(str(date_added)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return max(0, ((now or datetime.now()) - seen).days)


# set_page_config must be the first Streamlit command in the script, so no
# widget or panel may render above it. The add-company panel used to be called
# before this line.
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

AGE_CHOICES = {"Any age": None, "≤ 3 days": 3, "≤ 7 days": 7,
               "≤ 14 days": 14, "≤ 30 days": 30}
max_age_label = st.sidebar.select_slider(
    "Posting age", options=list(AGE_CHOICES), value="Any age",
    help="Days since a scraper first saw the posting. Boards rarely publish a "
         "posted-on date, so this is the closest proxy — and an old listing is "
         "often already filled.")
max_age = AGE_CHOICES[max_age_label]

st.title("USA Internship Monitor")

if st.session_state.get("show_success_toast"):
    st.toast("Database updated and refreshed successfully!", icon="✅")
    st.session_state.show_success_toast = False

b1, b2, _ = st.columns([1, 1, 4])
with b1:
    if st.button("🔄 Refresh Data", width="stretch"):
        st.cache_data.clear()
        st.session_state.show_success_toast = True
        st.rerun()
with b2:
    # Scraping only works on the machine that owns the database. On Streamlit
    # Cloud this would run in an ephemeral container, hit job boards from
    # Streamlit's IP, fail outright for every Playwright-based scraper, and
    # write results to a disk that is wiped on the next restart.
    if is_local():
        if st.button("🚀 Run Scrapers Now", width="stretch"):
            with st.spinner("Scraping job boards... this can take several minutes."):
                run_all_scrapers()
                # Scraping alone only updates the local database; the deployed
                # dashboard reads the committed copy, so publish as well --
                # otherwise results sit here until the scheduled run.
                st.write("Publishing results...")
                push_results()
            st.cache_data.clear()
            st.session_state.show_success_toast = True
            st.rerun()

if not is_local():
    st.caption("📖 Read-only view — data updates when the local scraper runs.")

# Add-a-company shells out to Playwright, which cannot run on Streamlit Cloud.
if is_local():
    render_add_company_panel()


@st.cache_data(ttl=60)
def load_health():
    """Latest run per scraper, plus how many consecutive runs came back with
    nothing. A scraper that quietly returns 0 forever looks identical to one
    watching a quiet board -- this is the difference."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        runs = pd.read_sql_query(
            "SELECT scraper, company, ran_at, status, jobs_found, rows_stored, "
            "seconds FROM scraper_runs ORDER BY ran_at DESC", conn)
    except Exception:
        return pd.DataFrame()       # table not created yet (no batch has run)
    finally:
        conn.close()
    if runs.empty:
        return runs

    rows = []
    for scraper, group in runs.groupby("scraper", sort=False):
        group = group.sort_values("ran_at", ascending=False)
        latest = group.iloc[0]
        dry = 0
        for _, run in group.iterrows():
            if run["status"] in ("empty", "failed"):
                dry += 1
            else:
                break
        rows.append({
            "scraper": scraper.replace("scrapers/", "").replace(".py", ""),
            "company": latest["company"],
            "last run": str(latest["ran_at"])[:16],
            "status": latest["status"],
            "found": latest["jobs_found"],
            "stored": latest["rows_stored"],
            "dry runs": dry,
            "runs logged": len(group),
        })
    return pd.DataFrame(rows)


def render_health_panel():
    health = load_health()
    if health.empty:
        st.sidebar.caption("🩺 Scraper health: no runs logged yet — it fills in "
                           "after the next nightly batch.")
        return

    failing = health[health["status"] == "failed"]
    stale = health[(health["status"] == "empty") & (health["dry runs"] >= DRY_RUN_ALERT)]
    finding = health[health["status"] == "ok"]
    if len(failing) or len(stale):
        st.sidebar.error(f"🩺 {len(failing)} failing · {len(stale)} quiet for "
                         f"{DRY_RUN_ALERT}+ runs")
    else:
        # Not "all healthy": a scraper can be fine and still find nothing on a
        # quiet board, and saying "healthy" would paper over that difference.
        st.sidebar.success(f"🩺 {len(finding)}/{len(health)} scrapers found jobs "
                           f"last run")

    unlogged = len(SCRAPERS) - len(health)
    if unlogged > 0:
        st.sidebar.caption(f"{unlogged} of {len(SCRAPERS)} scrapers haven't "
                           f"logged a run yet — they appear after the next batch.")

    with st.sidebar.expander("Scraper health", expanded=bool(len(failing))):
        def flag(row):
            """(severity, icon) -- severity sorts, the icon is shown. Sorting
            on the icon itself would order by codepoint, which is meaningless."""
            if row["status"] == "failed":
                return 0, "❌"
            if row["status"] == "empty" and row["dry runs"] >= DRY_RUN_ALERT:
                return 1, "⚠️"
            if row["status"] == "empty":
                return 2, "·"
            return 3, "✅"

        table = health.copy()
        flags = table.apply(flag, axis=1)
        table.insert(0, "", [f[1] for f in flags])
        table["_severity"] = [f[0] for f in flags]
        table = table.sort_values(["_severity", "dry runs", "scraper"],
                                  ascending=[True, False, True])
        st.dataframe(table[["", "scraper", "last run", "found", "stored",
                            "dry runs"]],
                     hide_index=True, width="stretch")
        st.caption(f"❌ failed · ⚠️ {DRY_RUN_ALERT}+ runs with 0 results "
                   f"(check the board by hand) · · one quiet run · ✅ found jobs")


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


# Sidebar order follows call order, so this lands under the filters. It is
# defined above, hence the call sitting here rather than in the sidebar block.
st.sidebar.divider()
render_health_panel()

df = load_data()

if df.empty:
    if is_local():
        st.info("No internships found yet. Click 'Run Scrapers Now' to start scraping!")
    else:
        st.info("No internship data has been published yet.")
    st.stop()

CUTOFF = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
df["is_new"] = df["date_added"] >= CUTOFF
df["score"] = df["title"].apply(internship_score)
df["tier"] = df["score"].apply(score_tier)
df["age"] = df["date_added"].apply(age_days)
df["age_label"] = df["date_added"].apply(age_text)
df["fit"] = [fit_score(t, l) for t, l in zip(df["title"], df["location"])]
# strongest match first; newest wins ties
df = df.sort_values(["score", "date_added"], ascending=[False, False])

view = df[df["is_new"]] if new_only else df
if hide_unlikely:
    view = view[view["tier"] != "unlikely"]
if max_age is not None:
    view = view[view["age"].notna() & (view["age"] <= max_age)]

# --- keyword search -------------------------------------------------------
# The widget itself is drawn further down (top right, under the counts and
# above the first card), but its value is needed HERE so the counts and the
# card grid both reflect it. Streamlit runs top to bottom, so we read the
# stored value from session_state now and create the widget later with the
# same key -- on every rerun after a keystroke the value is already there.
SEARCH_KEY = "job_search"


def apply_search(frame, query):
    """Filter on title + company + location.

    Every term must match (AND), "quoted phrases" are kept whole, and a
    leading - excludes. Matching is plain text, never regex, so a query like
    C++ or (Paid) can't raise.
    """
    terms = re.findall(r'"[^"]*"|\S+', query)
    haystack = (frame["title"] + " " + frame["company"] + " "
                + frame["location"].fillna("")).str.lower()
    for term in terms:
        negate = term.startswith("-") and len(term) > 1
        if negate:
            term = term[1:]
        term = term.strip('"').strip().lower()
        if not term:
            continue
        hit = haystack.str.contains(term, regex=False)
        frame = frame[~hit] if negate else frame[hit]
        haystack = haystack[frame.index]
    return frame


search_query = str(st.session_state.get(SEARCH_KEY, "")).strip() if unlocked() else ""
searched = view
if search_query:
    view = apply_search(view, search_query)

if searched.empty:
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
    + (f"  ·  🔎 matching “{search_query}” of {len(searched)}" if search_query else "")
)

# Password box (left) and search box (right), under the counts and above the
# first card. Searching titles you cannot read is pointless, so the search box
# only appears once the session is unlocked.
lock_col, search_col = st.columns([2, 1])
is_unlocked = unlocked()
if not is_unlocked:
    with lock_col:
        is_unlocked = render_unlock()
        if is_unlocked:
            st.rerun()

with search_col:
    if is_unlocked:
        st.text_input(
            "Search openings",
            key=SEARCH_KEY,
            placeholder="🔎 Search title, company, or location…",
            label_visibility="collapsed",
            help="Words are ANDed, \"quoted phrases\" match whole, and -word "
                 "excludes. Example: mechanical -senior \"summer 2027\"",
        )

if search_query and view.empty:
    st.warning(f"No openings match “{search_query}”. Try fewer words, or a "
               f"company, city, or state.")
    st.stop()

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
    # Age sits on the same line as the location: a 30-day-old posting is
    # usually filled, and that is worth seeing before clicking.
    age = job.get("age_label") or ""
    stale = isinstance(job.get("age"), (int, float)) and job["age"] >= 21
    age_part = (f" · 🕑 {'⚠️ ' if stale else ''}{age}") if age else ""
    if shown == full:
        st.caption(f"📍 {shown}{age_part}")
    else:
        # title= gives the full site list on hover without a taller card
        st.markdown(
            f"<span title=\"{html.escape(full, quote=True)}\" "
            f"style=\"font-size:0.82rem; opacity:0.65;\">📍 {html.escape(shown)}"
            f"{html.escape(age_part)}</span>",
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

                if not is_unlocked:
                    # Teaser: the company and its counts stay public, the
                    # titles, locations and links do not.
                    st.markdown(
                        f"<span style='font-size:0.85rem; opacity:0.6;'>🔒 "
                        f"{meta['openings']} title"
                        f"{'s' if meta['openings'] != 1 else ''} hidden — enter "
                        f"the password above</span>", unsafe_allow_html=True)
                    continue

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