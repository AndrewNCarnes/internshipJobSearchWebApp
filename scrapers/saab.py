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

COMPANY_NAME = "Saab"

# Saab's careers page is a custom Optimizely/Episerver site, not a mainstream ATS
# (no Workday/Greenhouse/SuccessFactors endpoint to hit). The good news: it is
# fully SERVER-RENDERED and returns the ENTIRE board in a single request --
# confirmed live, "Listing 580 job openings" with every row present in the HTML.
# No pagination, no JS, no browser needed. One GET does it.
#
# Each row carries: position type, location, title (linked), closing date.
# Job URLs are slugs -- /career/job-opportunities/lead-stress-engineer -- with no
# numeric requisition id anywhere, so the slug IS the stable key.
#
# THREE DATA HAZARDS, all confirmed against the real location_filter:
#
# 1. "Multiple Locations" -- extremely common on this board. location_filter
#    lists "multiple locations" under REMOTE_SIGNALS, so it returns True. Saab is
#    Sweden-heavy (409 Swedish postings vs 69 US), so trusting that would import
#    Linkoping jobs as US. These rows name no country at all, so there is no way
#    to tell a US multi-site role from a Swedish one without opening each posting.
#    Default is to SKIP them; see SKIP_MULTIPLE_LOCATIONS below.
#
# 2. Bare foreign city names -- "Linkoping", "Solna", "Nurnberg", "Adelaide",
#    "Thun", "Ottawa". These fall through to the filter's step-5 "unrecognized"
#    branch and are correctly dropped. Because the title filter runs FIRST, this
#    only prints for internship-titled rows, so the log stays quiet.
#
# 3. Ambiguous city names -- "Bristol" alone is Saab's UK site, but
#    "Bristol, Rhode Island" is US. The filter's US-state-before-foreign-city
#    ordering already resolves this correctly. Same for Melbourne (AU) vs a
#    hypothetical Melbourne FL. No special handling needed; do not "fix" it.
#
# Saab also runs a separate Students and Graduates page. Swedish student roles
# here are titled "Examensarbete" / "Master's Thesis" / "Werkstudent", which
# is_internship_title does not match -- correct, since those are not US intern
# postings.

BASE = "https://www.saab.com"
JOBS_URL = f"{BASE}/career/job-opportunities"
JOB_PATH = "/career/job-opportunities/"

# "Multiple Locations" rows name no country. On a board that is 70% Swedish,
# treating them as remote-US would poison the database. Set to False only if you
# would rather see false positives than miss a US multi-site internship.
SKIP_MULTIPLE_LOCATIONS = True
AMBIGUOUS_LOCATIONS = {"multiple locations", "various locations", "flexible"}

TIMEOUT = 45          # the full 580-row page is a big document
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def _clean(text):
    if not text:
        return ""
    # \ufeff (BOM) appears inside Saab's title text, e.g. "Lead Stress Engineer\ufeff"
    text = str(text).replace("\ufeff", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _get():
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(JOBS_URL, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"[{COMPANY_NAME}] request failed after {RETRIES} tries: {last}")
    return None


def _listed_total(soup):
    """Read 'Listing 580 job openings' so we can tell a real board size from a
    truncated parse. Returns None if the banner is missing."""
    m = re.search(r"Listing\s+([\d,]+)\s+job", soup.get_text(" ", strip=True), re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _slug_of(url):
    """No numeric ids on this site -- the URL slug is the stable key."""
    return url.rstrip("/").split("/")[-1]


def _location_from_row(a):
    """Saab renders each posting as a row of small fields (position type,
    location, title link, closing date). Markup class names are not documented
    and may change, so try explicit selectors first, then fall back to reading
    the row's text cells and picking the one that is neither the title nor the
    date nor the position-type."""
    row = a
    for _ in range(4):                     # climb a bounded number of levels
        row = row.parent
        if row is None:
            return ""
        for sel in ("[class*='ocation']", "[class*='Location']",
                    "td.location", "span.location"):
            el = row.select_one(sel)
            if el:
                loc = _clean(el.get_text())
                if loc:
                    return loc
        # Fallback: treat the row's direct children as cells.
        cells = [_clean(c.get_text()) for c in row.find_all(
            ["td", "div", "span", "p"], recursive=False)]
        cells = [c for c in cells if c]
        title = _clean(a.get_text())
        if len(cells) >= 3:
            cands = [c for c in cells
                     if c != title
                     and title not in c
                     and not re.match(r"^\d{1,2}\s+\w+\s+\d{4}$", c)]
            if cands:
                # Position type comes first, location second.
                return cands[1] if len(cands) > 1 else cands[0]
    return ""


def parse_listings(soup):
    """Return [(slug, title, location, url)] for every posting on the page."""
    rows = []
    seen = set()
    for a in soup.select(f"a[href*='{JOB_PATH}']"):
        href = a.get("href") or ""
        if JOB_PATH not in href:
            continue
        url = href if href.startswith("http") else BASE + href
        slug = _slug_of(url)
        # The index page links to itself in nav/sitemap; skip those.
        if not slug or slug == "job-opportunities":
            continue
        if slug in seen:
            continue
        title = _clean(a.get_text())
        if not title:
            continue
        seen.add(slug)
        rows.append((slug, title, _location_from_row(a), url))
    return rows


def get_current_jobs():
    r = _get()
    if r is None:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    total = _listed_total(soup)
    rows = parse_listings(soup)

    if not rows:
        print(f"[{COMPANY_NAME}] no postings parsed -- the page layout "
              f"changed. Check {JOBS_URL} by hand.")
        return None

    if total is not None:
        print(f"[{COMPANY_NAME}] page reports {total} openings; parsed {len(rows)}.")
        # A large shortfall means the board started paginating or lazy-loading;
        # say so rather than silently reporting fewer internships.
        if len(rows) < total * 0.9:
            print(f"[{COMPANY_NAME}] only parsed {len(rows)}/{total} rows -- "
                  f"the page may now lazy-load. Results may be incomplete.")
    else:
        print(f"[{COMPANY_NAME}] parsed {len(rows)} postings.")

    jobs = {}
    skipped_ambiguous = 0
    for slug, title, location, url in rows:
        if not is_internship_title(title):
            continue
        if not location:
            print(f"[{COMPANY_NAME}] no location parsed for: {title[:55]}")
            continue
        if SKIP_MULTIPLE_LOCATIONS and location.lower() in AMBIGUOUS_LOCATIONS:
            # Named no country; on this board that is usually Sweden.
            skipped_ambiguous += 1
            print(f"[{COMPANY_NAME}] ambiguous location '{location}', skipping: "
                  f"{title[:45]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            jobs[slug] = {"title": title, "location": location, "url": url}

    if skipped_ambiguous:
        print(f"[{COMPANY_NAME}] skipped {skipped_ambiguous} intern postings "
              f"with an ambiguous location.")
    print(f"[{COMPANY_NAME}] scanned {len(rows)} listings; "
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