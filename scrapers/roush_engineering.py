import sys
import os
import re
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Roush"

# Roush runs on Phenom People. The search-results page is client-side rendered --
# fetching it with requests returns raw, unsubstituted template placeholders like
#   "No results for ${pageStateData.searchKeyword}"
# so HTML scraping is worthless here. Behind it, every Phenom career site exposes
# a public, no-auth JSON endpoint that the page itself calls:
#
#   POST https://<career-domain>/widgets      (ddoKey=refineSearch)
#
# JSON in, JSON out, paginated with from/size, no CSRF and no cookies. Same
# quality of source as Greenhouse's API.
#
# The one site-specific value it needs is refNum. Roush's is visible in the page
# metadata -- its CDN assets live under CareerConnectResources/ROUROUUS -- so the
# refNum is ROUROUUS. We still scrape it from the live page at runtime and only
# fall back to the constant, so that a site reconfiguration surfaces as a warning
# rather than silently querying the wrong board.
#
# Phenom returns roughly 10-20 jobs per page and reports totalHits, so we page
# until we have them all and cross-check the count.

BASE = "https://jobs.roush.com"
SEARCH_PAGE = f"{BASE}/us/en/search-results"
WIDGETS_URL = f"{BASE}/widgets"
FALLBACK_REFNUM = "ROUROUUS"

PAGE_SIZE = 20
MAX_PAGES = 60          # 60 * 20 = 1200 postings, far above Roush's board size
PAGE_DELAY = 0.8

TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}


def _discover_refnum():
    """Pull refNum out of the live career page. Phenom embeds it in inline JS
    config and in CDN asset paths (CareerConnectResources/<REFNUM>/...).
    Returns the fallback if the page can't be read or nothing matches."""
    try:
        r = requests.get(SEARCH_PAGE, headers={"User-Agent": HEADERS["User-Agent"]},
                         timeout=TIMEOUT)
        if r.status_code == 200 and r.text:
            for pat in (r'"refNum"\s*:\s*"([A-Z0-9]+)"',
                        r"refNum\s*[:=]\s*['\"]([A-Z0-9]+)['\"]",
                        r"CareerConnectResources/(?:prod/)?([A-Z0-9]{4,})/"):
                m = re.search(pat, r.text)
                if m:
                    found = m.group(1)
                    if found != FALLBACK_REFNUM:
                        print(f"[{COMPANY_NAME}] refNum from page: {found} "
                              f"(constant is {FALLBACK_REFNUM}).")
                    return found
        print(f"[{COMPANY_NAME}] could not read refNum from page; "
              f"using {FALLBACK_REFNUM}.")
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
    """Unwrap {'refineSearch': {'data': {'jobs': [...]}, 'totalHits': N}}.
    Returns (jobs_list, total) or (None, None) when the shape is unrecognized --
    which is a FAILURE, not an empty board."""
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
    """Phenom usually supplies a prebuilt display string; otherwise assemble
    city/state/country. Country is spelled out here rather than a bare code, so
    the CA=California collision that bit the ADP and SPX boards doesn't apply."""
    combined = _first(job, "cityStateCountry", "cityState", "location",
                      "locationName", "displayLocation")
    if combined:
        return combined
    parts = []
    for key in ("city", "state", "country"):
        v = _first(job, key)
        if v:
            parts.append(v)
    if not parts:
        # Multi-location postings carry a list instead.
        locs = job.get("multi_location") or job.get("locations")
        if isinstance(locs, list) and locs:
            first = locs[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict):
                return _first(first, "cityStateCountry", "city", "location")
    return ", ".join(parts)


def _url_of(job):
    for key in ("applyUrl", "jobUrl", "url", "canonicalUrl"):
        v = job.get(key)
        if isinstance(v, str) and v.strip():
            u = v.strip()
            return u if u.startswith("http") else BASE + u
    seq = _first(job, "jobSeqNo", "jobId")
    return f"{BASE}/us/en/job/{seq}" if seq else SEARCH_PAGE


def get_current_jobs():
    refnum = _discover_refnum()

    jobs = {}
    seen = set()
    scanned = 0
    total = None

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        payload = _post(refnum, start, PAGE_SIZE)
        if payload is None:
            # Keep partial results if we already have some; otherwise this is a
            # hard failure and the save must be skipped, not treated as 0 jobs.
            return jobs if jobs else None

        batch, batch_total = _refine(payload)
        if batch is None:
            if page == 0:
                print(f"[{COMPANY_NAME}] unexpected response shape from "
                      f"widgets; refNum '{refnum}' may be wrong.")
                return None
            break
        if total is None and batch_total is not None:
            total = batch_total
            print(f"[{COMPANY_NAME}] board reports {total} total postings.")
        if not batch:
            break

        fresh = 0
        for job in batch:
            if not isinstance(job, dict):
                continue
            job_id = _first(job, "jobSeqNo", "jobId", "id")
            title = _first(job, "title", "jobTitle")
            if not job_id or not title:
                continue
            if job_id in seen:
                continue
            seen.add(job_id)
            fresh += 1
            scanned += 1

            if not is_internship_title(title):
                continue
            location = _location_of(job)
            if not location:
                print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
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
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}); results may be "
              f"incomplete.")

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