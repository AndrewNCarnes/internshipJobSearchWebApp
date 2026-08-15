import sys
import os
import time
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Vast Space"
BOARD_TOKEN = "vast"
API_URL = f"https://boards-api.greenhouse.io/v1/boards/{BOARD_TOKEN}/jobs"
TIMEOUT = 30      # without this, a stalled connection hangs the whole batch
RETRIES = 2

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def get_locations(job):
    """
    Greenhouse puts the headline location in location.name, but a posting
    attached to several sites also carries an offices array. Vast is mostly
    Long Beach with some Mojave test-site work, so multi-site postings are
    less common here than at Rocket Lab -- but reading only the headline is
    what loses them when they do appear.
    """
    parts = []
    name = (job.get("location") or {}).get("name") or ""
    if name:
        parts.append(name)

    for office in job.get("offices") or []:
        if isinstance(office, dict):
            for key in ("location", "name"):
                v = office.get(key)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())

    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def fetch():
    """GET the board, retrying briefly on transient network errors."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if attempt < RETRIES:
                print(f"[{COMPANY_NAME}] attempt {attempt} failed ({e}); retrying...")
                time.sleep(3)
    print(f"Error fetching {COMPANY_NAME}: {last}")
    return None


def get_current_jobs():
    data = fetch()
    if data is None:
        return None          # None (not {}) so run_monitor skips the save

    jobs = {}
    scanned = 0

    for job in data.get("jobs", []):
        scanned += 1
        title = job.get("title", "")
        if not is_internship_title(title):
            continue

        locations = get_locations(job)
        if not locations:
            print(f"[{COMPANY_NAME}] no location on posting: {title[:50]}")
            continue
        if not any(is_us_location(loc, COMPANY_NAME) for loc in locations):
            continue

        jobs[str(job.get("id"))] = {
            "title": title,
            "location": " | ".join(locations),
            "url": job.get("absolute_url", ""),
        }

    print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()

    if current_jobs is not None:
        new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
        if new_count > 0: print(f"[{COMPANY_NAME}] Added {new_count} new internships!")
        else: print(f"[{COMPANY_NAME}] No new internships.")
        if deleted_count > 0: print(f"[{COMPANY_NAME}] Removed {deleted_count} dead internships.")


if __name__ == "__main__":
    run_monitor()