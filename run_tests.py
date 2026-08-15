"""
run_tests.py -- feeds each scraper a realistic payload and checks the result.

Each case supplies a US intern job (must be kept), an international intern job
(must be dropped), and a non-intern US job (must be dropped).
"""
import sys
import os
import importlib

import test_harness as H

H.install_mocks()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrapers"))


# ---- payload builders -----------------------------------------------------
def greenhouse(loc="Hawthorne, CA"):
    return {"jobs": [
        {"id": 101, "title": "Mechanical Engineering Intern (Summer 2027)",
         "location": {"name": loc}, "absolute_url": "https://boards.greenhouse.io/x/jobs/101"},
        {"id": 102, "title": "Propulsion Intern",
         "location": {"name": "Auckland, New Zealand"}, "absolute_url": "https://x/102"},
        {"id": 103, "title": "Senior Structures Engineer",
         "location": {"name": "Hawthorne, CA"}, "absolute_url": "https://x/103"},
    ]}


def lever():
    return [
        {"id": "abc-1", "text": "Manufacturing Engineering Intern",
         "categories": {"location": "Atlanta, GA"}, "hostedUrl": "https://jobs.lever.co/hermeus/abc-1"},
        {"id": "abc-2", "text": "Avionics Intern",
         "categories": {"location": "London, United Kingdom"}, "hostedUrl": "https://x/2"},
        {"id": "abc-3", "text": "Staff Engineer",
         "categories": {"location": "Atlanta, GA"}, "hostedUrl": "https://x/3"},
    ]


def workday(loc="Winchester, Virginia"):
    return {"jobPostings": [
        {"title": "2027 Manufacturing Internship Program", "locationsText": loc,
         "externalPath": "/job/Winchester/Intern_R-12345"},
        {"title": "Design Intern", "locationsText": "Bangalore, India",
         "externalPath": "/job/Bangalore/Intern_R-999"},
        {"title": "Principal Engineer", "locationsText": loc,
         "externalPath": "/job/Winchester/Eng_R-777"},
    ]}


def oracle(loc="Indianapolis, IN"):
    return {"items": [{"requisitionList": [
        {"Title": "Engineering Intern", "PrimaryLocation": loc, "Id": "REQ55501"},
        {"Title": "Software Intern", "PrimaryLocation": "Bengaluru, India", "Id": "REQ55502"},
        {"Title": "Sr Manager", "PrimaryLocation": loc, "Id": "REQ55503"},
    ]}]}


def phenom():
    return {"refineSearch": {"data": {"jobs": [
        {"title": "Mechanical Engineering Intern", "cityStateCountry": "Orlando, FL", "jobId": "MEI-1"},
        {"title": "Systems Intern", "cityStateCountry": "Melbourne, Australia", "jobId": "SYS-2"},
        {"title": "Program Manager", "cityStateCountry": "Orlando, FL", "jobId": "PM-3"},
    ]}}}


def html_cards(kind):
    """kind: 'greenhouse' (.opening) or 'siemens' (article)"""
    if kind == "greenhouse":
        return [
            H.FakeEl(children={
                "a": H.FakeEl("Aerodynamics Intern", {"href": "/ottoaviation/jobs/441"}),
                ".location": H.FakeEl("Jacksonville, FL"),
            }),
            H.FakeEl(children={
                "a": H.FakeEl("Flight Test Intern", {"href": "/ottoaviation/jobs/442"}),
                ".location": H.FakeEl("Toronto, ON"),
            }),
        ]
    return [
        H.FakeEl(children={
            "h3.article__header__text__title a": H.FakeEl(
                "Mechanical Engineering Intern", {"href": "/job/391822"}),
            ".article__header__text__subtitle span": H.FakeEl("Orlando, FL, United States"),
        }),
        H.FakeEl(children={
            "h3.article__header__text__title a": H.FakeEl(
                "Digital Intern", {"href": "/job/391823"}),
            ".article__header__text__subtitle span": H.FakeEl("Erlangen, Germany"),
        }),
    ]


def radancy_cards():
    """[data-job-id] cards for Radancy sites (L3Harris, Ford)."""
    def c(title, loc, jid):
        return H.FakeEl(f"{title}\nENGINEERING\n{loc}",
                        {"href": f"/job/x/slug/4832/{jid}", "data-job-id": jid})
    return [c("Mechanical Engineering Intern", "Orlando, FL", "1"),
            c("Systems Intern", "Melbourne, Australia", "2"),
            c("Program Manager", "Orlando, FL", "3")]


def castelion_cards():
    def li(title, loc, jid):
        return H.FakeEl(f"{title}\n{loc}\nApply", children={
            "a[href*='/job/']": H.FakeEl(title, {"href": f"/castelion-corporation/job/{jid}"})})
    return [li("Mechanical Engineering Intern", "Torrance, California, United States", "A1"),
            li("R&D Technician", "Midland, Texas, United States", "A2")]


def siemens_cards():
    T = "h3.article__header__text__title a"
    def c(title, loc, jid):
        return H.FakeEl(f"{title}\n{loc}", children={
            T: H.FakeEl(title, {"href": f"/en_US/externaljobs/JobDetail/{jid}"}),
            ".list-item-location": H.FakeEl(loc),
            ".list-item-jobId": H.FakeEl(f"Job ID: {jid}")})
    return [c("Mechanical Engineering Intern", "Orlando, FL, United States", "391822"),
            c("Digital Intern", "Erlangen, Germany", "391823")]


def pcsx():
    def p(pid, name, loc):
        return {"id": pid, "name": name, "locations": [loc],
                "standardizedLocations": [loc], "positionUrl": f"/careers/job/{pid}"}
    return {"data": {"positions": [
        p(1, "Mechanical Engineering Intern", "Orlando, FL, US"),
        p(2, "Systems Intern", "Brisbane, QLD, AU"),
        p(3, "Program Manager", "Orlando, FL, US")]}}


# ---- cases ----------------------------------------------------------------
CASES = [
    # (module, routes, html_cards, intercept, expected_kept_count)
    ("spacex",              {"greenhouse.io": [greenhouse()]},              None, None, 1),
    ("rocketlab",           {"greenhouse.io": [greenhouse("Long Beach, CA")]}, None, None, 1),
    ("vast_space",          {"greenhouse.io": [greenhouse("Long Beach, CA")]}, None, None, 1),
    ("hermeus",             {"lever.co": [lever()]},                        None, None, 1),
    ("blue_origin",         {"myworkdayjobs.com": [{}, workday("Kent, WA"), {}]},   None, None, 1),
    ("boeing",              {"myworkdayjobs.com": [{}, workday("Winchester, Virginia"), {}]},   None, None, 1),
    ("northrop_grumman",    {"myworkdayjobs.com": [{}, workday("Melbourne, FL"), {}]}, None, None, 1),
    ("raytheon",            {"myworkdayjobs.com": [{}, workday("Tucson, AZ"), {}]}, None, None, 1),
    ("johnson_and_johnson", {"myworkdayjobs.com": [{}, workday("New Brunswick, NJ"), {}]}, None, None, 1),
    ("honeywell",           {"oraclecloud.com": [oracle(), {"items": []}]},  None, None, 1),
    ("lockheed_martin",     {"eightfold.ai": [pcsx(), {"data": {"positions": []}}]}, None, None, 1),
    ("l3harris",            {},  radancy_cards(), None, 1),
    ("otto_aerospace",      {"myworkdayjobs.com": [{}, workday("Fort Worth, TX"), {}]}, None, None, 1),
    ("siemens",             {},  siemens_cards(), None, 1),
    ("ford",                {},  radancy_cards(), None, 1),
    ("castelion",           {},  castelion_cards(), None, 1),
]


def run():
    results = []
    for name, routes, cards, intercept, expected in CASES:
        H.reset(routes, cards, intercept)
        mod = importlib.import_module(name)
        importlib.reload(mod)
        try:
            jobs = mod.get_current_jobs()
        except Exception as e:
            results.append((name, "ERROR", f"{type(e).__name__}: {e}", H.LIFECYCLE[:]))
            continue

        n = len(jobs)
        status = "PASS" if n == expected else "MISMATCH"
        detail = f"kept {n}, expected {expected}"
        if jobs:
            first = list(jobs.values())[0]
            detail += f" | {first['title'][:38]} @ {first['location'][:26]}"
            if not first["url"] or first["url"].startswith("None"):
                status, detail = "MISMATCH", detail + " | BAD URL"
        results.append((name, status, detail, H.LIFECYCLE[:]))
    return results


if __name__ == "__main__":
    print(f"{'scraper':22} {'status':10} detail")
    print("-" * 100)
    leaks = []
    headful = []
    for name, status, detail, life in run():
        print(f"{name:22} {status:10} {detail}")
        launched = any(isinstance(x, tuple) for x in life)
        if launched and "browser_closed" not in life:
            leaks.append(name)
        for x in life:
            if isinstance(x, tuple) and x[1] is False:
                headful.append(name)
    print()
    if leaks:
        print(f"⚠️  browser never closed (leaks a chromium process each run): {', '.join(leaks)}")
    if headful:
        print(f"⚠️  launches headless=False (opens a visible window / fails on a server): {', '.join(headful)}")