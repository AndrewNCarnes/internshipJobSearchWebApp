"""
verify_all.py -- full verification sweep across all 15 scrapers.

Four checks per scraper:
  A. HAPPY PATH   correct job kept, foreign dropped, non-intern dropped
  B. FALSE POS    does it admit "Internal Audit Manager" / "International PM"?
  C. FAILURES     403 / connection reset / selector timeout -> empty, no crash
  D. INTEGRATION  run_monitor() writes the right rows to a real sqlite file
"""
import sys, os, sqlite3, importlib, io, contextlib, tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import test_harness as H
H.install_mocks()
sys.path.insert(0, os.path.join(BASE, "scrapers"))
import database

# --- payloads -------------------------------------------------------------
FALSE_POS = [("IT Internal Audit Manager", "Phoenix, AZ"),
             ("International Project Manager 4", "Melbourne, FL")]

def greenhouse(loc, extra=()):
    jobs = [{"id": 101, "title": "Mechanical Engineering Intern",
             "location": {"name": loc}, "absolute_url": "https://gh/101"},
            {"id": 102, "title": "Propulsion Intern",
             "location": {"name": "Auckland, New Zealand"}, "absolute_url": "https://gh/102"},
            {"id": 103, "title": "Senior Structures Engineer",
             "location": {"name": loc}, "absolute_url": "https://gh/103"}]
    for i, (t, l) in enumerate(extra):
        jobs.append({"id": 200 + i, "title": t, "location": {"name": l},
                     "absolute_url": f"https://gh/{200+i}"})
    return {"jobs": jobs}

def lever(extra=()):
    jobs = [{"id": "a1", "text": "Manufacturing Engineering Intern",
             "categories": {"location": "Atlanta, GA"}, "hostedUrl": "https://lv/a1"},
            {"id": "a2", "text": "Avionics Intern",
             "categories": {"location": "London, United Kingdom"}, "hostedUrl": "https://lv/a2"},
            {"id": "a3", "text": "Staff Engineer",
             "categories": {"location": "Atlanta, GA"}, "hostedUrl": "https://lv/a3"}]
    for i, (t, l) in enumerate(extra):
        jobs.append({"id": f"b{i}", "text": t, "categories": {"location": l},
                     "hostedUrl": f"https://lv/b{i}"})
    return jobs

def workday(loc, extra=()):
    p = [{"title": "2027 Manufacturing Internship Program", "locationsText": loc,
          "externalPath": "/job/x/Intern_R-12345"},
         {"title": "Design Intern", "locationsText": "Bangalore, India",
          "externalPath": "/job/y/Intern_R-999"},
         {"title": "Principal Engineer", "locationsText": loc,
          "externalPath": "/job/x/Eng_R-777"}]
    for i, (t, l) in enumerate(extra):
        p.append({"title": t, "locationsText": l, "externalPath": f"/job/z/R-{i}"})
    return {"jobPostings": p}

def oracle(loc, extra=()):
    r = [{"Title": "Engineering Intern", "PrimaryLocation": loc, "Id": "REQ1"},
         {"Title": "Software Intern", "PrimaryLocation": "Bengaluru, India", "Id": "REQ2"},
         {"Title": "Sr Manager", "PrimaryLocation": loc, "Id": "REQ3"}]
    for i, (t, l) in enumerate(extra):
        r.append({"Title": t, "PrimaryLocation": l, "Id": f"REQ{10+i}"})
    return {"items": [{"requisitionList": r}]}

def pcsx(extra=()):
    def p(pid, name, loc):
        return {"id": pid, "name": name, "locations": [loc],
                "standardizedLocations": [loc], "positionUrl": f"/careers/job/{pid}"}
    out = [p(1, "Mechanical Engineering Intern", "Orlando, FL, US"),
           p(2, "Systems Intern", "Brisbane, QLD, AU"),
           p(3, "Program Manager", "Orlando, FL, US")]
    for i, (t, l) in enumerate(extra):
        out.append(p(100 + i, t, "Phoenix, AZ, US"))
    return {"data": {"positions": out}}


def phenom(extra=()):
    j = [{"title": "Mechanical Engineering Intern", "cityStateCountry": "Orlando, FL", "jobId": "M1"},
         {"title": "Systems Intern", "cityStateCountry": "Melbourne, Australia", "jobId": "S2"},
         {"title": "Program Manager", "cityStateCountry": "Orlando, FL", "jobId": "P3"}]
    for i, (t, l) in enumerate(extra):
        j.append({"title": t, "cityStateCountry": l, "jobId": f"F{i}"})
    return {"refineSearch": {"data": {"jobs": j}}}

def gh_cards(extra=()):
    c = [H.FakeEl(children={"a": H.FakeEl("Aerodynamics Intern", {"href": "/o/jobs/441"}),
                            ".location": H.FakeEl("Jacksonville, FL")}),
         H.FakeEl(children={"a": H.FakeEl("Flight Test Intern", {"href": "/o/jobs/442"}),
                            ".location": H.FakeEl("Toronto, ON")})]
    for i, (t, l) in enumerate(extra):
        c.append(H.FakeEl(children={"a": H.FakeEl(t, {"href": f"/o/jobs/{500+i}"}),
                                    ".location": H.FakeEl(l)}))
    return c

def siemens_cards(extra=()):
    T = "h3.article__header__text__title a"
    def c(title, loc, jid):
        return H.FakeEl(f"{title}\n{loc}", children={
            T: H.FakeEl(title, {"href": f"/en_US/externaljobs/JobDetail/{jid}"}),
            ".list-item-location": H.FakeEl(loc),
            ".list-item-jobId": H.FakeEl(f"Job ID: {jid}")})
    out = [c("Mechanical Engineering Intern", "Orlando, FL, United States", "391822"),
           c("Digital Intern", "Erlangen, Germany", "391823")]
    for i, (t, l) in enumerate(extra):
        out.append(c(t, l, str(600 + i)))
    return out

def l3_cards(extra=()):
    def c(title, loc, jid):
        return H.FakeEl(f"{title}\nENGINEERING\n{loc}",
                        {"href": f"/en/job/x/slug/4832/{jid}", "data-job-id": jid})
    out = [c("Mechanical Engineering Intern", "Orlando, FL", "1"),
           c("Systems Intern", "Melbourne, Australia", "2"),
           c("Program Manager", "Orlando, FL", "3")]
    for i, (t, l) in enumerate(extra):
        out.append(c(t, l, f"x{i}"))
    return out


def castelion_cards(extra=()):
    # careers-page.com <li> blocks: "Title\nLocation\nApply", with the title
    # link and the Apply link sharing one /job/<id>, so ids must dedupe.
    def li(title, loc, jid):
        return H.FakeEl(f"{title}\n{loc}\nApply", children={
            "a[href*='/job/']":
                H.FakeEl(title, {"href": f"/castelion-corporation/job/{jid}"})})
    out = [li("Mechanical Engineering Intern", "Torrance, California, United States", "A1"),
           li("R&D Technician", "Midland, Texas, United States", "A2"),
           li("Avionics Intern", "London, United Kingdom", "A3")]
    for i, (t, l) in enumerate(extra):
        out.append(li(t, l, f"X{i}"))
    return out

GH  = "greenhouse.io"; LV = "lever.co"; WD = "myworkdayjobs.com"; OR_ = "oraclecloud.com"

CASES = [
    ("spacex",              lambda e: ({GH: [greenhouse("Hawthorne, CA", e)]}, None, None)),
    ("rocketlab",           lambda e: ({GH: [greenhouse("Long Beach, CA", e)]}, None, None)),
    ("vast_space",          lambda e: ({GH: [greenhouse("Long Beach, CA", e)]}, None, None)),
    ("hermeus",             lambda e: ({LV: [lever(e)]}, None, None)),
    # leading {} = the facet probe response (no facets -> text-search fallback)
    ("blue_origin",         lambda e: ({WD: [{}, workday("Kent, WA", e), {}]}, None, None)),
    ("boeing",              lambda e: ({WD: [{}, workday("Winchester, Virginia", e), {}]}, None, None)),
    ("northrop_grumman",    lambda e: ({WD: [{}, workday("Melbourne, FL", e), {}]}, None, None)),
    ("raytheon",            lambda e: ({WD: [{}, workday("Tucson, AZ", e), {}]}, None, None)),
    ("johnson_and_johnson", lambda e: ({WD: [{}, workday("New Brunswick, NJ", e), {}]}, None, None)),
    ("honeywell",           lambda e: ({OR_: [oracle("Indianapolis, IN", e), {"items": []}]}, None, None)),
    ("lockheed_martin",     lambda e: ({"pcsx": [pcsx(e), {"data": {"positions": []}}]}, None, None)),
    ("l3harris",            lambda e: ({}, l3_cards(e), None)),
    ("otto_aerospace",      lambda e: ({WD: [{}, workday("Fort Worth, TX", e), {}]}, None, None)),
    ("siemens",             lambda e: ({}, siemens_cards(e), None)),
    ("ford",                lambda e: ({}, l3_cards(e), None)),
    ("castelion",           lambda e: ({}, castelion_cards(e), None)),
]

FAILURE = {
    "spacex": ({GH: ["__403__"]}, None, None, False),
    "rocketlab": ({GH: ["__boom__"]}, None, None, False),
    "vast_space": ({GH: ["__403__"]}, None, None, False),
    "hermeus": ({LV: ["__boom__"]}, None, None, False),
    "blue_origin": ({WD: ["__403__"]}, None, None, False),
    "boeing": ({WD: ["__403__"]}, None, None, False),
    "northrop_grumman": ({WD: ["__boom__"]}, None, None, False),
    "raytheon": ({WD: ["__403__"]}, None, None, False),
    "johnson_and_johnson": ({WD: ["__403__"]}, None, None, False),
    "honeywell": ({OR_: ["__403__"]}, None, None, False),
    "lockheed_martin": ({"pcsx": ["__403__"]}, None, None, False),
    "l3harris": ({}, [], None, True),
    "otto_aerospace": ({WD: ["__403__"]}, None, None, False),
    "siemens": ({}, [], None, False),
    "ford": ({}, [], None, True),
    "castelion": ({}, [], None, True),
}

def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()

def main():
    tmp = tempfile.mkdtemp()
    database.DB_NAME = os.path.join(tmp, "verify.db")

    print(f"{'scraper':21} {'A:happy':9} {'B:false-pos':13} {'C:failure':11} {'D:db write'}")
    print("-" * 74)
    fp_total = 0
    problems = []

    for name, build in CASES:
        mod = importlib.import_module(name)
        importlib.reload(mod)

        # A. happy path
        routes, cards, inter = build(())
        if name in ("l3harris", "ford", "castelion"):
            H.reset(routes, cards, inter, html_pages=[cards, cards])
        elif name == "siemens":
            H.reset(routes, cards, inter,
                    html_by_selector={".article.article--result": cards})
        else:
            H.reset(routes, cards, inter)
        jobs, _ = quiet(mod.get_current_jobs)
        a_ok = len(jobs) == 1
        A = "PASS" if a_ok else f"kept {len(jobs)}"

        # B. false positives
        routes, cards, inter = build(FALSE_POS)
        if name in ("l3harris", "ford", "castelion"):
            H.reset(routes, cards, inter, html_pages=[cards, cards])
        elif name == "siemens":
            H.reset(routes, cards, inter,
                    html_by_selector={".article.article--result": cards})
        else:
            H.reset(routes, cards, inter)
        jobs_fp, _ = quiet(mod.get_current_jobs)
        admitted = len(jobs_fp) - 1
        fp_total += max(admitted, 0)
        B = "clean" if admitted <= 0 else f"admits {admitted}"

        # C. failure modes
        r, c, i, goto = FAILURE[name]
        H.reset(r, c, i, goto_raises=goto)
        try:
            jobs_f, _ = quiet(mod.get_current_jobs)
            if jobs_f is None:
                C = "None(skip)"      # run_monitor skips save entirely: safest
            elif jobs_f == {}:
                C = "{} (guarded)"    # database.py refuses to wipe on empty
            else:
                C = f"LEAK {len(jobs_f)}"
                problems.append(f"{name}: returns data after a failure")
        except Exception as e:
            C = f"CRASH {type(e).__name__}"
            problems.append(f"{name}: crashes on failure -- {e}")

        # D. integration: run_monitor -> sqlite
        routes, cards, inter = build(())
        if name in ("l3harris", "ford", "castelion"):
            H.reset(routes, cards, inter, html_pages=[cards, cards])
        elif name == "siemens":
            H.reset(routes, cards, inter,
                    html_by_selector={".article.article--result": cards})
        else:
            H.reset(routes, cards, inter)
        _, _ = quiet(mod.run_monitor)
        conn = sqlite3.connect(database.DB_NAME)
        n = conn.execute("SELECT COUNT(*) FROM jobs WHERE company=?",
                         (mod.COMPANY_NAME,)).fetchone()[0]
        url_ok = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company=? AND url LIKE 'http%'",
            (mod.COMPANY_NAME,)).fetchone()[0]
        conn.close()
        D = "PASS" if (n == 1 and url_ok == 1) else f"rows={n} urls={url_ok}"
        if D != "PASS":
            problems.append(f"{name}: db write {D}")

        print(f"{name:21} {A:9} {B:13} {C:11} {D}")

    print()
    print(f"false-positive titles admitted across all scrapers: {fp_total}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  -", p)
    else:
        print("no crashes, no bad URLs, no failed writes")

if __name__ == "__main__":
    main()