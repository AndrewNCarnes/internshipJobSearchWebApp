"""
probe_quanta.py -- the ashby 'quanta' board returned only 5 jobs, 0 interns.
Is that Quanta Services, or a different company that happens to own the token?
Dump what's actually on the board so we know whether the scraper is right or
pointed at the wrong place.
"""
import requests

TOKEN = "quanta"
URL = f"https://api.ashbyhq.com/posting-api/job-board/{TOKEN}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

r = requests.get(URL, headers=HEADERS, timeout=20)
print(f"HTTP {r.status_code}")
if not r.ok:
    print(r.text[:300]); raise SystemExit

data = r.json()
jobs = data.get("jobs", [])
print(f"total postings on this board: {len(jobs)}\n")

# what company is this really?
names = set()
for j in jobs:
    for k in ("organizationName", "companyName", "teamName", "departmentName"):
        if j.get(k):
            names.add(j[k])
print("org/company/dept names seen:", names or "(none in payload)")

print("\nevery posting on the board:")
for j in jobs:
    title = j.get("title", "?")
    loc = j.get("location", "?")
    remote = " [remote]" if j.get("isRemote") else ""
    print(f"  - {title[:56]:58} @ {loc}{remote}")

# does the word intern appear anywhere at all?
intern_hits = [j.get("title") for j in jobs if "intern" in (j.get("title") or "").lower()]
print(f"\ntitles containing 'intern': {intern_hits or 'NONE'}")