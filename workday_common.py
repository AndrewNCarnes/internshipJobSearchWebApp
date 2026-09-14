"""
workday_common.py -- shared logic for every Workday-based scraper.

Extracted verbatim in behaviour from the proven scrapers/blue_origin.py, which
solved the two problems every Workday tenant has:

  1. FACETS LIE. Workday exposes a workerSubType/jobType facet for
     "Intern (Fixed Term)", but employers tag inconsistently. Blue Origin's
     facet reported 1 intern while a text search found 17 -- trusting it would
     have deleted 16 real postings. So we only trust the facet when it reports
     at least MIN_FACET_TRUST hits, and fall back to a text search otherwise.

  2. MULTI-SITE POSTINGS COLLAPSE. A posting open at several sites shows
     locationsText as "9 Locations" -- a string with no state in it, which no
     location filter can pass. The real list only exists on the job's own
     detail endpoint, so those get a second-pass lookup.

Usage from a per-company scraper:

    from workday_common import scrape_workday
    jobs = scrape_workday(COMPANY_NAME, TENANT, SITE, wd="wd5")

Returns {job_id: {...}} on success, or None on failure so the caller can skip
the save. Note this differs from blue_origin.py, which returned a partial dict
on exception -- meaning a mid-run crash could look like "these are all the
jobs" and prune the rest. Here, a failure before any results is None.
"""
import re
import time

from playwright.sync_api import sync_playwright

from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

MAX_PAGES = 150
PAGE_DELAY = 1.5
LIMIT = 20                 # Workday rejects limit > 20 on these tenants
MIN_FACET_TRUST = 5
INTERN_FACET_PARAMS = ("workerSubType", "jobType")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _endpoints(tenant, site, wd):
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api_base = f"{host}/wday/cxs/{tenant}/{site}"
    return {
        "host": host,
        "api_base": api_base,
        "api": f"{api_base}/jobs",
        "job_base": f"{host}/en-US/{site}",
        # Workday/Cloudflare returns 403 without Origin and Referer.
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": host,
            "Referer": f"{host}/en-US/{site}",
        },
    }


def _find_intern_facet(context, ep, company):
    """Locate the employer's own Intern/Co-op classification.
    Returns (param, [ids], count) or (None, None, None)."""
    try:
        r = context.request.post(ep["api"], headers=ep["headers"], data={
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
        print(f"[{company}] facet probe failed ({e}); using text search.")
    return None, None, None


def _resolve_locations(context, ep, external_path):
    """Expand a '9 Locations' posting via its detail endpoint."""
    try:
        r = context.request.get(ep["api_base"] + external_path,
                                headers=ep["headers"])
        if not r.ok:
            return ""
        info = r.json().get("jobPostingInfo", {}) or {}
        parts = []
        primary = info.get("location", "")
        if primary:
            parts.append(primary)
        parts.extend(x for x in (info.get("additionalLocations") or []) if x)
        return " | ".join(parts)
    except Exception:
        return ""


def scrape_workday(company, tenant, site, wd="wd5"):
    ep = _endpoints(tenant, site, wd)
    jobs = {}
    pending_multi = []
    hard_failure = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA)

        facet_param, facet_ids, facet_count = _find_intern_facet(
            context, ep, company)
        use_facet = bool(facet_ids) and facet_count >= MIN_FACET_TRUST
        if use_facet:
            applied, search_text = {facet_param: facet_ids}, ""
            print(f"[{company}] using {facet_param} facet -- "
                  f"{facet_count} intern postings.")
        else:
            applied, search_text = {}, "intern"
            if facet_ids:
                print(f"[{company}] facet reports only {facet_count} interns "
                      f"-- too few to trust; using text search.")
            else:
                print(f"[{company}] no intern facet; using text search.")

        try:
            offset = pages = 0
            total = None
            while True:
                resp = context.request.post(ep["api"], headers=ep["headers"],
                                            data={"appliedFacets": applied,
                                                  "limit": LIMIT,
                                                  "offset": offset,
                                                  "searchText": search_text})
                if not resp.ok:
                    print(f"[{company}] HTTP {resp.status} {resp.status_text}")
                    if offset == 0:
                        hard_failure = True
                    break

                data = resp.json()
                postings = data.get("jobPostings", [])
                if total is None:
                    total = data.get("total")
                    if isinstance(total, int) and total > 0:
                        print(f"[{company}] {total} results to page through.")
                if not postings:
                    break

                for job in postings:
                    title = job.get("title", "")
                    location = job.get("locationsText", "")

                    # A facet tag is evidence, not proof -- J&J files
                    # Postdoctoral Scholars under Intern/Co-op too.
                    if use_facet:
                        if not is_plausible_internship(title):
                            continue
                    elif not is_internship_title(title):
                        continue

                    external_path = job.get("externalPath", "")
                    job_id = external_path.split("/")[-1] if external_path else title

                    if location.strip().lower().endswith("locations"):
                        pending_multi.append((job_id, title, external_path))
                        continue
                    if is_us_location(location, company):
                        jobs[job_id] = {"title": title, "location": location,
                                        "url": ep["job_base"] + external_path}

                offset += LIMIT
                pages += 1
                if isinstance(total, int) and total and pages % 10 == 0:
                    pct = min(100, round(100 * offset / total))
                    print(f"[{company}] {pct}% | {min(offset, total)}/{total} "
                          f"| {len(jobs) + len(pending_multi)} matches so far")
                if isinstance(total, int) and offset >= total:
                    break
                if pages >= MAX_PAGES:
                    print(f"[{company}] hit MAX_PAGES ({MAX_PAGES}) at "
                          f"{offset}/{total} -- RESULTS INCOMPLETE.")
                    break
                time.sleep(PAGE_DELAY)

            if pending_multi:
                print(f"[{company}] resolving {len(pending_multi)} "
                      f"multi-location posting(s)...")
                for job_id, title, external_path in pending_multi:
                    resolved = _resolve_locations(context, ep, external_path)
                    if not resolved:
                        print(f"[{company}] could not resolve: {title}")
                        continue
                    if any(is_us_location(part, company)
                           for part in resolved.split(" | ")):
                        jobs[job_id] = {"title": title, "location": resolved,
                                        "url": ep["job_base"] + external_path}
                    time.sleep(1)

        except Exception as e:
            print(f"[{company}] error scraping Workday API: {e}")
            if not jobs:
                hard_failure = True
        finally:
            browser.close()

    if hard_failure and not jobs:
        return None
    print(f"[{company}] {len(jobs)} US internships.")
    return jobs


def run_workday_monitor(company, tenant, site, wd="wd5"):
    """Standard entry point: scrape, then save unless the scrape failed."""
    import database
    print(f"Starting {company} USA Internship Check...")
    jobs = scrape_workday(company, tenant, site, wd)
    if jobs is None:
        print(f"[{company}] scrape failed; skipping save to protect stored rows.")
        return
    new_count, deleted_count = database.save_jobs(company, jobs)
    if new_count > 0:
        print(f"[{company}] Added {new_count} new internships!")
    else:
        print(f"[{company}] No new internships.")
    if deleted_count > 0:
        print(f"[{company}] Removed {deleted_count} dead internships.")