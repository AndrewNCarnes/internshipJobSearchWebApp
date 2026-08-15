import sys
import os
import time

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "Viable Engineering Solutions"

# Viable Engineering Solutions runs its careers page on ADP Workforce Now (WFN).
# The public recruitment page --
#   https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html
#     ?cid=<cid>&ccId=<ccId>&type=JS&lang=en_US&selectedMenuKey=CurrentOpenings
# -- is a pure JavaScript shell; fetching it with requests returns only a
# "switch to a supported browser" stub. But the widget behind it calls a public,
# no-auth JSON endpoint that returns every open requisition in one shot:
#
#   GET .../careercenter/public/events/staffing/v1/job-requisitions
#         ?cid=<cid>&ccId=<ccId>&lang=en_US
#
# No CSRF token, no cookies, no pagination cursor -- the whole board comes back
# in a single response. This is the cleanest scraper class we have, on par with
# Greenhouse's JSON API, so there is no reason to drive a headless browser here.
#
# The cid/ccId pair below is taken straight from the careers URL you gave. If VES
# ever migrates ATS or regenerates the career center, the cid changes and the
# endpoint 404s -- which the guard below treats as a scrape failure (skip save),
# NOT an empty board (which would wipe stored rows).

CID = "f235e091-e446-4050-a9fd-156e35c53a12"
CCID = "19000101_000001"

API_URL = ("https://workforcenow.adp.com/mascsr/default/careercenter/public/"
           "events/staffing/v1/job-requisitions")
# The human-facing page for a single posting; ADP deep-links by requisition id.
JOB_URL_TMPL = ("https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                "recruitment.html?cid={cid}&ccId={ccId}&type=JS&lang=en_US"
                "&selectedMenuKey=CurrentOpenings&jobId={job_id}")

TIMEOUT = 30
RETRIES = 3
RETRY_BACKOFF = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def _get_json():
    """GET the requisitions endpoint with retries. Returns parsed JSON, or None
    on any transport / non-200 / non-JSON failure so the caller can skip the
    save rather than prune stored rows on a fluke."""
    params = {"cid": CID, "ccId": CCID, "lang": "en_US"}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS,
                             timeout=TIMEOUT)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    last = f"non-JSON body ({e})"
            else:
                last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    print(f"[{COMPANY_NAME}] requisitions request failed after {RETRIES} "
          f"tries: {last}")
    return None


def _extract_requisitions(payload):
    """ADP WFN has returned the list under a couple of shapes over time: a bare
    list, or wrapped in {'requisitions': [...]}. Normalize both, and return None
    if the shape is unrecognizable (treated as a failure, not an empty board)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("requisitions", "jobRequisitions", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # A dict with no known list key is an error/echo response, not "0 jobs".
        print(f"[{COMPANY_NAME}] unexpected JSON shape (dict without a "
              f"requisitions list); treating as failure.")
        return None
    print(f"[{COMPANY_NAME}] unexpected JSON shape ({type(payload).__name__}); "
          f"treating as failure.")
    return None


def _title_of(req):
    for key in ("requisitionTitle", "title", "postingTitle"):
        v = req.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Some tenants nest the title under a localized object.
    t = req.get("requisitionTitle") or {}
    if isinstance(t, dict):
        v = t.get("shortName") or t.get("longName")
        if v:
            return v.strip()
    return ""


def _id_of(req):
    for key in ("requisitionId", "itemID", "id", "requisitionID"):
        v = req.get(key)
        if v:
            return str(v)
    return ""


# Two-letter ISO country codes that are ALSO valid US state codes. ADP returns
# countryCode="CA" for Canada, which is identical to California's state code --
# appending a bare "CA" would make location_filter read a Toronto posting as
# California and wrongly keep it. For these, we spell the country out so the
# filter's foreign-country name veto (which lists "canada") fires cleanly and
# there is no collision with a US state abbreviation.
AMBIGUOUS_COUNTRY = {
    "CA": "Canada",   # vs California
    "IN": "India",    # vs Indiana
    "LA": None,       # Laos vs Louisiana -- ADP uses "LA" for Louisiana far
                      # more often; leave as-is and let state logic handle it
    "OR": None,       # (no country) -- Oregon
    "DE": "Germany",  # vs Delaware
    "ID": None,       # Indonesia uses "ID" but so does Idaho; ADP tenants here
                      # are US-centric, keep as Idaho
}


def _country_token(code):
    """Return how a countryCode should appear in the joined location string.
    None means 'omit it' (ambiguous but overwhelmingly US); a string means
    'use this spelled-out name'; otherwise the code passes through."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    if code == "US" or code == "USA":
        return "US"
    if code in AMBIGUOUS_COUNTRY:
        return AMBIGUOUS_COUNTRY[code] or ""
    return code


def _location_of(req):
    """ADP carries locations under requisitionLocations[].address with city,
    state (countrySubdivisionLevel1), and countryCode. Join the first location
    into a string location_filter understands, spelling out ambiguous foreign
    country codes (see AMBIGUOUS_COUNTRY) so 'CA'=Canada is never read as
    California. Falls back to a flat 'location' string if present."""
    locs = req.get("requisitionLocations") or req.get("locations") or []
    if isinstance(locs, list) and locs:
        addr = locs[0].get("address", locs[0]) if isinstance(locs[0], dict) else {}
        parts = []
        city = addr.get("cityName") or addr.get("city")
        if city:
            parts.append(str(city).strip())

        sub = addr.get("countrySubdivisionLevel1")
        state = ""
        if isinstance(sub, dict):
            state = sub.get("codeValue") or sub.get("shortName") or ""
        elif isinstance(sub, str):
            state = sub
        state = state or addr.get("stateCode") or addr.get("state") or ""
        if state:
            parts.append(str(state).strip())

        country = addr.get("countryCode") or addr.get("country") or ""
        if isinstance(country, dict):
            country = country.get("codeValue") or ""
        token = _country_token(country)
        if token:
            parts.append(token)
        return ", ".join(parts)

    flat = req.get("location")
    if isinstance(flat, str):
        return flat.strip()
    return ""


def get_current_jobs():
    payload = _get_json()
    if payload is None:
        return None
    reqs = _extract_requisitions(payload)
    if reqs is None:
        return None

    jobs = {}
    scanned = 0
    for req in reqs:
        if not isinstance(req, dict):
            continue
        scanned += 1
        title = _title_of(req)
        job_id = _id_of(req)
        if not title or not job_id:
            continue
        if not is_internship_title(title):
            continue
        location = _location_of(req)
        if not location:
            print(f"[{COMPANY_NAME}] no location parsed for: {title[:50]}")
            continue
        if is_us_location(location, COMPANY_NAME):
            url = JOB_URL_TMPL.format(cid=CID, ccId=CCID, job_id=job_id)
            jobs[job_id] = {"title": title, "location": location, "url": url}

    print(f"[{COMPANY_NAME}] scanned {scanned} listings; "
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