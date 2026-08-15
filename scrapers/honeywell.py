import sys
import os
import time
import uuid
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Honeywell"
MAX_PAGES = 150   # safety fuse only; normal exit is offset >= total
PAGE_DELAY = 1.5  # seconds between pages; raise if you start seeing errors
DOMAIN = "ibqbjb.fa.ocs.oraclecloud.com"
SITE_NUMBER = "Honeywell"
BASE_JOB_URL = f"https://{DOMAIN}/hcmUI/CandidateExperience/en/sites/{SITE_NUMBER}/job/"

HEADERS = {
    "Accept": "application/json",
    "ora-irc-cx-userid": str(uuid.uuid4()),
}


def build_url(limit, offset):
    """Oracle HCM wants limit/offset inside an unencoded 'finder' string."""
    finder = (f"findReqs;siteNumber={SITE_NUMBER},keyword=intern,"
              f"limit={limit},offset={offset}")
    return (f"https://{DOMAIN}/hcmRestApi/resources/latest/"
            f"recruitingCEJobRequisitions?expand=requisitionList&onlyData=true"
            f"&finder={finder}")


def extract_locations(job):
    """
    Oracle splits location across several fields. PrimaryLocation is usually
    enough, but multi-site postings put the rest in secondaryLocations, and
    some tenants only fill PrimaryLocationCountry. Gather whatever exists so
    the filter sees every site, not just the first.
    """
    parts = []
    primary = job.get("PrimaryLocation") or ""
    if primary:
        parts.append(primary)

    for key in ("secondaryLocations", "SecondaryLocations", "workLocations"):
        extra = job.get(key) or []
        if isinstance(extra, list):
            for e in extra:
                if isinstance(e, dict):
                    name = e.get("Name") or e.get("LocationName") or ""
                    if name:
                        parts.append(name)
                elif isinstance(e, str) and e:
                    parts.append(e)

    country = job.get("PrimaryLocationCountry") or ""
    if country and not parts:
        parts.append(country)

    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def get_current_jobs():
    jobs = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0")

        try:
            offset = 0
            limit = 25
            pages = 0
            total = None

            while True:
                response = context.request.get(build_url(limit, offset),
                                               headers=HEADERS)

                if not response.ok:
                    print(f"Error fetching {COMPANY_NAME}: "
                          f"{response.status} {response.status_text}")
                    break

                data = response.json()
                items = data.get("items", [])
                if not items:
                    break

                # Oracle reports the match count, same idea as Workday's
                # "total" -- use it so we stop when the results run out
                # instead of paging until the MAX_PAGES fuse blows.
                if total is None:
                    total = items[0].get("TotalJobsCount")
                    if isinstance(total, int) and total > 0:
                        print(f"[{COMPANY_NAME}] {total} results to page through.")

                req_list = items[0].get("requisitionList", [])
                if not req_list:
                    break

                for job in req_list:
                    title = job.get("Title", "")
                    job_id = str(job.get("Id", ""))
                    if not is_internship_title(title):
                        continue

                    locations = extract_locations(job)
                    if not locations:
                        print(f"[{COMPANY_NAME}] no location on posting: {title}")
                        continue

                    if any(is_us_location(loc, COMPANY_NAME) for loc in locations):
                        jobs[job_id] = {
                            "title": title,
                            "location": " | ".join(locations),
                            "url": BASE_JOB_URL + job_id,
                        }

                offset += limit
                pages += 1

                if isinstance(total, int) and total and pages % 10 == 0:
                    pct = min(100, round(100 * offset / total))
                    print(f"[{COMPANY_NAME}] {pct}% | {min(offset, total)}/{total} "
                          f"| {len(jobs)} matches so far")

                if isinstance(total, int) and offset >= total:
                    break
                if pages >= MAX_PAGES:
                    print(f"[{COMPANY_NAME}] ⚠️  hit MAX_PAGES ({MAX_PAGES}) at "
                          f"{offset}/{total} results -- RESULTS ARE INCOMPLETE. "
                          f"Raise MAX_PAGES.")
                    break

                time.sleep(PAGE_DELAY)

        except Exception as e:
            print(f"Error scraping {COMPANY_NAME}: {e}")
        finally:
            browser.close()

    return jobs


def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()

    if current_jobs is not None:
        n, d = database.save_jobs(COMPANY_NAME, current_jobs)
        if n > 0: print(f"[{COMPANY_NAME}] Added {n} new internships!")
        else: print(f"[{COMPANY_NAME}] No new internships.")
        if d > 0: print(f"[{COMPANY_NAME}] Removed {d} dead internships.")


if __name__ == "__main__":
    run_monitor()