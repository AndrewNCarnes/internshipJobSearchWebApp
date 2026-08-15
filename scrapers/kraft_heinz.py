import sys
import os
import time

from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Kraft Heinz"

# Kraft Heinz runs on Eightfold, which MIGRATED off the old careers API.
# The previous version of this scraper listened for "/api/apply/v2/jobs" and
# silently intercepted nothing -- that endpoint no longer fires. A network probe
# of the live page showed the current one:
#
#   GET /api/pcsx/search?domain=kraftheinz.com&query=intern&location=&start=0&
#   -> {"status":..., "error":..., "data": {...}, "metadata": {...}}
#
# Rather than scroll the page and eavesdrop, we load the page once (to pick up
# cookies + any bot-check the site sets), then call that endpoint directly
# through Playwright's request context, paging on `start`. That gets the whole
# result set instead of only what lazy-rendered.
#
# The exact key holding the job array under "data" is not documented and has
# already changed once, so _find_jobs walks the response recursively for the
# first list of dicts that look like postings. That survives another rename.
#
# Filters: uses the shared location_filter. This scraper used to carry its own
# is_us_location with an 11-state whitelist and substring matching, which
# dropped "Milwaukee, Wisconsin" ("uk" matched Milwa-UK-ee) and "Indianapolis,
# Indiana" ("india" matched INDIA-napolis), and a bare \bintern regex that kept
# "International Brand Manager". Never reintroduce a local copy.

BASE = "https://jobs.kraftheinz.com"
PAGE_URL = f"{BASE}/careers?query=intern"
API = f"{BASE}/api/pcsx/search"
DOMAIN = "kraftheinz.com"
QUERY = "intern"

PAGE_SIZE = 10          # what the site itself requests
MAX_PAGES = 40          # hard cap: 400 postings
NAV_TIMEOUT = 60000
SETTLE_MS = 5000

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Keys that identify a posting object, whatever the wrapper is called.
TITLE_KEYS = ("name", "title", "position_name", "displayJobTitle")
ID_KEYS = ("id", "position_id", "positionId", "jobId")
LOC_KEYS = ("location", "locations", "display_location", "city_state_country",
            "work_location")


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def _looks_like_job(obj):
    return (isinstance(obj, dict)
            and _first(obj, TITLE_KEYS)
            and _first(obj, ID_KEYS))


def _find_jobs(node, depth=0):
    """Recursively locate the first list of posting-shaped dicts."""
    if depth > 6:
        return None
    if isinstance(node, list):
        if node and _looks_like_job(node[0]):
            return node
        for item in node:
            found = _find_jobs(item, depth + 1)
            if found:
                return found
        return None
    if isinstance(node, dict):
        for value in node.values():
            found = _find_jobs(value, depth + 1)
            if found:
                return found
    return None


def _location_of(job):
    v = job.get("location") or job.get("locations")
    if isinstance(v, list) and v:
        parts = []
        for item in v:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, dict):
                s = _first(item, ("name", "location", "city"))
                if s:
                    parts.append(s)
        if parts:
            return " | ".join(dict.fromkeys(parts))
    return _first(job, LOC_KEYS)


def get_current_jobs():
    """Returns {job_id: {...}} on success, or None if the scrape failed.

    None matters: database.save_jobs deletes every stored row we don't return,
    so an endpoint change that yields nothing must NOT look like "0 openings".
    That is exactly what happened when Eightfold retired the old API.
    """
    raw = []
    failed = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA)
        page = context.new_page()

        try:
            print(f"[{COMPANY_NAME}] Loading careers page to establish "
                  f"session...")
            page.goto(PAGE_URL, wait_until="domcontentloaded",
                      timeout=NAV_TIMEOUT)
            page.wait_for_timeout(SETTLE_MS)

            for i in range(MAX_PAGES):
                start = i * PAGE_SIZE
                url = (f"{API}?domain={DOMAIN}&query={QUERY}"
                       f"&location=&start={start}&")
                resp = context.request.get(url, timeout=30000)
                if resp.status != 200:
                    print(f"[{COMPANY_NAME}] search HTTP {resp.status} at "
                          f"start={start}")
                    if i == 0:
                        failed = True
                    break
                try:
                    payload = resp.json()
                except Exception as e:
                    print(f"[{COMPANY_NAME}] non-JSON at start={start}: {e}")
                    if i == 0:
                        failed = True
                    break

                batch = _find_jobs(payload)
                if batch is None:
                    if i == 0:
                        print(f"[{COMPANY_NAME}] could not locate a job list in "
                              f"the response; PCSX shape changed again.")
                        failed = True
                    break
                if not batch:
                    break

                raw.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
                time.sleep(0.6)

        except Exception as e:
            print(f"[{COMPANY_NAME}] page/API error: {e}")
            failed = True
        finally:
            browser.close()

    if failed and not raw:
        return None
    if not raw:
        print(f"[{COMPANY_NAME}] no postings returned by {API}; "
              f"skipping save rather than pruning.")
        return None

    jobs = {}
    scanned = 0
    for job in raw:
        if not isinstance(job, dict):
            continue
        scanned += 1
        title = _first(job, TITLE_KEYS)
        job_id = _first(job, ID_KEYS)
        if not title or not job_id:
            continue
        if job_id in jobs:
            continue
        if not is_internship_title(title):
            continue
        location = _location_of(job)
        if not location:
            print(f"[{COMPANY_NAME}] no location for: {title[:50]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            jobs[job_id] = {
                "title": title,
                "location": location,
                # PCSX deep-links by pid on the careers page itself.
                "url": f"{BASE}/careers?pid={job_id}",
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