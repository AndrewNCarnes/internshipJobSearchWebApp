import sys
import os
import re
import time
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Ford"
BASE = "https://www.careers.ford.com"
SEARCH_URL = f"{BASE}/search-jobs?ac=&Country=6252001&State=&k=intern&orgIds=48560"
MAX_PAGES = 25    # ~19 cards per page; a fuse, not the normal exit
PAGE_DELAY = 1.5

# careers.ford.com/search-jobs is the same Radancy platform as L3Harris:
# server-rendered [data-job-id] cards, ?p=N pagination, no jobs API.
#
# Ford's own filters are applied server-side in SEARCH_URL:
#   Country=6252001  -> United States (GeoNames id)
#   orgIds=48560     -> Ford Motor Company
#   k=intern         -> keyword
# apply.ford.com (Oracle Recruiting) was the previous target; it serves an
# incomplete TLS chain and its API rejected requests, so this reads the public
# careers site instead.
CARD_SELECTOR = "[data-job-id]"
FALLBACK_SELECTORS = ["[data-job-id]", "a[href*='/job/']", "li.job-listing",
                      "[class*='job-listing']", "[class*='search-result']"]

# "PLANO, TX" / "Palm Bay, FL" / "Washington, DC"
LOCATION_LINE = re.compile(r"^[A-Za-z .'\-]+,\s*[A-Za-z .'\-]{2,}$")


def parse_card(card):
    """
    Card text arrives as stacked lines:
        Systems Engineering Intern
        ENGINEERING|NEW, GRADS
        PLANO, TX
    Title is the first line; location is the last line that looks like
    "City, ST". The middle line is a job category and is ignored.
    """
    try:
        text = card.inner_text() or ""
        href = card.get_attribute("href") or ""
        job_id = card.get_attribute("data-job-id") or ""
    except Exception:
        return None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None

    title = lines[0]
    location = ""
    for line in reversed(lines[1:]):
        if LOCATION_LINE.match(line):
            location = line
            break

    if not job_id:
        # /en/job/plano/systems-engineering-intern/4832/99069057264
        parts = [p for p in href.split("/") if p]
        job_id = parts[-1] if parts else title

    url = href if href.startswith("http") else BASE + href
    return job_id, title, location, url


def get_current_jobs():
    jobs = {}
    seen_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        total_cards = 0
        try:
            for page_num in range(1, MAX_PAGES + 1):
                url = SEARCH_URL if page_num == 1 else f"{SEARCH_URL}&p={page_num}"
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)

                cards = []
                for sel in FALLBACK_SELECTORS:
                    try:
                        found = page.query_selector_all(sel)
                    except Exception:
                        continue
                    if found:
                        cards = found
                        if page_num == 1 and sel != CARD_SELECTOR:
                            print(f"[{COMPANY_NAME}] using fallback selector '{sel}'.")
                        break

                if not cards:
                    if page_num == 1:
                        print(f"[{COMPANY_NAME}] ⚠️  no '{CARD_SELECTOR}' cards on the "
                              f"page -- the site layout probably changed.")
                    break

                fresh = 0
                for card in cards:
                    parsed = parse_card(card)
                    if not parsed:
                        continue
                    job_id, title, location, job_url = parsed
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    fresh += 1
                    total_cards += 1

                    if not is_internship_title(title):
                        continue
                    if not location:
                        print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
                        continue
                    if is_us_location(location, COMPANY_NAME):
                        jobs[job_id] = {
                            "title": title,
                            "location": location,
                            "url": job_url,
                        }

                # every card repeated -> the site ignored ?p and re-served page 1
                if fresh == 0:
                    break
                if page_num % 5 == 0:
                    print(f"[{COMPANY_NAME}] page {page_num} | {total_cards} cards read "
                          f"| {len(jobs)} matches so far")
                time.sleep(PAGE_DELAY)

            print(f"[{COMPANY_NAME}] read {total_cards} listings; "
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