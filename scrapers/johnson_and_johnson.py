import sys
import os
import re
import time
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

COMPANY_NAME = "Johnson & Johnson"
MAX_PAGES = 150   # safety fuse only; normal exit is offset >= total
PAGE_DELAY = 1.5  # seconds between pages; raise if you start seeing 403s
API_BASE = "https://jj.wd5.myworkdayjobs.com/wday/cxs/jj/JJ"
API_URL = f"{API_BASE}/jobs"
BASE_JOB_URL = "https://jj.wd5.myworkdayjobs.com/en-US/JJ"

# Workday/Cloudflare returns 403 without Origin and Referer.
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://jj.wd5.myworkdayjobs.com",
    "Referer": BASE_JOB_URL,
}

INTERN_FACET_PARAMS = ("workerSubType", "jobType")

# Below this, the employer clearly isn't tagging its internships and the facet
# can't be trusted. Observed: RTX 49 and Boeing 16 (both trusted and correct),
# Blue Origin 1 and Northrop 2 (both rejected -- text search finds far more).
# Falling back costs time; trusting a bad facet costs postings.
#
# J&J matters most for the location filter: it is the only one of the five with
# heavy Latin America and EMEA hiring, and its old scraper let Buenos Aires,
# Bogota and Panama into the database.
MIN_FACET_TRUST = 5


def find_intern_facet(context):
    """
    Ask Workday for its own facets and locate the employer's "Intern/Co-op"
    classification -- a structured field, not a text match on the description.

    Returns (param, [ids], count) or (None, None, None).
    """
    try:
        r = context.request.post(API_URL, headers=HEADERS, data={
            "appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "intern"})
        if not r.ok:
            return None, None, None
        for f in r.json().get("facets", []) or []:
            param = f.get("facetParameter")
            if param not in INTERN_FACET_PARAMS:
                continue
            ids, count = [], 0
            for v in f.get("values", []) or []:
                desc = (v.get("descriptor") or "").lower()
                if re.search(r"\bintern|co-?op", desc):
                    ids.append(v.get("id"))
                    count += v.get("count") or 0
            if ids:
                return param, ids, count
    except Exception as e:
        print(f"[{COMPANY_NAME}] facet probe failed ({e}); falling back to text search.")
    return None, None, None


def resolve_locations(context, external_path):
    """
    Workday collapses a multi-site posting into "9 Locations" -- a string with
    no state in it, which no location filter can pass. The real list only
    appears on the job's own detail endpoint, so fetch it for those postings.

    Returns a display string, or "" if the lookup fails.
    """
    try:
        r = context.request.get(API_BASE + external_path, headers=HEADERS)
        if not r.ok:
            return ""
        info = r.json().get("jobPostingInfo", {}) or {}
        parts = []
        primary = info.get("location", "")
        if primary:
            parts.append(primary)
        extra = info.get("additionalLocations", []) or []
        parts.extend(x for x in extra if x)
        return " | ".join(parts)
    except Exception:
        return ""


def get_current_jobs():
    jobs = {}
    pending_multi = []   # (job_id, title, external_path) needing a detail lookup

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        facet_param, facet_ids, facet_count = find_intern_facet(context)
        use_facet = bool(facet_ids) and facet_count >= MIN_FACET_TRUST
        if use_facet:
            applied = {facet_param: facet_ids}
            search_text = ""
            print(f"[{COMPANY_NAME}] using {facet_param} facet -- {facet_count} intern postings.")
        else:
            applied = {}
            search_text = "intern"
            if facet_ids:
                print(f"[{COMPANY_NAME}] facet reports only {facet_count} interns "
                      f"-- too few to trust; using text search instead.")
            else:
                print(f"[{COMPANY_NAME}] no intern facet found; falling back to text search.")

        try:
            offset = 0
            limit = 20      # Workday rejects limit > 20 on these tenants
            pages = 0
            total = None

            while True:
                response = context.request.post(
                    API_URL,
                    headers=HEADERS,
                    data={
                        "appliedFacets": applied,
                        "limit": limit,
                        "offset": offset,
                        "searchText": search_text,
                    },
                )

                if not response.ok:
                    print(f"Error fetching {COMPANY_NAME}: {response.status} {response.status_text}")
                    break

                data = response.json()
                postings = data.get("jobPostings", [])

                # Workday reports how many results exist. Using it means we stop
                # when the result set is exhausted instead of paging until the
                # MAX_PAGES fuse blows.
                if total is None:
                    total = data.get("total")
                    if isinstance(total, int) and total > 0:
                        print(f"[{COMPANY_NAME}] {total} results to page through.")

                if not postings:
                    break

                for job in postings:
                    title = job.get("title", "")
                    location = job.get("locationsText", "")

                    # The employer's tag is evidence, not proof: J&J files
                    # Postdoctoral Scholars under Intern/Co-op too. So facet
                    # postings still get a seniority check, while untagged ones
                    # fall back to reading the title.
                    if use_facet:
                        if not is_plausible_internship(title):
                            continue
                    elif not is_internship_title(title):
                        continue

                    external_path = job.get("externalPath", "")
                    job_id = external_path.split("/")[-1] if external_path else title

                    # "3 Locations" / "9 Locations" -> resolve later
                    if location.strip().lower().endswith("locations"):
                        pending_multi.append((job_id, title, external_path))
                        continue

                    if is_us_location(location, COMPANY_NAME):
                        jobs[job_id] = {
                            "title": title,
                            "location": location,
                            "url": BASE_JOB_URL + external_path,
                        }

                offset += limit
                pages += 1

                if isinstance(total, int) and total and pages % 10 == 0:
                    pct = min(100, round(100 * offset / total))
                    print(f"[{COMPANY_NAME}] {pct}% | {min(offset, total)}/{total} "
                          f"| {len(jobs) + len(pending_multi)} matches so far")

                if isinstance(total, int) and offset >= total:
                    break
                if pages >= MAX_PAGES:
                    print(f"[{COMPANY_NAME}] ⚠️  hit MAX_PAGES ({MAX_PAGES}) at "
                          f"{offset}/{total} results -- RESULTS ARE INCOMPLETE. "
                          f"Raise MAX_PAGES.")
                    break

                time.sleep(PAGE_DELAY)

            # --- second pass: expand the multi-location postings --------------
            if pending_multi:
                print(f"[{COMPANY_NAME}] resolving {len(pending_multi)} multi-location posting(s)...")
                for job_id, title, external_path in pending_multi:
                    resolved = resolve_locations(context, external_path)
                    if not resolved:
                        print(f"[{COMPANY_NAME}] could not resolve locations for: {title}")
                        continue
                    if any(is_us_location(part, COMPANY_NAME)
                           for part in resolved.split(" | ")):
                        jobs[job_id] = {
                            "title": title,
                            "location": resolved,
                            "url": BASE_JOB_URL + external_path,
                        }
                    time.sleep(1)

        except Exception as e:
            print(f"Error scraping {COMPANY_NAME} API with Playwright: {e}")
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