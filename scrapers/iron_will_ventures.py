import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Iron Will Ventures"

# Iron Will is on BAMBOOHR. Its own site (iron-will.us) answers 403 to any
# automated client, so the board was found via a job aggregator's apply link:
# ironwillventures.bamboohr.com. Every BambooHR careers site exposes the whole
# board as JSON at /careers/list -> {"result": [{id, jobOpeningName,
# location:{city,state}, atsLocation:{country,...}}]}.
#
# A small firm's board can legitimately be empty or have no interns (their
# Summer Intern posting runs in spring), so an empty list returns {} -- only a
# transport/shape failure returns None.
#
# This file doubles as the BambooHR reference: another BambooHR employer needs
# only a new SUBDOMAIN.

SUBDOMAIN = "ironwillventures"
LIST_URL = f"https://{SUBDOMAIN}.bamboohr.com/careers/list"
JOB_URL = f"https://{SUBDOMAIN}.bamboohr.com/careers/{{id}}"
US_ONLY_EMPLOYER = True   # defense contractor: a blank location is still US
TIMEOUT = 30
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _fetch():
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(LIST_URL, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get("result"), list):
                    return data["result"]
                last = "no result list"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] careers/list failed after {RETRIES} tries: {last}")
    return None


def _location(job):
    loc = job.get("location") or {}
    ats = job.get("atsLocation") or {}
    city = loc.get("city") or ats.get("city")
    state = loc.get("state") or ats.get("state") or ats.get("province")
    country = ats.get("country")
    return ", ".join(x for x in (city, state, country) if x)


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    postings = _fetch()
    if postings is None:
        return None

    jobs = {}
    for job in postings:
        jid = str(job.get("id") or "")
        title = (job.get("jobOpeningName") or "").strip()
        if not jid or not title or not is_internship_title(title):
            continue
        location = _location(job)
        if not location and US_ONLY_EMPLOYER:
            location = "United States"
        if is_us_location(location, COMPANY_NAME):
            jobs[jid] = {"title": title, "location": location,
                         "url": JOB_URL.format(id=jid)}

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
