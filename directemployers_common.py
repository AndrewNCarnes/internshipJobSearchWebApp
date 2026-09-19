"""
directemployers_common.py -- shared logic for DirectEmployers / NLX boards
(careers.textron.com, enfra.dejobs.org, and any other *.dejobs.org site).

These sites are client-rendered; the data comes from the jobsyn Solr search
API, which needs only an `x-origin` header naming the site. A browser-style
Origin header gets 403 "Mismatched origin". Page size is capped at 10.

Usage from a per-company scraper:

    from directemployers_common import scrape_directemployers
    jobs = scrape_directemployers(COMPANY_NAME, "careers.textron.com",
                                  job_type="internship-co-op")

job_type: the site's own internship facet slug when it has one (Textron's
"Internship / Co-Op" -> "internship-co-op"); pages only that slice. Without
it, the whole board is paged and filtered by title.

Returns {guid: {...}} on success, or None on any failure -- a partial list
would let save_jobs prune real postings.
"""
import time

import requests

from location_filter import is_us_location, is_internship_title

API = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
PAGE_SIZE = 10          # the API ignores num_items above 10
MAX_PAGES = 150
PAGE_DELAY = 0.5
TIMEOUT = 30
RETRIES = 3
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _get_page(company, site, page, job_type):
    params = {"page": page, "num_items": PAGE_SIZE}
    if job_type:
        params["job_type"] = job_type
    headers = {"User-Agent": UA, "Accept": "application/json", "x-origin": site}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API, headers=headers, params=params, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get("jobs"), list):
                    return data
                last = "no jobs list"
            else:
                last = f"HTTP {r.status_code}: {r.text[:120]}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{company}] page {page} failed after {RETRIES} tries: {last}")
    return None


def scrape_directemployers(company, site, job_type=None):
    jobs = {}
    scanned = 0
    total = None

    for page in range(1, MAX_PAGES + 1):
        data = _get_page(company, site, page, job_type)
        if data is None:
            return None

        pagination = data.get("pagination") or {}
        if total is None:
            total = pagination.get("total")
            scope = f"'{job_type}'" if job_type else "all"
            print(f"[{company}] board reports {total} postings ({scope}).")

        for job in data["jobs"]:
            scanned += 1
            guid = job.get("guid") or job.get("id")
            title = (job.get("title_exact") or "").strip()
            location = (job.get("location_exact") or "").strip()
            country = job.get("country_exact") or job.get("country_short_exact") or ""
            if not guid or not title:
                continue
            # A facet is the employer's tag -- still require an intern title
            # so "Apprentice" style tags don't slip in.
            if not is_internship_title(title):
                if job_type:
                    print(f"[{company}] tagged intern but title isn't: {title[:60]}")
                continue
            # Structured country appended so bare cities resolve and a
            # Canadian "CA" can't read as California.
            full = f"{location}, {country}" if country and country not in location else location
            if is_us_location(full, company):
                jobs[guid] = {"title": title, "location": location or full,
                              "url": f"https://{site}/{guid}/job/"}

        if not pagination.get("has_more_pages"):
            break
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{company}] hit MAX_PAGES ({MAX_PAGES}); results incomplete.")
        return None

    if isinstance(total, int) and scanned < total:
        print(f"[{company}] only scanned {scanned}/{total}; treating as failure.")
        return None

    print(f"[{company}] scanned {scanned} listings; {len(jobs)} are US internships.")
    return jobs


def run_directemployers_monitor(company, site, job_type=None):
    """Standard entry point: scrape, then save unless the scrape failed."""
    import database
    print(f"Starting {company} USA Internship Check...")
    jobs = scrape_directemployers(company, site, job_type)
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
