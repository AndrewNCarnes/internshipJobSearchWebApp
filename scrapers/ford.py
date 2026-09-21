import sys
import os
import time

import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Ford"

# careers.ford.com is RADANCY, the same platform as Disney and L3Harris, and
# it serves a JSON endpoint whose "results" field is rendered card HTML.
#
# This replaces a Playwright version that walked ?p=N pages of the search URL
# with the site's own filters applied (k=intern&Country=...&orgIds=...). Those
# filters no longer work: the keyword search matches description text, so
# "intern" returned 19 postings -- German sales roles among them -- and zero
# actual internships, while the board holds 805 jobs. Filtering by title
# ourselves is the same approach every other scraper here uses.
#
# The old version also returned whatever it had collected when a page failed,
# which save_jobs would treat as the complete board and use to prune.

BASE = "https://www.careers.ford.com"
API = f"{BASE}/search-jobs/results"
PAGE_SIZE = 100
MAX_PAGES = 40
PAGE_DELAY = 0.8
TIMEOUT = 40
RETRIES = 3
PARAMS = {
    "ActiveFacetID": 0, "RecordsPerPage": PAGE_SIZE, "Distance": 50,
    "RadiusUnitType": 0, "Keywords": "", "Location": "", "ShowRadius": "False",
    "IsPagination": "True", "SearchResultsModuleName": "Search Results",
    "SearchFiltersModuleName": "Search Filters",
    "SortCriteria": 0, "SortDirection": 0, "SearchType": 5,
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*",
}


def _get_page(session, page):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(API, params={**PARAMS, "CurrentPage": page},
                            timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if "results" in data:
                    return data["results"] or ""
                last = "no results field"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] page {page} failed after {RETRIES} tries: {last}")
    return None


def _cards(html):
    for a in BeautifulSoup(html, "html.parser").select("a[data-job-id]"):
        h2 = a.find("h2")
        if not h2:
            continue
        loc = a.select_one(".job-location") or a.select_one("[class*='location']")
        href = a.get("href", "")
        yield {
            "id": a["data-job-id"],
            "title": h2.get_text(" ", strip=True),
            "location": " ".join((loc.get_text(" ", strip=True) if loc else "").split()),
            "url": href if href.startswith("http") else BASE + href,
        }


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    session = requests.Session()
    session.headers.update(HEADERS)
    jobs, seen = {}, set()

    for page in range(1, MAX_PAGES + 1):
        html = _get_page(session, page)
        if html is None:
            return None
        cards = [c for c in _cards(html) if c["id"] not in seen]
        if not cards:
            if page == 1:
                print(f"[{COMPANY_NAME}] no job cards on page 1; markup changed.")
                return None
            break
        for c in cards:
            seen.add(c["id"])
            if not is_internship_title(c["title"]):
                continue
            if not c["location"]:
                print(f"[{COMPANY_NAME}] no location for: {c['title'][:50]}")
                continue
            if is_us_location(c["location"], COMPANY_NAME):
                jobs[c["id"]] = {"title": c["title"], "location": c["location"],
                                 "url": c["url"]}
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}); results incomplete.")
        return None

    print(f"[{COMPANY_NAME}] scanned {len(seen)} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()
    if current_jobs is None:
        print(f"[{COMPANY_NAME}] scrape failed; skipping save to protect stored rows.")
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
