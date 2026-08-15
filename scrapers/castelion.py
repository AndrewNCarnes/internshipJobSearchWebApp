import sys
import os
import re
import time
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Castelion"
BOARD = "https://www.careers-page.com"
BOARD_PATH = "/castelion-corporation"
SEARCH_URL = f"{BOARD}{BOARD_PATH}"
MAX_PAGES = 20
PAGE_DELAY = 1.5

# Castelion isn't on Greenhouse/Lever/Ashby -- all nine token probes 404'd.
# They use careers-page.com, a server-rendered board with no JSON API. Each
# listing is an <li> whose text reads:
#     R&D Technician / Midland, Texas, United States / Apply
# and which contains two links to the same /job/<id> (the title and "Apply"),
# so job ids are deduped.
#
# Their own site (castelion.com/careers) renders the same jobs, but a marketing
# page is far more likely to be redesigned than the ATS board, so scrape here.
JOB_LINK = "a[href*='/job/']"
NOISE = re.compile(r"^(apply|view details|share|save)$", re.I)


def parse_card(card):
    """Pull (job_id, title, location, url) out of one listing block."""
    try:
        link = card.query_selector(JOB_LINK)
        if not link:
            return None
        href = link.get_attribute("href") or ""
        text = card.inner_text() or ""
    except Exception:
        return None

    if "/job/" not in href:
        return None

    lines = [l.strip() for l in text.split("\n") if l.strip() and not NOISE.match(l.strip())]
    if not lines:
        return None

    title = lines[0]
    location = lines[1] if len(lines) > 1 else ""

    job_id = href.rstrip("/").split("/")[-1]
    url = href if href.startswith("http") else BOARD + href
    return job_id, title, location, url


def get_current_jobs():
    jobs = {}
    seen_ids = set()
    scanned = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ).new_page()

        try:
            for page_num in range(1, MAX_PAGES + 1):
                url = SEARCH_URL if page_num == 1 else f"{SEARCH_URL}?page={page_num}"
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)

                cards = [c for c in page.query_selector_all("li")
                         if c.query_selector(JOB_LINK)]

                if not cards:
                    if page_num == 1:
                        print(f"[{COMPANY_NAME}] ⚠️  no job listings found at "
                              f"{SEARCH_URL} -- board moved or layout changed.")
                    break

                fresh = 0
                for card in cards:
                    parsed = parse_card(card)
                    if not parsed:
                        continue
                    job_id, title, location, job_url = parsed
                    if job_id in seen_ids:
                        continue          # title link and Apply link share an id
                    seen_ids.add(job_id)
                    fresh += 1
                    scanned += 1

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

                if fresh == 0:            # ?page ignored, same cards re-served
                    break
                time.sleep(PAGE_DELAY)

            print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
                  f"{len(jobs)} are US internships.")

        except Exception as e:
            print(f"Error scraping {COMPANY_NAME}: {e}")
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