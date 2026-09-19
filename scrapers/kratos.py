import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

COMPANY_NAME = "Kratos Defense"

# Kratos is on PERELESS SYSTEMS (submit4jobs.com), not iCIMS as first guessed.
# kratosdefense.com/careers/open-roles embeds an AngularJS app from
# apps3.pereless.com; its getJobs endpoint returns the WHOLE board (~300
# postings) in one POST. It needs the `cid` header and the full filter object
# the app sends -- an empty {"filters": {}} gets Pereless's HTML error page.
#
# Canada and UK are separate Pereless boards (open-roles/canada, /uk), so this
# one is US-only; the location filter still runs as a guard.

CID = "85347"
API = "https://apps3.pereless.com/templates/magnetolive/api/?action=getJobs"
JOB_URL = "https://www.kratosdefense.com/careers/open-roles/#/jobDescription/{jid}/job"
FILTERS = {
    "buid": "", "intranet": "0", "city": "", "mystate": [], "country": "",
    "title": "", "zipcode": "", "department": "", "businessname": "",
    "language": "en", "jobtype": "", "keyword": "", "jobcapability": [],
    "jobcategory": [],
}
TIMEOUT = 60
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Content-Type": "application/json;charset=UTF-8",
    "cid": CID,
}


def _fetch_all():
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(API, headers=HEADERS, json={"filters": FILTERS},
                              timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()           # error page is HTML -> ValueError
                if isinstance(data, list):
                    return data
                last = f"unexpected JSON shape: {type(data).__name__}"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] getJobs failed after {RETRIES} tries: {last}")
    return None


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    postings = _fetch_all()
    if postings is None:
        return None
    if not postings:
        # A 300-posting board never goes to zero overnight; an empty list
        # means the API changed, not that Kratos stopped hiring.
        print(f"[{COMPANY_NAME}] API returned an empty board; treating as failure.")
        return None

    jobs = {}
    for job in postings:
        jid = job.get("jid")
        title = (job.get("job_title") or "").strip()
        if not jid or not title:
            continue
        tagged = "intern" in str(job.get("jobtype", "")).lower()
        if not (is_internship_title(title) or (tagged and is_plausible_internship(title))):
            continue
        parts = [job.get("city"), job.get("state"), job.get("country")]
        location = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
        if is_us_location(location, COMPANY_NAME):
            jobs[str(jid)] = {"title": title, "location": location,
                              "url": JOB_URL.format(jid=jid)}

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
