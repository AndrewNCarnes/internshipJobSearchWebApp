import sys
import os
import re
import time
import html

import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "SPX Technologies"

# SPX runs on SAP SuccessFactors Recruiting Marketing (RMK) -- same platform as
# ULA. But where ULA's category page renders client-side, this search board is
# server-rendered: /search-jobs returns a full HTML results table with no JS, so
# plain requests + BeautifulSoup is enough. No Playwright needed.
#
# Pagination is offset-based via ?startrow=N at 25 rows/page (confirmed live:
# "Results 1-25 of 215", Page 1 of 9). We read the real total out of the page
# rather than assuming, and stop when a page yields no new job ids.
#
# LOCATION FORMATS on this board vary a lot, all confirmed from live listings:
#   "Overland Park, Kansas (KS), US"      full state name + code + country
#   "Cuba, Missouri, United States"       full state name + country spelled out
#   "Colorado (CO), US"                   state only, no city
#   "Mississauga, ON, CA"                 CANADA -- see the collision note below
#   "Bristol, GB"                         UK
#
# The "CA" case is a live landmine. location_filter matches bare 2-letter codes
# against US_STATE_CODES, and "CA" is California -- so "Mississauga, ON, CA"
# trips the US signal, skips the foreign-country veto, and lands a Canadian
# posting in the US database. Verified against the real filter. _normalize_location
# below spells out trailing country codes so the veto ("canada") fires cleanly.
# Same class of bug as the ADP scraper's countryCode handling.

BASE = "https://careers.spx.com"
SEARCH_URL = f"{BASE}/search-jobs"
PAGE_SIZE = 25
MAX_PAGES = 40          # hard cap; 215 jobs is ~9 pages, this is huge headroom
PAGE_DELAY = 1.0

TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

# Trailing ISO country codes that collide with US state codes, or that the
# filter reads more reliably by name. Only applied to the LAST comma-segment,
# so "Overland Park, Kansas (KS), US" is untouched.
COUNTRY_CODE_NAMES = {
    "CA": "Canada",          # vs California -- the live Mississauga case
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "MX": "Mexico",          # vs no US state, but be explicit
    "DE": "Germany",         # vs Delaware
    "IN": "India",           # vs Indiana
    "CN": "China",
    "JP": "Japan",
    "KR": "South Korea",
    "FR": "France",
    "IT": "Italy",
    "ES": "Spain",
    "NL": "Netherlands",
    "PL": "Poland",
    "BR": "Brazil",
    "AU": "Australia",
    "ZA": "South Africa",
    "SG": "Singapore",
    "AE": "United Arab Emirates",
}


def _clean(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", html.unescape(str(text))).strip()


def _normalize_location(loc):
    """Spell out a trailing 2-letter country code so location_filter's
    country-name veto fires instead of the code being misread as a US state.
    'Mississauga, ON, CA' -> 'Mississauga, ON, Canada'.
    Leaves 'US'/'USA' and everything else alone."""
    loc = _clean(loc)
    if not loc:
        return ""
    # Drop RMK's multi-location suffix: "Fremont, California (CA), US +1 more…"
    loc = re.sub(r"\+\s*\d+\s*more.*$", "", loc, flags=re.I).strip(" ,")
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) >= 2:
        tail = parts[-1].upper()
        if tail in COUNTRY_CODE_NAMES:
            parts[-1] = COUNTRY_CODE_NAMES[tail]
            return ", ".join(parts)
    return loc


def _get(params):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(SEARCH_URL, params=params, headers=HEADERS,
                             timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"[{COMPANY_NAME}] request failed after {RETRIES} tries "
          f"(startrow={params.get('startrow')}): {last}")
    return None


def _total_results(soup):
    """Read 'Results 1 - 25 of 215' so we page the real count instead of
    guessing. Returns None if the banner isn't found."""
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Results\s+[\d,]+\s*[-–]\s*[\d,]+\s+of\s+([\d,]+)", text, re.I)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _job_id_from_url(url):
    """RMK job URLs: /job/Overland-Park-Senior-Engineer-Kans/1224176400/
    The long numeric segment is the stable requisition id."""
    for seg in reversed([p for p in url.split("/") if p]):
        if seg.isdigit():
            return seg
    return url.rstrip("/").split("/")[-1]


def parse_rows(soup):
    """Extract (job_id, title, location, url) from an RMK results table.
    The board renders each posting as a row with a .jobTitle-link anchor and a
    sibling location cell; several class spellings are tried so a reskin does
    not silently return zero."""
    rows = []
    seen_local = set()

    anchors = (soup.select("a.jobTitle-link")
               or soup.select("[class*='jobTitle'] a")
               or soup.select("a[href*='/job/']"))

    for a in anchors:
        href = a.get("href") or ""
        if "/job/" not in href:
            continue
        title = _clean(a.get_text())
        if not title:
            continue
        url = href if href.startswith("http") else BASE + href
        job_id = _job_id_from_url(url)
        # The table repeats each title twice per row (mobile + desktop markup);
        # dedupe within the page so counts are honest.
        if job_id in seen_local:
            continue
        seen_local.add(job_id)

        # Location: look in the containing row, then fall back to siblings.
        location = ""
        row = a.find_parent("tr") or a.find_parent("li") or a.parent
        if row:
            for sel in ("span.jobLocation", ".jobLocation", "td.colLocation",
                        "[class*='ocation']"):
                el = row.select_one(sel)
                if el:
                    location = _clean(el.get_text())
                    if location:
                        break
        rows.append((job_id, title, location, url))
    return rows


def get_current_jobs():
    jobs = {}
    seen = set()
    scanned = 0
    total = None

    for page in range(MAX_PAGES):
        startrow = page * PAGE_SIZE
        params = {"q": "", "sortColumn": "referencedate",
                  "sortDirection": "desc"}
        if startrow:
            params["startrow"] = startrow

        r = _get(params)
        if r is None:
            # Transport failure. If we already have results, keep them; if this
            # was the first page, return None so the save is skipped entirely
            # rather than pruning every stored SPX row.
            return jobs if jobs else None

        soup = BeautifulSoup(r.text, "html.parser")
        if total is None:
            total = _total_results(soup)
            if total is not None:
                print(f"[{COMPANY_NAME}] board reports {total} total postings.")

        rows = parse_rows(soup)
        if not rows:
            if page == 0:
                print(f"[{COMPANY_NAME}] no job rows found -- the board layout "
                      f"changed. Check {SEARCH_URL} by hand.")
                return None
            break

        fresh = 0
        for job_id, title, location, url in rows:
            if job_id in seen:
                continue
            seen.add(job_id)
            fresh += 1
            scanned += 1

            if not is_internship_title(title):
                continue
            location = _normalize_location(location)
            if not location:
                print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
                continue
            if is_us_location(location, COMPANY_NAME):
                jobs[job_id] = {"title": title, "location": location,
                                "url": url}

        if fresh == 0:
            break
        if total is not None and scanned >= total:
            break
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}); results may be "
              f"incomplete.")

    print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()

    if current_jobs is None:
        print(f"[{COMPANY_NAME}] scrape failed; skipping save to protect "
              f"stored rows.")
        return

    new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
    if new_count > 0:
        print(f"[{COMPANY_NAME}] Added {new_count} new internships!")
    else:
        print(f"[{COMPANY_NAME}] No new internships.")
    if deleted_count > 0:
        print(f"[{COMPANY_NAME}] Removed {deleted_count} dead internships.")


if __name__ == "__main__":
    run_monitor()