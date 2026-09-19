import sys
import os
import re
import json
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Beehive Industries"

# Beehive is on RIPPLING ATS. Note the domain: beehive-industries.com (with a
# hyphen). beehiveindustries.com belongs to an unrelated CivicPlus product --
# never guess a careers URL from the company name.
#
# The careers page embeds ats.rippling.com/embed/<slug>/jobs, a Next.js page
# whose __NEXT_DATA__ script carries each page of postings (20 per page) plus
# totalItems/totalPages. Each posting has structured locations with an ISO
# countryCode.
#
# This file doubles as the Rippling reference: another Rippling employer needs
# only a new BOARD_SLUG.

BOARD_SLUG = "beehive-industries"
PAGE_URL = f"https://ats.rippling.com/embed/{BOARD_SLUG}/jobs?page={{page}}"
MAX_PAGES = 50
PAGE_DELAY = 0.8
TIMEOUT = 30
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _find_board(obj):
    """The job-post query result: a dict with items + totalItems. Searched for
    rather than addressed by path, since dehydratedState ordering can change."""
    if isinstance(obj, dict):
        if isinstance(obj.get("items"), list) and "totalItems" in obj:
            return obj
        for v in obj.values():
            found = _find_board(v)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_board(v)
            if found is not None:
                return found
    return None


def _get_page(page):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(PAGE_URL.format(page=page), headers=HEADERS,
                             timeout=TIMEOUT)
            if r.status_code == 200:
                m = NEXT_DATA.search(r.text)
                board = _find_board(json.loads(m.group(1))) if m else None
                if board is not None:
                    return board
                last = "no job data in __NEXT_DATA__"
            else:
                last = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] page {page} failed after {RETRIES} tries: {last}")
    return None


def _us_locations(post):
    out = []
    for loc in post.get("locations") or []:
        name = loc.get("name") or ", ".join(
            x for x in (loc.get("city"), loc.get("stateCode")) if x)
        code = (loc.get("countryCode") or "").upper()
        if code == "US" or (not code and is_us_location(name, COMPANY_NAME)):
            out.append(name or "United States")
    return out


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    posts, total, pages = [], None, None
    for page in range(MAX_PAGES):
        board = _get_page(page)
        if board is None:
            return None
        if total is None:
            total, pages = board.get("totalItems"), board.get("totalPages")
        posts.extend(board["items"])
        if not board["items"] or (isinstance(pages, int) and page + 1 >= pages):
            break
        time.sleep(PAGE_DELAY)
    else:
        print(f"[{COMPANY_NAME}] hit MAX_PAGES; results incomplete.")
        return None

    if isinstance(total, int) and len(posts) < total:
        print(f"[{COMPANY_NAME}] only got {len(posts)}/{total}; treating as failure.")
        return None

    jobs = {}
    for post in posts:
        pid, title = post.get("id"), (post.get("name") or "").strip()
        if not pid or not title or not is_internship_title(title):
            continue
        us = _us_locations(post)
        if us:
            jobs[pid] = {"title": title, "location": " | ".join(us),
                         "url": post.get("url") or
                         f"https://ats.rippling.com/{BOARD_SLUG}/jobs/{pid}"}

    print(f"[{COMPANY_NAME}] scanned {len(posts)} listings; "
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
