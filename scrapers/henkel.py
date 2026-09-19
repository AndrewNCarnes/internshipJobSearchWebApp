import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Henkel"

# henkel.com/careers/find-your-job-apply is Henkel's own CMS; the list comes
# from an ajax "collection" endpoint returning JSON {resultsTotal,
# resultsRemaining, results:[{id,title,location,link}]}. It caps every call at
# 20 results whatever loadCount asks for, so the ~1,000-posting global board is
# ~50 requests. Location reads "Country, City, ST, Business Unit", country
# first, so the shared filter vetoes foreign rows by name.

BASE = "https://www.henkel.com"
API = f"{BASE}/ajax/collection/en/2083458-2083458/queryresults/asJson"
BATCH = 20               # server maximum
MAX_REQUESTS = 150
DELAY = 0.5
TIMEOUT = 30
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


def _get(start):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API, headers=HEADERS, timeout=TIMEOUT,
                             params={"startIndex": start, "loadCount": BATCH})
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
    print(f"[{COMPANY_NAME}] batch at {start} failed after {RETRIES} tries: {last}")
    return None


def _clean_location(loc):
    """'United States, Madison Heights, MI, Adhesive Technologies'
    -> 'Madison Heights, MI, United States' (drop the business unit)."""
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if len(parts) >= 3:
        return ", ".join(parts[1:3] + parts[:1])
    return loc


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    postings, seen, total, start = [], set(), None, 0
    for _ in range(MAX_REQUESTS):
        data = _get(start)
        if data is None:
            return None
        if total is None:
            total = data.get("resultsTotal")
        batch = [p for p in data["results"] if p.get("id") not in seen]
        if not batch:
            break
        for p in batch:
            seen.add(p.get("id"))
        postings.extend(batch)
        start += len(data["results"])
        if not data.get("resultsRemaining"):
            break
        time.sleep(DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_REQUESTS; results incomplete.")
        return None

    if isinstance(total, int) and len(postings) < total * 0.95:
        print(f"[{COMPANY_NAME}] only got {len(postings)}/{total}; treating as failure.")
        return None

    jobs = {}
    for p in postings:
        jid, title = p.get("id"), (p.get("title") or "").strip()
        location = p.get("location") or ""
        if not jid or not title or not is_internship_title(title):
            continue
        if is_us_location(location, COMPANY_NAME):
            link = p.get("link") or ""
            jobs[jid] = {"title": title, "location": _clean_location(location),
                         "url": link if link.startswith("http") else BASE + link}

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
