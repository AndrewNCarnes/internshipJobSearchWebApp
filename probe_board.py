"""
probe_board.py -- generic "what is this careers page built on?" probe.

Loads a careers URL in headless Chromium and reports:
  * which ATS it's built on (fingerprinted from URLs, script srcs, iframes, HTML)
  * whether job links are in the initial HTML (server-rendered) or only appear
    after JS runs (client-rendered)
  * every XHR/fetch response that looks like job data, with its JSON keys and
    the size of any lists inside it

Never guess a platform from a company name -- run this first.

Usage:
    python probe_board.py https://careers.example.com/jobs/
    python probe_board.py https://example.com/careers --show-all
    python probe_board.py https://example.com/careers --search intern
"""
import sys
import re
import json
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import requests
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# (platform, regex over URLs / HTML). Order matters only for display.
FINGERPRINTS = [
    ("workday", r"myworkdayjobs\.com|myworkdaysite\.com|/wday/cxs/"),
    ("greenhouse", r"greenhouse\.io|boards-api\.greenhouse"),
    ("lever", r"jobs\.lever\.co|api\.lever\.co"),
    ("ashby", r"ashbyhq\.com"),
    ("icims", r"icims\.com"),
    ("phenom", r"phenompeople\.com|/widgets\b.*refineSearch|phApp\b"),
    ("eightfold", r"eightfold\.ai|/api/pcsx/|/api/apply/v2/"),
    ("successfactors", r"successfactors|sitemal\.xml|jobs2web|rmkcdn"),
    ("oracle", r"oraclecloud\.com|/hcmUI/CandidateExperience"),
    ("taleo", r"taleo\.net"),
    ("adp", r"workforcenow\.adp\.com|recruiting\.adp\.com"),
    ("smartrecruiters", r"smartrecruiters\.com"),
    ("jobvite", r"jobvite\.com"),
    ("bamboohr", r"bamboohr\.com"),
    ("workable", r"workable\.com"),
    ("paycom", r"paycomonline\.net"),
    ("paylocity", r"paylocity\.com"),
    ("ultipro/ukg", r"ultipro\.com|ukg\.net"),
    ("dayforce", r"dayforcehcm\.com"),
    ("brassring", r"brassring\.com"),
    ("avature", r"avature\.net"),
    ("radancy/tmp", r"tmpwebeng\.com|radancy|/search-jobs/results"),
    ("nlx/directemployers", r"dejobs\.org|directemployers|nlx\.org"),
    ("jibe/google", r"jibeapply\.com|/api/jobs\?.*page="),
    ("clearcompany", r"clearcompany\.com"),
    ("rippling", r"ats\.rippling\.com"),
    ("breezy", r"breezy\.hr"),
    ("jazzhr", r"applytojob\.com|jazzhr"),
    ("pereless", r"pereless\.com|submit4jobs\.com"),
]

JOBISH_KEYS = re.compile(r"job|posting|requisition|position|opening|vacanc|title",
                         re.I)
SKIP_URL = re.compile(r"\.(png|jpe?g|gif|svg|woff2?|ttf|css|ico|mp4|webp)(\?|$)|"
                      r"google-analytics|googletagmanager|doubleclick|facebook|"
                      r"hotjar|onetrust|cookielaw|newrelic|segment\.|clarity\.ms|"
                      r"linkedin\.com/px|bing\.com", re.I)


def fingerprint(text):
    hits = []
    for name, pat in FINGERPRINTS:
        m = re.search(pat, text, re.I)
        if m:
            hits.append((name, m.group(0)))
    return hits


def summarize_json(obj, depth=0, path="$", out=None):
    """Find lists of dicts anywhere in the JSON; report path, length, keys."""
    if out is None:
        out = []
    if depth > 6:
        return out
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            out.append((path, len(obj), list(obj[0].keys())[:15]))
        for i, v in enumerate(obj[:1]):
            summarize_json(v, depth + 1, f"{path}[{i}]", out)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            summarize_json(v, depth + 1, f"{path}.{k}", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--show-all", action="store_true",
                    help="list every JSON response, not just job-looking ones")
    ap.add_argument("--wait", type=int, default=6000, help="ms to wait after load")
    ap.add_argument("--headful", action="store_true")
    args = ap.parse_args()

    print(f"\n=== probe_board: {args.url} ===")

    # 1. raw HTML (what a plain requests scraper would see)
    raw_html, raw_status = "", None
    try:
        r = requests.get(args.url, headers={"User-Agent": UA}, timeout=25)
        raw_status, raw_html = r.status_code, r.text
        print(f"\n[raw GET] HTTP {r.status_code}, {len(r.text):,} bytes, final URL {r.url}")
    except Exception as e:
        print(f"\n[raw GET] failed: {e}")

    responses = []
    seen_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(user_agent=UA)
        page = ctx.new_page()

        def on_response(resp):
            u = resp.url
            seen_urls.append(u)
            if SKIP_URL.search(u):
                return
            ctype = (resp.headers or {}).get("content-type", "")
            if resp.request.resource_type not in ("xhr", "fetch", "document") \
                    and "json" not in ctype:
                return
            body = None
            if "json" in ctype:
                try:
                    body = resp.json()
                except Exception:
                    body = None
            responses.append({"url": u, "status": resp.status,
                              "method": resp.request.method,
                              "post": (resp.request.post_data or "")[:400],
                              "ctype": ctype, "json": body})

        page.on("response", on_response)
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(args.wait)
        except Exception as e:
            print(f"[browser] load problem: {e}")

        final_url = page.url
        rendered = page.content()
        frames = [f.url for f in page.frames if f.url and f.url != "about:blank"]
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => [e.href, (e.innerText||'').trim().slice(0,80)])")
        title = page.title()
        browser.close()

    print(f"[browser] final URL {final_url}")
    print(f"[browser] title: {title!r}")

    # 2. platform fingerprint
    blob = "\n".join(seen_urls + frames + [final_url, rendered[:400000]])
    hits = fingerprint(blob)
    print("\n--- platform fingerprints ---")
    if hits:
        for name, ev in hits:
            print(f"  {name:22} evidence: {ev}")
    else:
        print("  none recognized")
    if frames[1:]:
        print("\n--- iframes ---")
        for f in frames[1:]:
            print("  ", f[:160])

    # Phenom refNum
    m = re.search(r"CareerConnectResources/(?:prod/)?([A-Z0-9]{5,12})/", blob)
    if m:
        print(f"\n  phenom refNum: {m.group(1)}")
    # Workday tenant/site
    for m in set(re.findall(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkday(?:jobs|site)\.com/"
                            r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", blob)):
        print(f"  workday candidate: tenant={m[0]} wd={m[1]} site={m[2]}")

    # 3. server- vs client-rendered
    job_link = re.compile(r"/job[s]?/|jobid|job_id|requisition|/position|jobdetail|"
                          r"/careers?/.+\d{4,}", re.I)
    raw_links = len(re.findall(r'href="[^"]*(?:/jobs?/|jobid|job_id|requisition|jobdetail)[^"]*"',
                               raw_html, re.I))
    rendered_links = [l for l in links if job_link.search(l[0])]
    print("\n--- rendering ---")
    print(f"  job-like links in raw HTML:      {raw_links}")
    print(f"  job-like links after JS render:  {len(rendered_links)}")
    if raw_links and raw_links >= len(rendered_links) * 0.5:
        print("  => looks SERVER-RENDERED (requests + BeautifulSoup may suffice)")
    elif rendered_links:
        print("  => looks CLIENT-RENDERED (need the XHR below, or a browser)")
    for href, text in rendered_links[:12]:
        print(f"     {text[:50]:52} {href[:110]}")

    # 4. job-data XHRs
    print("\n--- JSON / XHR responses ---")
    shown = 0
    for r in responses:
        j = r["json"]
        if j is None:
            continue
        lists = summarize_json(j)
        jobby = any(JOBISH_KEYS.search(" ".join(k)) for _, _, k in lists) or \
            JOBISH_KEYS.search(r["url"])
        if not (jobby or args.show_all):
            continue
        shown += 1
        print(f"\n  {r['method']} {r['status']} {r['url'][:180]}")
        if r["post"]:
            print(f"    body: {r['post'][:300]}")
        if isinstance(j, dict):
            print(f"    top keys: {list(j.keys())[:20]}")
        for path, n, keys in lists[:6]:
            print(f"    list {path} (len {n}) keys: {keys}")
    if not shown:
        print("  no job-looking JSON responses captured")

    print("\n--- all non-asset request hosts ---")
    hosts = sorted({re.sub(r"^https?://([^/]+).*", r"\1", u) for u in seen_urls
                    if not SKIP_URL.search(u)})
    print("  " + ", ".join(hosts[:40]))


if __name__ == "__main__":
    main()
