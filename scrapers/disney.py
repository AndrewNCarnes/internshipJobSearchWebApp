import sys
import os
import time

import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Disney"

# Requested as "Walt Disney Imagineering". WDI has no board of its own -- it
# posts on disneycareers.com (RADANCY/TMP, same platform as L3Harris) as one
# brand among many. We keep every US Disney internship and put the brand in
# the title, so "(Walt Disney Imagineering)" is visible and filterable on the
# dashboard. To restrict to WDI only, set BRANDS = {"Walt Disney Imagineering"}.
BRANDS = None

# Radancy serves a JSON endpoint whose "results" field is the rendered card
# HTML: <a data-job-id> <h2>title</h2> <span.job-brand> <span.job-location>.
# The site's data-total-results over-counts (178 vs 112 unique jobs for
# "intern" -- multi-location postings count once per site), so we page until a
# page comes back empty rather than trusting the total.

BASE = "https://www.disneycareers.com"
API = f"{BASE}/en/search-jobs/results"
KEYWORD = "intern"      # "internship"/"co-op" searches found nothing extra
PAGE_SIZE = 100
MAX_PAGES = 30
PAGE_DELAY = 0.8
TIMEOUT = 30
RETRIES = 3
PARAMS = {
    "ActiveFacetID": 0, "RecordsPerPage": PAGE_SIZE, "Distance": 50,
    "SearchResultsModuleName": "Search Results",
    "SearchFiltersModuleName": "Search Filters",
    "SortCriteria": 0, "SortDirection": 0, "SearchType": 5, "Keywords": KEYWORD,
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
        brand = a.select_one(".job-brand")
        loc = a.select_one(".job-location")
        yield {
            "id": a["data-job-id"],
            "title": h2.get_text(" ", strip=True),
            "brand": brand.get_text(" ", strip=True) if brand else "",
            # "Lake Buena Vista,  Florida / Glendale,  California"
            "location": " ".join((loc.get_text(" ", strip=True) if loc else "").split()),
            "url": BASE + a.get("href", ""),
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
            if BRANDS and c["brand"] not in BRANDS:
                continue
            if not is_internship_title(c["title"]):
                continue
            sites = [s.strip() for s in c["location"].split(" / ") if s.strip()]
            us = [s for s in sites if is_us_location(s, COMPANY_NAME)]
            if us:
                title = f"{c['title']} ({c['brand']})" if c["brand"] else c["title"]
                jobs[c["id"]] = {"title": title, "location": " | ".join(us),
                                 "url": c["url"]}
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES; results incomplete.")
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
