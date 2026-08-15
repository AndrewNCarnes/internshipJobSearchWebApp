import sys
import os
import time
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Lockheed Martin"
BASE = "https://lockheedmartin.eightfold.ai"
CAREERS_URL = f"{BASE}/careers?hl=en-US&start=0&sort_by=timestamp"
SEARCH_URL = (f"{BASE}/api/pcsx/search?domain=lockheedmartin.com"
              "&query={query}&location=&start={start}&num={num}"
              "&sort_by=timestamp&hl=en-US")

PAGE_SIZE = 10     # PCSX default; the code adapts if the server returns more
MAX_PAGES = 120
PAGE_DELAY = 1.0

# Lockheed left lockheedmartinjobs.com (now a "site under construction" page)
# and moved to Eightfold's PCSX platform. Notes from probing the new API:
#   * jobs live at data.positions; there is NO total count anywhere, so paging
#     has to stop on a short/empty page rather than on offset >= total
#   * the old /api/apply/v2/jobs endpoint returns 403 "Not authorized for PCSX"
#   * requests need session cookies from a real page load first
#   * standardizedLocations ("Stratford, CT, US") is cleaner than the raw
#     locations ("Stratford,US-CT,United States"); both are arrays
#
# CRAWL_ALL: a server-side query=intern returned only 2 results, neither of
# them an internship, so Lockheed's relevance search cannot be trusted to find
# them. Crawling everything and filtering titles locally is slower but honest.
# Set False to use the server query if it improves later.
CRAWL_ALL = True


def get_locations(pos):
    """Prefer the standardized strings; fall back to the raw ones."""
    for key in ("standardizedLocations", "locations"):
        vals = pos.get(key) or []
        if isinstance(vals, list):
            out = [v for v in vals if isinstance(v, str) and v.strip()]
            if out:
                return out
    return []


def get_current_jobs():
    jobs = {}
    seen_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            # PCSX rejects cold requests; load the careers page for cookies.
            page.goto(CAREERS_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            query = "" if CRAWL_ALL else "intern"
            start = 0
            pages = 0
            scanned = 0
            page_size = PAGE_SIZE

            while pages < MAX_PAGES:
                url = SEARCH_URL.format(query=query, start=start, num=page_size)
                r = context.request.get(url, headers={"Accept": "application/json"})

                if not r.ok:
                    print(f"[{COMPANY_NAME}] stopped at {scanned} listings: "
                          f"HTTP {r.status} {r.status_text}")
                    break

                try:
                    positions = (r.json().get("data") or {}).get("positions") or []
                except Exception as e:
                    print(f"[{COMPANY_NAME}] unreadable response at start={start}: {e}")
                    break

                if not positions:
                    break

                # The API may honour &num or ignore it; learn the real page size
                # from the first response so paging steps by the right amount.
                if pages == 0 and len(positions) > page_size:
                    page_size = len(positions)

                fresh = 0
                for pos in positions:
                    job_id = str(pos.get("id") or pos.get("atsJobId") or "")
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    fresh += 1
                    scanned += 1

                    title = pos.get("name", "")
                    if not is_internship_title(title):
                        continue

                    locations = get_locations(pos)
                    if not locations:
                        print(f"[{COMPANY_NAME}] no location on posting: {title[:50]}")
                        continue
                    if not any(is_us_location(loc, COMPANY_NAME) for loc in locations):
                        continue

                    position_url = pos.get("positionUrl") or f"/careers/job/{job_id}"
                    jobs[job_id] = {
                        "title": title,
                        "location": " | ".join(locations),
                        "url": position_url if position_url.startswith("http")
                               else BASE + position_url,
                    }

                if fresh == 0:          # server ignored start= and repeated a page
                    break

                start += page_size
                pages += 1

                if pages % 10 == 0:
                    print(f"[{COMPANY_NAME}] {scanned} listings scanned | "
                          f"{len(jobs)} internships so far")

                if len(positions) < page_size:   # short page = last page
                    break

                time.sleep(PAGE_DELAY)

            if pages >= MAX_PAGES:
                print(f"[{COMPANY_NAME}] ⚠️  hit MAX_PAGES ({MAX_PAGES}) after "
                      f"{scanned} listings -- RESULTS MAY BE INCOMPLETE.")

            print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
                  f"{len(jobs)} are US internships.")

        except Exception as e:
            print(f"Error scraping {COMPANY_NAME}: {e}")
        finally:
            browser.close()

    return jobs


def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()

    if current_jobs is not None:
        new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
        if new_count > 0: print(f"[{COMPANY_NAME}] Added {new_count} new internships!")
        else: print(f"[{COMPANY_NAME}] No new internships.")
        if deleted_count > 0: print(f"[{COMPANY_NAME}] Removed {deleted_count} dead internships.")


if __name__ == "__main__":
    run_monitor()