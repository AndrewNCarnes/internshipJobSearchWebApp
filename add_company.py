"""
add_company.py -- guided intake for new companies.

Pipeline:
  1. PROBE   -- given a company + careers URL (or token), detect the ATS
  2. GENERATE -- write a scraper from the matching template
  3. SMOKE TEST -- hit the REAL endpoint once and sanity-check the output
  4. VERDICT -- verified (wire into master_runner) or unverified (quarantine)

The smoke test is the gate. Mock-based verify_all proves parsing logic; it does
NOT prove a scraper matches the live site (Ford passed every mock test and then
read Cologne jobs). So this hits the real board once and asks: did we get cards,
do titles look like jobs, do locations parse? Only a live pass auto-adds.

Usage:
    python add_company.py "Roush"
    python add_company.py "Roush" --url https://roush.com/careers
    python add_company.py "Roush" --greenhouse roushindustries
    python add_company.py "Roush" --dry-run      # probe + report, write nothing
"""
import sys
import os
import re
import argparse
import importlib.util

BASE = os.path.dirname(os.path.abspath(__file__))
SCRAPERS = os.path.join(BASE, "scrapers")
TEMPLATES = os.path.join(BASE, "templates")
sys.path.insert(0, BASE)
sys.path.insert(0, SCRAPERS)

try:
    import requests
except Exception:
    requests = None

from location_filter import is_us_location, is_internship_title

# Windows consoles default to cp1252, which can't encode the status glyphs
# below and raises UnicodeEncodeError mid-run -- which killed the process
# before it could emit RESULT_JSON, so the dashboard saw "no result". Force
# UTF-8 (and fall back to ASCII-safe markers if even that is unavailable).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    OK, WARN, FAIL, FOUND = "\u2713", "\u26a0", "\u2717", "\U0001f50d"
except Exception:
    OK, WARN, FAIL, FOUND = "[OK]", "[!]", "[X]", "[?]"


# --------------------------------------------------------------------------
# naming helpers
# --------------------------------------------------------------------------
def slug(company):
    """"Rocket Lab" -> "rocket_lab" (module name)."""
    s = re.sub(r"[^a-z0-9]+", "_", company.lower()).strip("_")
    return s or "company"


# Single common words collide with unrelated boards ("new" matched a stranger's
# Greenhouse board for "New York Air Brake"). Never guess these as a token.
STOPWORD_TOKENS = {
    "new", "american", "general", "united", "first", "national", "global",
    "the", "and", "group", "systems", "solutions", "technologies", "services",
    "industries", "international", "corp", "inc", "co", "company", "us", "usa",
}

def token_guesses(company):
    """
    Plausible ATS board tokens. Deliberately conservative: a wrong guess that
    happens to hit a live board generates a scraper for the WRONG company, so
    we only emit tokens tightly derived from the full name.
    """
    base = re.sub(r"[^a-z0-9]+", "", company.lower())
    hyphen = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    words = [w for w in re.split(r"[^a-z0-9]+", company.lower()) if w]

    out = [base, hyphen, base + "inc", base + "corp", base + "careers"]

    # acronym for 3+ word names: "New York Air Brake" -> "nyab"
    if len(words) >= 3:
        out.append("".join(w[0] for w in words))

    # first word ONLY if it's distinctive (not a generic stopword) AND long
    if words and words[0] not in STOPWORD_TOKENS and len(words[0]) >= 5:
        out.append(words[0])

    seen, uniq = set(), []
    for t in out:
        if t and t not in STOPWORD_TOKENS and t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


# --------------------------------------------------------------------------
# 1. PROBE -- detect the ATS
# --------------------------------------------------------------------------
ATS_HINTS = {
    "greenhouse": "greenhouse", "lever.co": "lever", "ashbyhq": "ashby",
    "myworkdayjobs": "workday", "oraclecloud": "oracle", "eightfold": "eightfold",
    "careers-page.com": "careerspage", "icims.com": "icims", "icims": "icims",
    "smartrecruiters": "smartrecruiters", "workable": "workable",
    "bamboohr": "bamboohr", "phenom": "phenom",
}


def _name_tokens(name):
    """Distinctive lowercase words from a company name (drop generic stopwords)."""
    return {w for w in re.split(r"[^a-z0-9]+", name.lower())
            if w and w not in STOPWORD_TOKENS and len(w) >= 3}


def _board_matches_company(data, platform, company):
    """
    Does this board's content plausibly belong to `company`? A token like 'new'
    can hit a stranger's board; guard against it by checking the board's own
    text (job URLs, apply links, titles) for the company's distinctive words.
    Returns True if related, False if it looks like a coincidental match.
    """
    want = _name_tokens(company)
    if not want:
        return True  # nothing distinctive to check; don't block

    # gather text the board itself emits
    blob = []
    jobs = data.get("jobs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    for j in jobs[:20]:
        if not isinstance(j, dict):
            continue
        for k in ("absolute_url", "hostedUrl", "jobUrl", "applyUrl",
                  "company", "companyName", "organizationName"):
            v = j.get(k)
            if isinstance(v, str):
                blob.append(v.lower())
    text = " ".join(blob)
    if not text:
        return True  # board gave us nothing to check; fall back to smoke test

    # a match needs at least one distinctive company word to appear somewhere
    return any(w in text for w in want)


def probe_apis(company):
    """Try Greenhouse/Lever/Ashby tokens directly. Returns (platform, token) or None."""
    if requests is None:
        return None
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for token in token_guesses(company):
        checks = [
            ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
             lambda d: isinstance(d, dict) and "jobs" in d and len(d["jobs"]) > 0),
            ("lever", f"https://api.lever.co/v0/postings/{token}?mode=json",
             lambda d: isinstance(d, list) and len(d) > 0),
            ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{token}",
             lambda d: isinstance(d, dict) and d.get("jobs")),
        ]
        for platform, url, ok in checks:
            try:
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 200 and ok(r.json()):
                    data = r.json()
                    if _board_matches_company(data, platform, company):
                        print(f"  [probe] {platform} board '{token}' -> LIVE (name matches)")
                        return platform, token
                    else:
                        print(f"  [probe] {platform} board '{token}' is live but its "
                              f"content doesn't mention '{company}' -- skipping "
                              f"(coincidental token)")
            except Exception:
                pass
    return None


def probe_careers_page(url):
    """Load a careers page and sniff for a known ATS. Returns (platform, detail) or None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        print("  [probe] playwright not available; skipping page sniff")
        return None

    found = {"hit": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36").new_page()

        def on_response(resp):
            u = resp.url.lower()
            for hint, platform in ATS_HINTS.items():
                if hint in u and not found["hit"]:
                    found["hit"] = (platform, resp.url[:120])

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  [probe] page load failed: {e}")

        # also scan iframes + links for ATS hints
        if not found["hit"]:
            for el in page.query_selector_all("iframe, a"):
                attr = (el.get_attribute("src") or el.get_attribute("href") or "").lower()
                for hint, platform in ATS_HINTS.items():
                    if hint in attr:
                        found["hit"] = (platform, attr[:120])
                        break
                if found["hit"]:
                    break
        browser.close()

    if found["hit"]:
        print(f"  [probe] page sniff -> {found['hit'][0]} ({found['hit'][1]})")
    return found["hit"]


# --------------------------------------------------------------------------
# 2. GENERATE -- write a scraper from a template
# --------------------------------------------------------------------------
def generate(company, platform, token_or_detail):
    tmpl_path = os.path.join(TEMPLATES, f"{platform}.py.tmpl")
    if not os.path.exists(tmpl_path):
        return None, (f"no template for '{platform}'. Detected but can't auto-generate; "
                      f"this is a Ford/Lockheed-class case needing manual work.")
    tmpl = open(tmpl_path).read()
    code = (tmpl.replace("{{COMPANY_NAME}}", company)
                .replace("{{TOKEN}}", str(token_or_detail)))
    out_path = os.path.join(SCRAPERS, f"{slug(company)}.py")
    return code, out_path


# --------------------------------------------------------------------------
# 3. SMOKE TEST -- hit the real endpoint once, sanity-check output
# --------------------------------------------------------------------------
def smoke_test(module_path):
    """
    Import the freshly-written scraper and call get_current_jobs() for real.
    Returns (verdict, reason, sample). verdict in {'verified','empty','error'}.
    """
    name = os.path.splitext(os.path.basename(module_path))[0]
    try:
        spec = importlib.util.spec_from_file_location(name, module_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        return "error", f"scraper failed to import/run: {e}", {}

    try:
        jobs = mod.get_current_jobs()
    except Exception as e:
        return "error", f"get_current_jobs() raised: {e}", {}

    if jobs is None:
        return "error", "scraper returned None (endpoint failed on first contact)", {}
    if not jobs:
        # not necessarily broken -- company may have no US internships right now
        return "empty", ("ran cleanly but found 0 US internships. Could be genuine, "
                         "or the filter/selectors miss the live site. Review before trusting."), {}

    # sanity: do the results actually look like internships in the US?
    bad_title = [j["title"] for j in jobs.values() if not is_internship_title(j["title"])]
    no_loc = [j["title"] for j in jobs.values() if not j.get("location")]
    sample = dict(list(jobs.items())[:3])
    if bad_title:
        return "error", f"{len(bad_title)} results aren't internships, e.g. {bad_title[0][:50]!r}", sample
    if no_loc:
        return "error", f"{len(no_loc)} results have no location parsed", sample
    return "verified", f"{len(jobs)} US internships, all titles + locations parse", sample


# --------------------------------------------------------------------------
# 4. wiring
# --------------------------------------------------------------------------
def _purge_staging_pyc(staged_path):
    """Remove the __pycache__ .pyc the smoke-test import left behind."""
    d = os.path.join(os.path.dirname(staged_path), "__pycache__")
    if not os.path.isdir(d):
        return
    stem = os.path.splitext(os.path.basename(staged_path))[0]
    for f in os.listdir(d):
        if f.startswith(stem + ".") and f.endswith(".pyc"):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def register_in_master(company):
    mr = os.path.join(BASE, "master_runner.py")
    fname = f"{slug(company)}.py"
    src = open(mr).read()

    m = re.search(r"(SCRAPERS\s*=\s*\[)(.*?)(\])", src, re.S)
    if not m:
        return "could not find SCRAPERS list -- add manually"

    body = m.group(2)

    # Match however the existing entries are written. Every entry in this list
    # is "scrapers/<file>.py"; writing a bare "<file>.py" registers a path that
    # does not resolve, so the scraper is listed but silently never runs (the
    # subprocess exits 2, gets logged as an error, and the batch moves on).
    uses_prefix = bool(re.search(r"[\"']scrapers[/\\]", body))
    entry = f"scrapers/{fname}" if uses_prefix else fname

    # Already registered? Check both spellings so we never double-add.
    if re.search(r"[\"'](?:scrapers[/\\])?" + re.escape(fname) + r"[\"']", body):
        return "already listed"

    indent = "    "
    im = re.search(r"\n(\s+)[\"']", body)
    if im:
        indent = im.group(1)

    new_body = body.rstrip()
    if new_body and not new_body.endswith(","):
        new_body += ","
    new_body += f'\n{indent}"{entry}",\n'

    open(mr, "w").write(src[:m.start()] + m.group(1) + new_body + m.group(3)
                        + src[m.end():])
    return f"registered as {entry}"


def find_existing_scraper(company):
    """Return the filename of a scraper that already covers this company, or
    None. Checks the exact slug filename first, then scans every scraper's
    COMPANY_NAME so that a hand-written scrapers/roush_engineering.py is found
    when the user types 'Roush' (slug -> roush.py, a different filename)."""
    if not os.path.isdir(SCRAPERS):
        return None

    target = f"{slug(company)}.py"
    for suffix in ("", ".unverified"):
        p = os.path.join(SCRAPERS, target + suffix)
        if os.path.exists(p):
            return os.path.basename(p)

    want = {w for w in re.split(r"[^a-z0-9]+", company.lower()) if w}
    if not want:
        return None
    for f in sorted(os.listdir(SCRAPERS)):
        if not (f.endswith(".py") or f.endswith(".unverified")):
            continue
        if f.startswith("_staging_") or f.startswith("probe"):
            continue
        try:
            head = open(os.path.join(SCRAPERS, f), encoding="utf-8",
                        errors="ignore").read(4000)
        except OSError:
            continue
        m = re.search(r"COMPANY_NAME\s*=\s*[\"']([^\"']+)[\"']", head)
        if not m:
            continue
        have = {w for w in re.split(r"[^a-z0-9]+", m.group(1).lower()) if w}
        if not have:
            continue
        # Word-token subset, not string prefix. "Roush" matches
        # "Roush Engineering", but "Saabre" does NOT match "Saab" -- a prefix
        # test would wrongly block that legitimately different company.
        if want <= have or have <= want:
            return f
    return None


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--url", help="careers page URL to sniff")
    ap.add_argument("--greenhouse"); ap.add_argument("--lever"); ap.add_argument("--ashby")
    ap.add_argument("--dry-run", action="store_true", help="probe + report, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing scraper for this company")
    ap.add_argument("--json", action="store_true",
                    help="emit a machine-readable result line for the dashboard")
    args = ap.parse_args()

    result = {"company": args.company, "platform": None, "token": None,
              "verdict": None, "reason": None, "sample": [], "file": None}

    def emit(code):
        if args.json:
            import json
            print("RESULT_JSON:" + json.dumps(result))
        return code

    # A scraper may already exist under a different filename -- slug("Roush") is
    # roush.py but the hand-written file is roush_engineering.py. Without this,
    # a verified run would write a second, competing scraper for the same
    # company, or overwrite a hand-tuned one with a generic template.
    existing = find_existing_scraper(args.company)
    if existing and not (args.force or args.dry_run):
        print(f"\n{WARN} '{args.company}' already has a scraper: {existing}")
        print("  Nothing written. Re-run with --force to replace it.")
        result["verdict"] = "exists"
        result["reason"] = f"already covered by scrapers/{existing}"
        result["file"] = existing
        return emit(0)

    print(f"\n=== add_company: {args.company} ===")

    # explicit token overrides probing
    platform = token = None
    for p in ("greenhouse", "lever", "ashby"):
        if getattr(args, p):
            platform, token = p, getattr(args, p)
            print(f"  [given] {platform} token '{token}'")
            break

    # If the user handed us a careers URL, trust what it points at over a
    # bare-token API guess -- a token like "quanta" can resolve to an unrelated
    # company's board (it did: an SF startup, not Quanta Services on iCIMS).
    if not platform and args.url:
        hit = probe_careers_page(args.url)
        if hit:
            platform, token = hit
            print(f"  [probe] careers URL points at {platform} -- trusting it "
                  f"over any token guess")
    if not platform:
        hit = probe_apis(args.company)
        if hit:
            platform, token = hit
            # sanity: a token-only match with no URL is weakly grounded. Warn
            # so the smoke test's verdict is read with appropriate suspicion.
            if not args.url:
                print(f"  [probe] matched by token guess alone -- if this is the "
                      f"wrong company, provide the careers URL")

    if not platform:
        print(f"\n{FAIL} could not detect a known ATS.")
        print("  Next: run a probe by hand (probe_castelion.py pattern) and paste the DOM.")
        result["verdict"] = "undetected"
        result["reason"] = "no known ATS found by API probe or page sniff"
        return emit(2)

    print(f"\n  platform: {platform}   token/detail: {token}")
    result["platform"] = platform
    result["token"] = str(token)

    if args.dry_run:
        print("  [dry-run] stopping before generation.")
        result["verdict"] = "detected"
        result["reason"] = f"{platform} (dry run, nothing written)"
        return emit(0)

    code, out = generate(args.company, platform, token)
    if code is None:
        print(f"\n{WARN} {out}")
        print("  This is a known-platform-but-no-template case. Needs a manual scraper.")
        result["verdict"] = "no_template"
        result["reason"] = out
        return emit(2)

    # Stage under a real .py name so Python can import it for the smoke test;
    # "<name>.py.staged" is not an importable module path. A bad generate thus
    # never overwrites a known-good <name>.py -- it lands on the staging file.
    staged = os.path.join(SCRAPERS, f"_staging_{slug(args.company)}.py")
    open(staged, "w").write(code)
    print(f"\n  wrote {os.path.basename(staged)}")

    verdict, reason, sample = smoke_test(staged)
    print(f"\n  SMOKE TEST: {verdict.upper()} -- {reason}")
    for jid, j in sample.items():
        print(f"     {j['title'][:44]:46} @ {j['location'][:34]}")
    result["verdict"] = verdict
    result["reason"] = reason
    result["sample"] = [{"title": j["title"], "location": j["location"]}
                        for j in sample.values()]

    if verdict == "verified":
        _purge_staging_pyc(staged)
        os.replace(staged, out)
        status = register_in_master(args.company)
        print(f"\n{OK} VERIFIED and added ({status}). Live on the next run.")
        result["file"] = os.path.basename(out)
        result["reason"] += f" ({status})"
        return emit(0)
    else:
        quarantine = out + ".unverified"
        _purge_staging_pyc(staged)
        os.replace(staged, quarantine)
        print(f"\n{WARN} QUARANTINED as {os.path.basename(quarantine)}")
        print("  NOT wired into master_runner. Review, fix, rename to .py when good.")
        result["file"] = os.path.basename(quarantine)
        return emit(1)


if __name__ == "__main__":
    sys.exit(main())