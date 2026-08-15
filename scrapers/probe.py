import json
import requests

CID = "f235e091-e446-4050-a9fd-156e35c53a12"
CCID = "19000101_000001"
API_URL = ("https://workforcenow.adp.com/mascsr/default/careercenter/public/"
           "events/staffing/v1/job-requisitions")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

r = requests.get(API_URL, params={"cid": CID, "ccId": CCID, "lang": "en_US"},
                 headers=HEADERS, timeout=30)
data = r.json()

# What wrapper key is the list under?
print("top-level type:", type(data).__name__)
if isinstance(data, dict):
    print("top-level keys:", list(data.keys()))

reqs = data if isinstance(data, list) else (
    data.get("requisitions") or data.get("jobRequisitions")
    or data.get("items") or [])
print("count:", len(reqs))

# Full dump of the first record so we can see the real field names
if reqs:
    print("\n--- FIRST RECORD ---")
    print(json.dumps(reqs[0], indent=2)[:4000])

# Just the top-level keys of each, plus anything title-ish
print("\n--- KEYS PER RECORD ---")
for i, rq in enumerate(reqs):
    print(f"[{i}] keys:", list(rq.keys()))
    for k, v in rq.items():
        if "title" in k.lower() or "name" in k.lower():
            print(f"     {k} = {v!r}")