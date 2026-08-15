import sys
import os
import time

from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Kraft Heinz"

# Kraft Heinz runs on Eightfold. The careers page renders client-side, but it
# fetches its postings from an internal JSON endpoint (/api/apply/v2/jobs), so
# rather than scrape the DOM we let the page load and eavesdrop on that call --
# clean structured data, no selectors to break on a reskin.
#
# UPDATED: this scraper originally carried its own private is_us_location() and
# a bare \bintern title regex. Both were replaced with the shared filters, which
# fixed real losses confirmed against live-style data:
#
#   "Milwaukee, Wisconsin"    was DROPPED -- "uk" matched Milwa-UK-ee
#   "Indianapolis, Indiana"   was DROPPED -- "india" matched INDIA-napolis
#   "Madison, Wisconsin"      was DROPPED -- Wisconsin wasn't in the 11-state list
#   "Davenport, Iowa"         was DROPPED -- Iowa wasn't either
#   "International Brand Manager" was KEPT -- \bintern has no closing boundary
#   "Manufacturing Co-op"     was DROPPED -- co-ops weren't matched at all
#
# The old whitelist covered 11 states; location_filter covers all 50 plus DC and
# territories, and orders its checks so a US state beats a same-named foreign
# city. Never reintroduce a local copy of this logic.

URL = "https://jobs.kraftheinz.com/careers?query=intern"
BASE_JOB_URL = "https://jobs.kraftheinz.com/careers/job/"
API_MARKER = "/api/apply/v2/jobs"

NAV_TIMEOUT = 30000
SETTLE_MS = 5000


def get_current_jobs():
    """Returns {job_id: {...}} on success, or None if the scrape failed.

    The None case matters: database.save_jobs deletes every stored row we don't
    return, so a navigation timeout that yielded an empty dict would silently
    wipe all Kraft Heinz internships. Previously this returned {} on any
    exception. Now a failure returns None and run_monitor skips the save.
    """
    jobs = {}
    intercepted = []
    failed = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_response(response):
            if API_MARKER in response.url and response.request.method == "GET":
                try:
                    data = response.json()
                except Exception:
                    return
                positions = data.get("positions") or []
                if positions:
                    intercepted.extend(positions)

        page.on("response", handle_response)

        try:
            print(f"[{COMPANY_NAME}] Loading careers page and intercepting "
                  f"background API...")
            page.goto(URL, wait_until="networkidle", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(SETTLE_MS)
        except Exception as e:
            print(f"[{COMPANY_NAME}] page load failed: {e}")
            failed = True
        finally:
            browser.close()

    if failed and not intercepted:
        return None

    if not intercepted:
        # Page loaded but the API call never fired or changed shape. That is a
        # scraper problem, not "Kraft Heinz has no internships" -- don't prune.
        print(f"[{COMPANY_NAME}] no jobs intercepted from {API_MARKER}; "
              f"the endpoint may have changed. Skipping save.")
        return None

    scanned = 0
    for job in intercepted:
        if not isinstance(job, dict):
            continue
        scanned += 1
        title = (job.get("name") or "").strip()
        location = (job.get("location") or "").strip()
        job_id = str(job.get("id") or "").strip()
        if not title or not job_id:
            continue

        if not is_internship_title(title):
            continue
        if not location:
            print(f"[{COMPANY_NAME}] no location for: {title[:50]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            jobs[job_id] = {
                "title": title,
                "location": location,
                "url": BASE_JOB_URL + job_id,
            }

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

    n, d = database.save_jobs(COMPANY_NAME, current_jobs)
    if n > 0:
        print(f"[{COMPANY_NAME}] Added {n} new internships!")
    else:
        print(f"[{COMPANY_NAME}] No new internships.")
    if d > 0:
        print(f"[{COMPANY_NAME}] Removed {d} dead internships.")


if __name__ == "__main__":
    run_monitor()