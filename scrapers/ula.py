import sys
import os
import time
import html
import xml.etree.ElementTree as ET

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "ULA"

# United Launch Alliance runs on SAP SuccessFactors Recruiting Marketing (RMK),
# the same "jobs.<company>.com" career-site builder used by a lot of large
# employers. The public INTERNSHIPS category page
# (https://jobs.ulalaunch.com/go/INTERNSHIPS/9628400/) renders its listings
# client-side from an OData call, so fetching that HTML with requests returns an
# empty shell -- "There are currently no open positions..." shows even when jobs
# exist. Rather than drive a headless browser (like the Siemens board needs), we
# use two JS-free endpoints every RMK instance exposes:
#
#   1. sitemal.xml  -- yes, the misspelling is real and load-bearing. It is an
#      RSS 1.0 feed listing EVERY posted requisition with title, location, and
#      link. No auth, no CSRF token, no pagination cap. This is the primary
#      source: one request gets the whole board, we filter to internships +
#      US locations ourselves.
#
#   2. tile-search-results JSON -- the endpoint the category page itself calls.
#      Filterable server-side by category, so it is a good fallback when the
#      sitemap is unreachable and lets us cross-check the internship count.
#
# ULA internships are summer-only, so an empty result outside the recruiting
# season (roughly Aug--Dec) is normal, NOT a scraper failure. That distinction
# matters because database.save_jobs deletes any stored row we do not return --
# so if a transport error ever looked like "zero jobs", it would wipe every ULA
# internship from the dashboard. We guard that below: transport/parse failure
# returns None (skip save), a genuinely empty board returns {} (allow prune).

BASE = "https://jobs.ulalaunch.com"
SITEMAP_URL = f"{BASE}/sitemal.xml"            # not a typo -- see note above
TILE_URL = f"{BASE}/tile-search-results/"
INTERNSHIP_CATEGORY = "Internships"

TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# RSS 1.0 / RDF namespaces the RMK sitemal.xml feed uses.
NS = {
    "rss": "http://purl.org/rss/1.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
}


def _get(url, params=None, accept_xml=True):
    """GET with retries. Returns response or None (never raises)."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and r.content:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"[{COMPANY_NAME}] request failed after {RETRIES} tries "
          f"({url}): {last}")
    return None


def _job_id_from_url(url):
    """RMK job URLs look like .../job/City-Title-ST/123456700/ -- the long
    numeric segment is the stable requisition id. Fall back to the whole path
    if the shape is unexpected so we never key on an empty string."""
    parts = [p for p in url.split("/") if p]
    for seg in reversed(parts):
        if seg.isdigit():
            return seg
    return parts[-1] if parts else url


def _clean(text):
    if not text:
        return ""
    return html.unescape(text).replace("\xa0", " ").strip()


def parse_sitemap(xml_bytes):
    """Parse the RMK sitemal.xml RSS feed into a list of
    (job_id, title, location, url) tuples. Returns None on a parse failure so
    the caller can distinguish 'broken feed' from 'empty board'."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"[{COMPANY_NAME}] sitemap XML parse error: {e}")
        return None

    items = root.findall(".//rss:item", NS)
    if not items:
        # Some instances emit plain <item> without the RSS namespace prefix.
        items = root.findall(".//item")

    results = []
    for it in items:
        def field(tag):
            el = it.find(f"rss:{tag}", NS)
            if el is None:
                el = it.find(tag)
            return _clean(el.text) if el is not None and el.text else ""

        title = field("title")
        link = field("link")
        if not link:
            # RSS 1.0 carries the URL on rdf:about of the <item>.
            link = it.get(f"{{{NS['rdf']}}}about") or ""
        link = _clean(link)
        if not title or not link:
            continue

        # RMK feeds usually carry the city/state in the title tail
        # ("... - Denver, CO") and/or a Google-feed <g:location> element.
        location = ""
        for loc_tag in ("location", "{http://base.google.com/ns/1.0}location"):
            el = it.find(loc_tag)
            if el is not None and el.text:
                location = _clean(el.text)
                break
        if not location and " - " in title:
            location = title.rsplit(" - ", 1)[-1].strip()

        results.append((_job_id_from_url(link), title, location, link))
    return results


def from_sitemap():
    r = _get(SITEMAP_URL)
    if r is None:
        return None
    rows = parse_sitemap(r.content)
    if rows is None:
        return None

    jobs = {}
    scanned = 0
    for job_id, title, location, url in rows:
        scanned += 1
        if not is_internship_title(title):
            continue
        if not location:
            print(f"[{COMPANY_NAME}] no location for intern posting: {title[:60]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            jobs[job_id] = {"title": title, "location": location, "url": url}

    print(f"[{COMPANY_NAME}] sitemap: scanned {scanned} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def from_tile_search():
    """Fallback: the category endpoint the live page calls. Returns the parsed
    HTML fragment's postings, or None on failure."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print(f"[{COMPANY_NAME}] tile-search fallback needs beautifulsoup4; "
              f"skipping.")
        return None

    jobs = {}
    scanned = 0
    startrow = 0
    page_size = 25
    for _ in range(40):  # hard cap: 40 * 25 = 1000 postings
        params = {
            "q": "",
            "sortColumn": "referencedate",
            "sortDirection": "desc",
            "category": INTERNSHIP_CATEGORY,
            "startrow": startrow,
        }
        r = _get(TILE_URL, params=params, accept_xml=False)
        if r is None:
            return None if not jobs else jobs
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("a.jobTitle-link") or soup.select("[class*='jobTitle'] a")
        if not cards:
            break
        for a in cards:
            href = a.get("href", "")
            title = _clean(a.get_text())
            if not href or not title:
                continue
            url = href if href.startswith("http") else BASE + href
            job_id = _job_id_from_url(url)
            scanned += 1
            # Location sits in a sibling span on the same tile.
            loc_el = a.find_parent(class_=lambda c: c and "tile" in c)
            location = ""
            if loc_el:
                span = loc_el.select_one("[class*='location']")
                if span:
                    location = _clean(span.get_text())
            if not is_internship_title(title):
                continue
            if location and is_us_location(location, COMPANY_NAME):
                jobs[job_id] = {"title": title, "location": location, "url": url}
        startrow += page_size
        time.sleep(1.0)

    print(f"[{COMPANY_NAME}] tile-search: scanned {scanned} listings; "
          f"{len(jobs)} are US internships.")
    return jobs


def get_current_jobs():
    jobs = from_sitemap()
    if jobs is None:
        print(f"[{COMPANY_NAME}] sitemap unavailable; trying tile-search "
              f"fallback...")
        jobs = from_tile_search()
    elif not jobs:
        # Sitemap worked and returned zero internships. Cross-check the
        # category endpoint before trusting an empty result: if the season is
        # live and the feed just lagged, the fallback catches it. If the
        # fallback also comes back empty (or unavailable), {} stands and the
        # off-season prune is correct.
        alt = from_tile_search()
        if alt:
            jobs = alt
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