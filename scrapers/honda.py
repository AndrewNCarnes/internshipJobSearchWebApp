import sys
import os
import re
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Honda"

# Honda is on PHENOM PEOPLE (careers.honda.com), same shape as Eli Lilly and
# Roush: POST /widgets with ddoKey=refineSearch. refNum is AHMAHMUS (American
# Honda Motor), discovered from the search page and hard-coded as a fallback.
#
# This replaces a Playwright version that drove the page and intercepted the
# same /widgets responses. Two things were wrong with it:
#   1. It carried its OWN is_us_location with a substring whitelist -- the
#      exact filter that dropped Milwaukee ("uk") and Indianapolis ("india"),
#      and an 11-state list that dropped everything else. It also used the old
#      r'\bintern' with no closing boundary, so "Internal Audit" counted.
#   2. On any exception it returned the jobs collected so far, so a failed
#      page load looked like "Honda has no internships" and save_jobs pruned
#      every stored row. Honda's stored count was 0 when this was rewritten.
# Now: shared filters, one plain request per page, None on failure.

BASE = "https://careers.honda.com"
SEARCH_PAGE = f"{BASE}/us/en/search-results"
WIDGETS_URL = f"{BASE}/widgets"
FALLBACK_REFNUM = "AHMAHMUS"

PAGE_SIZE = 100          # the widget accepts 100; whole board is ~250 postings
MAX_PAGES = 30
PAGE_DELAY = 0.8
TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}


def _discover_refnum():
    """Read refNum from the live page; fall back to the constant."""
    try:
        r = requests.get(SEARCH_PAGE, headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=TIMEOUT)
        if r.status_code == 200 and r.text:
            for pat in (r'"refNum"\s*:\s*"([A-Z0-9]+)"',
                        r"CareerConnectResources/(?:prod/)?([A-Z0-9]{4,})/"):
                m = re.search(pat, r.text)
                if m:
                    found = m.group(1)
                    if found != FALLBACK_REFNUM:
                        print(f"[{COMPANY_NAME}] refNum from page: {found} "
                              f"(constant is {FALLBACK_REFNUM}).")
                    return found
        print(f"[{COMPANY_NAME}] could not read refNum; using {FALLBACK_REFNUM}.")
    except requests.RequestException as e:
        print(f"[{COMPANY_NAME}] refNum lookup failed ({e}); "
              f"using {FALLBACK_REFNUM}.")
    return FALLBACK_REFNUM


def _payload(refnum, start, size):
    return {
        "lang": "en_us",
        "deviceType": "desktop",
        "country": "us",
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "size": size,
        "from": start,
        "jobs": True,
        "counts": True,
        "all_fields": ["category", "country", "state", "city", "type"],
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page20",
        "siteType": "external",
        # No keyword: Phenom's keyword search is fuzzy, and the whole board is
        # only ~250 postings -- 3 requests -- so scan all of it.
        "keywords": "",
        "global": True,
        "selected_fields": {},
        "sort": {"order": "desc", "field": "postedDate"},
        "locationData": {},
        "refNum": refnum,
    }


def _post(refnum, start, size):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(WIDGETS_URL, json=_payload(refnum, start, size),
                              headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    last = f"non-JSON body ({e})"
            else:
                last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"[{COMPANY_NAME}] widgets request failed after {RETRIES} tries "
          f"(from={start}): {last}")
    return None


def _refine(payload):
    """Pull (jobs, totalHits) out of the refineSearch envelope."""
    if not isinstance(payload, dict):
        return None, None
    rs = payload.get("refineSearch")
    if not isinstance(rs, dict):
        return None, None
    data = rs.get("data")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if jobs is None:
        jobs = rs.get("jobs")
    if not isinstance(jobs, list):
        return None, None
    total = rs.get("totalHits")
    if not isinstance(total, int):
        total = (data or {}).get("totalHits") if isinstance(data, dict) else None
    return jobs, total


def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return ""


def _location_of(job):
    combined = _first(job, "cityStateCountry", "cityState", "location",
                      "locationName", "displayLocation")
    if combined:
        return combined
    parts = [_first(job, k) for k in ("city", "state", "country")]
    return ", ".join(p for p in parts if p)


def _url_of(job):
    for key in ("applyUrl", "jobUrl", "url", "canonicalUrl"):
        v = job.get(key)
        if isinstance(v, str) and v.strip():
            u = v.strip()
            return u if u.startswith("http") else BASE + u
    job_id = _first(job, "jobId", "jobSeqNo")
    title = _first(job, "title")
    if job_id:
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
        return f"{BASE}/us/en/job/{job_id}/{slug}"
    return SEARCH_PAGE


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    refnum = _discover_refnum()
    jobs = {}
    seen = set()
    scanned = 0
    total = None

    for page in range(MAX_PAGES):
        payload = _post(refnum, page * PAGE_SIZE, PAGE_SIZE)
        if payload is None:
            # A partial dict would look like the complete board and prune the
            # rest. Any failure is a failure.
            return None

        batch, batch_total = _refine(payload)
        if batch is None:
            if page == 0:
                print(f"[{COMPANY_NAME}] unexpected response shape; "
                      f"refNum '{refnum}' may be wrong.")
                return None
            break
        if total is None and batch_total is not None:
            total = batch_total
            print(f"[{COMPANY_NAME}] board reports {total} postings.")
        if not batch:
            break

        fresh = 0
        for job in batch:
            if not isinstance(job, dict):
                continue
            job_id = _first(job, "jobId", "jobSeqNo", "id")
            title = _first(job, "title", "jobTitle")
            if not job_id or not title or job_id in seen:
                continue
            seen.add(job_id)
            fresh += 1
            scanned += 1

            if not is_internship_title(title):
                continue
            location = _location_of(job)
            if not location:
                print(f"[{COMPANY_NAME}] no location for: {title[:50]}")
                continue
            if is_us_location(location, COMPANY_NAME):
                jobs[job_id] = {"title": title, "location": location,
                                "url": _url_of(job)}

        if fresh == 0:
            break
        if total is not None and scanned >= total:
            break
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}); "
              f"results may be incomplete.")
        return None

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

    new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
    if new_count > 0:
        print(f"[{COMPANY_NAME}] Added {new_count} new internships!")
    else:
        print(f"[{COMPANY_NAME}] No new internships.")
    if deleted_count > 0:
        print(f"[{COMPANY_NAME}] Removed {deleted_count} dead internships.")


if __name__ == "__main__":
    run_monitor()
