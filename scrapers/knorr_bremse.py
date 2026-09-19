import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

COMPANY_NAME = "Knorr-Bremse"

# Requested as "New York Air Brake". NYAB posts on its parent's board,
# careers.knorr-bremse.com, alongside the other US units (Bendix, KB Signaling,
# Knorr Brake Company, Zelisko). We keep every US internship and put the brand
# in the title so "(New York Air Brake)" is visible on the dashboard. To keep
# NYAB only, set BRANDS = {"New York Air Brake"}.
BRANDS = None

# The board is SuccessFactors-backed, but the listing comes from an Azure
# Cognitive Search proxy (production.api.recruiting-solutions.org/search). The
# x-api-key is the site's PUBLISHABLE key ("pk_..."), embedded in the public
# page for every visitor's browser -- not a secret. If it rotates, the scraper
# fails loudly (None) and the new key is in the page's network calls.
#
# Facet queries 500 through this proxy, so we page the whole English index
# (~300 postings, ~46 per call) and filter locally. entryLevel "Student
# Opportunities" is the employer's own intern tag.

API = "https://production.api.recruiting-solutions.org/search"
API_KEY = ("pk_kb-prod_CXFXRxFHPLFLmfirIhpeWkwBaOhvmPWThobZrRZOpiDSncwTPTnDcpLhEvQs"
           "LWotRlKQIBXbweiOOOnaokaEsOznXIFFZWcU")
STUDENT_LEVELS = {"Student Opportunities", "High School Internships"}
MAX_REQUESTS = 60
DELAY = 0.5
TIMEOUT = 30
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Content-Type": "application/json;charset=UTF-8",
    "Referer": "https://careers.knorr-bremse.com/",
    "x-api-key": API_KEY,
    "customerid": "kb-prod",
    "internal": "false",
    "privatejobboard": "false",
}


def _post(skip):
    body = {"count": True, "facets": [], "filter": "language eq 'en_US'",
            "search": "*", "skip": skip}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(API, headers=HEADERS, json=body, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get("value"), list):
                    return data
                last = "no value list"
            else:
                last = f"HTTP {r.status_code}: {r.text[:100]}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] search at skip={skip} failed after {RETRIES} tries: {last}")
    return None


def _location(job):
    names = []
    for a in job.get("addresses") or []:
        city = (a.get("city") or "").strip()           # "Bowling Green, KY"
        country = (a.get("country") or "").strip()
        names.append(", ".join(x for x in (city, country) if x))
    if not names:
        names.append(", ".join(x for x in (job.get("state"), job.get("country")) if x))
    return " | ".join(n for n in names if n)


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    postings, total, skip = [], None, 0
    for _ in range(MAX_REQUESTS):
        data = _post(skip)
        if data is None:
            return None
        if total is None:
            total = data.get("@odata.count")
        batch = data["value"]
        if not batch:
            break
        postings.extend(batch)
        skip += len(batch)
        if isinstance(total, int) and skip >= total:
            break
        time.sleep(DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_REQUESTS; results incomplete.")
        return None

    if not postings or (isinstance(total, int) and len(postings) < total):
        print(f"[{COMPANY_NAME}] got {len(postings)}/{total}; treating as failure.")
        return None

    jobs = {}
    for job in postings:
        jid = job.get("jobId")
        title = (job.get("title") or "").strip()
        brand = (job.get("brand") or "").strip()
        if not jid or not title:
            continue
        if BRANDS and brand not in BRANDS:
            continue
        tagged = job.get("entryLevel") in STUDENT_LEVELS
        if not (is_internship_title(title) or (tagged and is_plausible_internship(title))):
            continue
        location = _location(job)
        if job.get("country") == "United States" or is_us_location(location, COMPANY_NAME):
            jobs[jid] = {
                "title": f"{title} ({brand})" if brand else title,
                "location": location or "United States",
                "url": job.get("link") or "https://careers.knorr-bremse.com/",
            }

    print(f"[{COMPANY_NAME}] scanned {len(postings)} listings; "
          f"{len(jobs)} are US internships.")
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
