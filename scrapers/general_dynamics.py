import sys
import os
import gzip
import json
import time
import base64

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

COMPANY_NAME = "General Dynamics"

# gd.com/careers/job-search is ONE search across every GD business -- Electric
# Boat, Gulfstream, GD Mission Systems, Land Systems, OTS, NASSCO, Bath Iron
# Works, Jet Aviation -- so one scraper covers the whole family instead of one
# per subsidiary board.
#
# API quirk: the query is JSON -> gzip -> base64, passed as ?request=. It
# returns 200 with a non-JSON body unless the request carries browser-style
# Accept/Referer headers. Each result carries EmploymentTypes (e.g. "Intern")
# and structured Locations with an ISO Country.
#
# We page the whole board (~3,100 postings, 16 pages of 200) rather than use
# the facet filter, whose request syntax isn't documented anywhere.

API = "https://www.gd.com/API/Careers/CareerSearch"
BASE = "https://www.gd.com"
PAGE_SIZE = 200          # largest size the site offers
MAX_PAGES = 60
PAGE_DELAY = 0.8
TIMEOUT = 60
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.gd.com/careers/job-search",
}


def _encode(req):
    raw = json.dumps(req, separators=(",", ":")).encode()
    return base64.b64encode(gzip.compress(raw)).decode()


def _get_page(page):
    req = {"address": [], "facets": [], "page": page, "pageSize": PAGE_SIZE}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API, params={"request": _encode(req)},
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get("Results"), list):
                    return data
                last = "no Results list"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] page {page} failed after {RETRIES} tries: {last}")
    return None


def _us_locations(job):
    """US location names for a posting, using the structured Country field and
    falling back to the shared text filter when Country is missing."""
    names = []
    for loc in job.get("Locations") or []:
        name = (loc.get("Name") or "").strip()
        country = (loc.get("Country") or "").upper()
        if country == "US" or (not country and is_us_location(name, COMPANY_NAME)):
            names.append(name or "United States")
    return names


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    jobs = {}
    seen = set()
    total = None
    interns_seen = 0

    for page in range(MAX_PAGES):
        data = _get_page(page)
        if data is None:
            return None          # partial list would prune real rows
        if total is None:
            total = data.get("ResultTotal")
            print(f"[{COMPANY_NAME}] board reports {total} postings.")
        results = data["Results"]
        if not results:
            break

        for job in results:
            jid = str(job.get("Id") or "")
            title = (job.get("Title") or "").strip()
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)

            tagged = any("intern" in str(t).lower() or "co-op" in str(t).lower()
                         for t in job.get("EmploymentTypes") or [])
            if tagged:
                interns_seen += 1
            if not (is_internship_title(title) or (tagged and is_plausible_internship(title))):
                continue

            us = _us_locations(job)
            if not us:
                continue
            url = (job.get("Link") or {}).get("Url") or ""
            if url and not url.startswith("http"):
                url = BASE + url
            company = (job.get("Company") or "").strip()
            jobs[jid] = {
                # keep the business unit visible -- "Electric Boat" vs
                # "Gulfstream" matters more than "General Dynamics"
                "title": f"{title} ({company})" if company else title,
                "location": " | ".join(us),
                "url": url or f"{BASE}/careers/job-search",
            }

        if isinstance(total, int) and len(seen) >= total:
            break
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}); results incomplete.")
        return None

    if isinstance(total, int) and len(seen) < total * 0.95:
        print(f"[{COMPANY_NAME}] only scanned {len(seen)}/{total}; treating as failure.")
        return None

    print(f"[{COMPANY_NAME}] scanned {len(seen)} listings ({interns_seen} tagged "
          f"Intern); {len(jobs)} are US internships.")
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
