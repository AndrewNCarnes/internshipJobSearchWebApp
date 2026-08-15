import sys
import os
import time
from playwright.sync_api import sync_playwright

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
# Pagination is offset-based: &folderRecordsPerPage=6&folderOffset=6, and a
# "Next >>" link carries class paginationNextLink. The default page size of 6
# would mean ~167 requests for the 999+ results a keyword=intern search
# returns, so the page size is raised and the real value read back from the
# response rather than assumed.
SEARCH_PATH = "/en_US/externaljobs/SearchJobs/intern/"

# Siemens' own Country filter, taken from a real filtered search URL:
#   42386=[812209]  ->  Country = United States of America
#   42386_format=17546 accompanies it
# This is server-side filtering and it cuts the result set from "999+" to 413,
# so the scraper pages a third as far and never sees Cairo or Sao Paulo.
COUNTRY_FILTER = "42386=%5B812209%5D&42386_format=17546"
PAGE_SIZE = 100
MAX_PAGES = 100
PAGE_DELAY = 1.5

# Siemens lists "SkillBridge Internship - Field Service Engineer" style roles.
# SkillBridge is a Department of Defense transition program for separating
# service members, not a student internship. Set to False to keep them.
SKIP_SKILLBRIDGE = True

CARD_SEL = ".article.article--result"
TITLE_SEL = "h3.article__header__text__title a"
LOCATION_SELS = [".list-item-location", ".article__header__text__subtitle span"]
JOBID_SEL = ".list-item-jobId"
NEXT_SEL = ".paginationNextLink"

# Fallbacks if Siemens reskins the board.
FALLBACK_CARDS = ["article.article--result", "[class*='article--result']",
                  "article[id^='article--']"]


def page_url(offset):
    return (f"{BASE}{SEARCH_PATH}?{COUNTRY_FILTER}&listFilterMode=1"
            f"&folderRecordsPerPage={PAGE_SIZE}&folderOffset={offset}")


def text_of(el):
    try:
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def parse_card(card):
    try:
        link = card.query_selector(TITLE_SEL) or card.query_selector("a")
    except Exception:
        return None
    if not link:
        return None

    title = text_of(link)
    href = link.get_attribute("href") or ""
    if not title or not href:
        return None
    url = href if href.startswith("http") else BASE + href

    location = ""
    for sel in LOCATION_SELS:
        try:
            el = card.query_selector(sel)
        except Exception:
            el = None
        if el:
            location = text_of(el)
            if location:
                break

    job_id = ""
    try:
        idel = card.query_selector(JOBID_SEL)
        if idel:
            job_id = text_of(idel).replace("Job ID:", "").strip()
    except Exception:
        pass
    if not job_id:
        job_id = url.rstrip("/").split("/")[-1]

    return job_id, title, location, url


def find_cards(page):
    for sel in [CARD_SEL] + FALLBACK_CARDS:
        try:
            found = page.query_selector_all(sel)
        except Exception:
            continue
        if found:
            return found, sel
    return [], None


def get_current_jobs():
    jobs = {}
    seen = set()
    scanned = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        ).new_page()

        try:
            print(f"[{COMPANY_NAME}] Loading careers page...")
            offset = 0
            step = PAGE_SIZE

            for page_num in range(1, MAX_PAGES + 1):
                page.goto(page_url(offset), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                cards, used = find_cards(page)
                if not cards:
                    if page_num == 1:
                        print(f"[{COMPANY_NAME}] ⚠️  no job cards found -- the board "
                              f"layout changed. Check {page_url(0)} by hand.")
                    break

                if page_num == 1:
                    # Siemens may cap folderRecordsPerPage; use what it actually
                    # returned as the paging step so offsets stay aligned.
                    step = len(cards)
                    print(f"[{COMPANY_NAME}] {len(cards)} results per page "
                          f"via '{used}'.")

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
                    scanned += 1

                    if not is_internship_title(title):
                        continue
                    if SKIP_SKILLBRIDGE and "skillbridge" in title.lower():
                        continue
                    if not location:
                        print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
                        continue
                    if is_us_location(location, COMPANY_NAME):
                        jobs[job_id] = {"title": title, "location": location, "url": url}

                if fresh == 0:
                    break

                try:
                    has_next = bool(page.query_selector(NEXT_SEL))
                except Exception:
                    has_next = False
                if not has_next:
                    break

                offset += step
                if page_num % 5 == 0:
                    print(f"[{COMPANY_NAME}] {scanned} listings scanned | "
                          f"{len(jobs)} US internships so far")
                time.sleep(PAGE_DELAY)
            else:
                print(f"[{COMPANY_NAME}] ⚠️  hit MAX_PAGES ({MAX_PAGES}) -- "
                      f"results may be incomplete.")

            print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
                  f"{len(jobs)} are US internships.")

        except Exception as e:
            print(f"Error scraping {COMPANY_NAME} with Playwright: {e}")
        finally:
            browser.close()

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