import sys
import os
import re
import time

import requests
from bs4 import BeautifulSoup

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Siemens"
BASE = "https://jobs.siemens.com"

# Confirmed against the live page source:
#   card      <article class="article article--result 1" id="article--1">
#   title     <h3 class="article__header__text__title"><a href=".../JobDetail/516029">
#   location  <span class="list-item-location">  -- either nested city/state/
#             country spans ("Cairo, Al Qahirah, Egypt") or plain text
#             ("Multiple Locations")
#   job id    <span class="list-item-jobId">Job ID: 516029</span>
#
# This board is SERVER-RENDERED: plain requests get the same HTML a browser
# does. It used to be driven through Playwright, which cost ~7s per page --
# 12+ minutes for a board of 659 results, and it still hit MAX_PAGES and
# returned a PARTIAL list, which save_jobs would treat as complete and use to
# prune every posting past page 100. Now: requests + BeautifulSoup (~2.5s per
# page), the total is read from the page, and an incomplete walk returns None.
SEARCH_PATH = "/en_US/externaljobs/SearchJobs/intern/"

# Siemens' own Country filter, taken from a real filtered search URL:
#   42386=[812209]  ->  Country = United States of America
#   42386_format=17546 accompanies it
# This is server-side filtering and it cuts the result set from "999+" to ~660,
# so the scraper pages a third as far and never sees Cairo or Sao Paulo.
COUNTRY_FILTER = "42386=%5B812209%5D&42386_format=17546"
PAGE_SIZE = 6            # the board ignores folderRecordsPerPage; 6 is forced
MAX_PAGES = 400          # fuse only: 400 * 6 = 2400 postings
PAGE_DELAY = 0.4
TIMEOUT = 40
RETRIES = 3

# Siemens lists "SkillBridge Internship - Field Service Engineer" style roles.
# SkillBridge is a Department of Defense transition program for separating
# service members, not a student internship. Set to False to keep them.
SKIP_SKILLBRIDGE = True

CARD_SEL = ".article.article--result"
TITLE_SEL = "h3.article__header__text__title a"
LOCATION_SELS = [".list-item-location", ".article__header__text__subtitle span"]
JOBID_SEL = ".list-item-jobId"

# Fallbacks if Siemens reskins the board.
FALLBACK_CARDS = ["article.article--result", "[class*='article--result']",
                  "article[id^='article--']"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}
RESULT_COUNT = re.compile(r"([\d,]+)\s*\+?\s*results", re.I)


def page_url(offset):
    return (f"{BASE}{SEARCH_PATH}?{COUNTRY_FILTER}&listFilterMode=1"
            f"&folderRecordsPerPage={PAGE_SIZE}&folderOffset={offset}")


def _get(offset):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(page_url(offset), headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.text:
                return BeautifulSoup(r.text, "html.parser")
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] offset {offset} failed after {RETRIES} tries: {last}")
    return None


def find_cards(soup):
    for sel in [CARD_SEL] + FALLBACK_CARDS:
        found = soup.select(sel)
        if found:
            return found, sel
    return [], None


def parse_card(card):
    link = card.select_one(TITLE_SEL) or card.find("a")
    if not link:
        return None
    title = link.get_text(" ", strip=True)
    href = link.get("href") or ""
    if not title or not href:
        return None
    url = href if href.startswith("http") else BASE + href

    location = ""
    for sel in LOCATION_SELS:
        el = card.select_one(sel)
        if el:
            # the location is nested city/state/country spans, so joining with
            # a space gives "Raleigh , North Carolina , United States"
            location = " ".join(el.get_text(" ", strip=True).split())
            location = re.sub(r"\s+([,.])", r"\1", location).strip(" ,")
            if location:
                break

    idel = card.select_one(JOBID_SEL)
    job_id = (idel.get_text(" ", strip=True).replace("Job ID:", "").strip()
              if idel else "")
    if not job_id:
        job_id = url.rstrip("/").split("/")[-1]

    return job_id, title, location, url


def total_results(soup):
    m = RESULT_COUNT.search(soup.get_text(" ", strip=True))
    return int(m.group(1).replace(",", "")) if m else None


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    jobs = {}
    seen = set()
    total = None

    for page_num in range(MAX_PAGES):
        soup = _get(page_num * PAGE_SIZE)
        if soup is None:
            return None

        cards, used = find_cards(soup)
        if page_num == 0:
            if not cards:
                print(f"[{COMPANY_NAME}] no job cards found -- the board layout "
                      f"changed. Check {page_url(0)} by hand.")
                return None
            total = total_results(soup)
            print(f"[{COMPANY_NAME}] board reports {total} results "
                  f"(via '{used}').")
        if not cards:
            break

        fresh = 0
        for card in cards:
            parsed = parse_card(card)
            if not parsed:
                continue
            job_id, title, location, url = parsed
            if job_id in seen:
                continue
            seen.add(job_id)
            fresh += 1

            if not is_internship_title(title):
                continue
            if SKIP_SKILLBRIDGE and "skillbridge" in title.lower():
                continue
            if not location:
                print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
                continue
            if is_us_location(location, COMPANY_NAME):
                jobs[job_id] = {"title": title, "location": location, "url": url}

        if fresh == 0:                  # same cards re-served: end of the list
            break
        if isinstance(total, int) and len(seen) >= total:
            break
        if page_num and page_num % 25 == 0:
            print(f"[{COMPANY_NAME}] {len(seen)} listings scanned | "
                  f"{len(jobs)} US internships so far")
        time.sleep(PAGE_DELAY)
    else:
        # Ran out of pages before running out of results: the list is
        # incomplete, and saving it would prune everything past the fuse.
        print(f"[{COMPANY_NAME}] hit MAX_PAGES ({MAX_PAGES}) -- results "
              f"incomplete; returning None to protect stored rows.")
        return None

    if isinstance(total, int) and len(seen) < total * 0.9:
        print(f"[{COMPANY_NAME}] only scanned {len(seen)}/{total}; "
              f"treating as failure.")
        return None

    print(f"[{COMPANY_NAME}] scanned {len(seen)} listings; "
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
