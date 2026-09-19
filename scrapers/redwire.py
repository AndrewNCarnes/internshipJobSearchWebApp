import sys
import os
import re
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import (is_us_location, is_internship_title,
                             is_plausible_internship)

COMPANY_NAME = "Redwire"

# careers.rdw.com is a server-rendered job board behind an AWS WAF bot check:
# plain requests get a 200 with an EMPTY body (it looks like success, which is
# exactly the trap), so pages are loaded in a real browser and the rendered
# HTML is parsed. Each result is an <article> card with one <li> per field
# (requisition id, workplace type, location, department), 10 per page, paged
# by ?page=N until a page comes back with no cards.
#
# Redwire also posts Edge Autonomy roles on a separate board
# (careers.edgeautonomy.io) -- not covered here.

SEARCH_URL = "https://careers.rdw.com/jobs/search?page={page}"
MAX_PAGES = 40
PAGE_DELAY = 1.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _field(card, name):
    li = card.select_one(f"li[class*='job-component-{name}']")
    return " ".join(li.get_text(" ", strip=True).split()) if li else ""


def _parse(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for card in soup.select("article.job-search-results-card-col, "
                            "div.job-search-results-card"):
        a = card.select_one(".job-search-results-card-title a[href]")
        if not a:
            continue
        cards.append({
            "title": a.get_text(" ", strip=True),
            "url": a["href"],
            "req": _field(card, "requisition-identifier"),
            "location": _field(card, "location"),
            "department": _field(card, "department"),
        })
    # an article and its inner div can both match; keep one per URL
    return list({c["url"]: c for c in cards}.values())


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    jobs = {}
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(user_agent=UA).new_page()
        try:
            for n in range(1, MAX_PAGES + 1):
                page.goto(SEARCH_URL.format(page=n), wait_until="networkidle",
                          timeout=60000)
                page.wait_for_timeout(1000)
                html = page.content()
                cards = _parse(html)
                if not cards:
                    if n == 1:
                        # page 1 always has jobs; empty means the WAF or the
                        # markup changed -- a failure, not an empty board
                        print(f"[{COMPANY_NAME}] no job cards on page 1; "
                              f"page structure or bot check changed.")
                        return None
                    break
                fresh = [c for c in cards if c["url"] not in seen]
                if not fresh:
                    break          # past the last page, site repeats itself
                for c in fresh:
                    seen.add(c["url"])
                    title = c["title"]
                    tagged = "intern" in c["department"].lower()
                    if not (is_internship_title(title) or
                            (tagged and is_plausible_internship(title))):
                        continue
                    location = c["location"] or _location_from_slug(c["url"])
                    if is_us_location(location, COMPANY_NAME):
                        job_id = c["req"] or c["url"].rsplit("/", 1)[-1]
                        jobs[job_id] = {"title": title, "location": location,
                                        "url": c["url"]}
                time.sleep(PAGE_DELAY)
            else:
                print(f"[{COMPANY_NAME}] hit MAX_PAGES; results incomplete.")
                return None
        except Exception as e:
            print(f"[{COMPANY_NAME}] browser error: {e}")
            return None
        finally:
            browser.close()

    print(f"[{COMPANY_NAME}] scanned {len(seen)} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def _location_from_slug(url):
    """Fallback: slugs end in the location, e.g. ...-littleton-colorado-united-states."""
    slug = url.rsplit("/", 1)[-1]
    slug = re.sub(r"-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$", "", slug)
    return slug.replace("-", " ")


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
