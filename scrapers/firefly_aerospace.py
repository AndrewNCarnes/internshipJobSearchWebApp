import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Firefly Aerospace"

# Firefly is on CLEARCOMPANY. fireflyspace.com/careers renders its list from a
# public JSON API keyed by a company GUID (found by probe_board.py). One GET
# with a large pageSize returns the whole board; each position carries a
# structured `locations` list with an ISO country, so no string guessing.
#
# This file doubles as the ClearCompany reference: another ClearCompany
# employer needs only a new COMPANY_GUID (from probe_board) and job host.

COMPANY_GUID = "00ed92c3-5bfb-7bfb-456d-4d9d77fef9a5"
API = f"https://careers-api.clearcompany.com/v1/{COMPANY_GUID}"
JOB_URL = "https://firefly.clearcompany.com/careers/jobs/{id}"
PAGE_SIZE = 500
TIMEOUT = 30
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _get_page(index):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API, headers=HEADERS, timeout=TIMEOUT, params={
                "pageIndex": index, "pageSize": PAGE_SIZE, "source": "CJB-0"})
            if r.status_code == 200:
                data = r.json()
                if isinstance(data.get("results"), list):
                    return data
                last = "no results list"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] page {index} failed after {RETRIES} tries: {last}")
    return None


def _us_locations(pos):
    out = []
    for loc in pos.get("locations") or []:
        name = ", ".join(x for x in (loc.get("city"), loc.get("subdivision")) if x)
        country = (loc.get("country") or "").upper()
        if country == "US" or (not country and is_us_location(name, COMPANY_NAME)):
            out.append(name or ("Remote, US" if loc.get("isRemote") else "United States"))
    if not out and not pos.get("locations"):
        text = pos.get("location") or ""
        if is_us_location(text, COMPANY_NAME):
            out.append(text)
    return out


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    positions, index, total = [], 0, None
    while True:
        data = _get_page(index)
        if data is None:
            return None
        if total is None:
            total = data.get("totalCount")
        positions.extend(data["results"])
        if not data["results"] or (isinstance(total, int) and len(positions) >= total):
            break
        index += 1
        if index > 20:
            print(f"[{COMPANY_NAME}] too many pages; results incomplete.")
            return None

    if isinstance(total, int) and len(positions) < total:
        print(f"[{COMPANY_NAME}] only got {len(positions)}/{total}; treating as failure.")
        return None

    jobs = {}
    for pos in positions:
        pid = pos.get("id")
        title = (pos.get("positionTitle") or "").strip()
        if not pid or not title or not is_internship_title(title):
            continue
        us = _us_locations(pos)
        if us:
            jobs[pid] = {"title": title, "location": " | ".join(us),
                         "url": JOB_URL.format(id=pid)}

    print(f"[{COMPANY_NAME}] scanned {len(positions)} listings; "
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
