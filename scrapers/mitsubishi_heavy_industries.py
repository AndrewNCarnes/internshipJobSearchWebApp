import sys
import os
import re
import time
import html
import xml.etree.ElementTree as ET

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Mitsubishi Heavy Industries"

# Requested as "Mitsubishi" -- there are several unrelated US Mitsubishi
# employers. This is the Mitsubishi Heavy Industries group board
# (mhicareers.com), which carries MITSUBISHI POWER AMERICAS (gas turbines, HQ
# Lake Mary FL / Orlando -- the one most relevant to a UCF ME student), plus
# Primetals Technologies and MHI America. Mitsubishi Electric is a separate
# employer with its own board and is not covered here.
#
# SuccessFactors RMK, same as ULA: the sitemal.xml RSS feed (misspelling is
# real) lists every posting in one request, with a Google-feed <g:location>
# like "Orlando, FL, US, 32809". The link path names the business unit
# (/MitsubishiPower/job/...), which we surface in the title.

BASE = "https://mhicareers.com"
FEED_URL = f"{BASE}/sitemal.xml"
TIMEOUT = 60
RETRIES = 3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
G = "{http://base.google.com/ns/1.0}"
UNIT_NAMES = {
    "MitsubishiPower": "Mitsubishi Power",
    "PrimetalsTechnologies": "Primetals",
}


def _fetch():
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(FEED_URL, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.content:
                return r.content
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:150]
        time.sleep(2 * attempt)
    print(f"[{COMPANY_NAME}] feed request failed after {RETRIES} tries: {last}")
    return None


def _text(el):
    return html.unescape(el.text).replace("\xa0", " ").strip() if el is not None and el.text else ""


def _unit(link):
    """https://mhicareers.com/MitsubishiPower/job/... -> 'Mitsubishi Power'."""
    m = re.match(r"https?://[^/]+/([^/]+)/job/", link)
    if not m:
        return ""
    seg = m.group(1)
    return UNIT_NAMES.get(seg, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", seg))


def get_current_jobs():
    """Return {job_id: {title, location, url}} on success, or None on failure."""
    raw = _fetch()
    if raw is None:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"[{COMPANY_NAME}] feed XML parse error: {e}")
        return None
    items = root.findall(".//item")
    if not items:
        # a ~300-posting group board is never empty; a shell means the feed broke
        print(f"[{COMPANY_NAME}] feed has no items; treating as failure.")
        return None

    jobs = {}
    for it in items:
        link = _text(it.find("link"))
        location = _text(it.find(f"{G}location"))
        job_id = _text(it.find(f"{G}id")) or _text(it.find("guid")) or link
        # Title arrives as "Warranty Program Associate Internship (Orlando, FL,
        # US, 32809)"; drop the trailing location copy.
        title = re.sub(r"\s*\([^()]*\)\s*$", "", _text(it.find("title"))).strip()
        if not job_id or not title or not is_internship_title(title):
            continue
        if not location:
            print(f"[{COMPANY_NAME}] no location for intern posting: {title[:60]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            unit = _unit(link)
            jobs[job_id] = {
                "title": f"{title} ({unit})" if unit else title,
                "location": re.sub(r",\s*\d{5}(-\d{4})?$", "", location),
                "url": link or BASE,
            }

    print(f"[{COMPANY_NAME}] scanned {len(items)} listings; "
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
