import sys
import os
import re
import time
from playwright.sync_api import sync_playwright

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

COMPANY_NAME = "Kraft Heinz"
# Visit the actual web page with the search filter applied
URL = "https://jobs.kraftheinz.com/careers?query=intern"
BASE_JOB_URL = "https://jobs.kraftheinz.com/careers/job/"

def is_us_location(location_name):
    if not location_name:
        return False
    loc = location_name.lower()
    
    international_keywords = ["uk", "united kingdom", "canada", "india", "germany", "poland", "australia", "netherlands", "brazil", "italy", "france"]
    if any(k in loc for k in international_keywords):
        return False
        
    if "flexible" in loc or "remote" in loc or "virtual" in loc:
        return True
        
    us_keywords = [
        "united states", "usa", ", us", "us -", ", il", "illinois", "chicago",
        ", pa", "pennsylvania", "pittsburgh", ", ca", "california", ", oh", "ohio",
        ", ny", "new york", ", tx", "texas", ", fl", "florida", ", nj", "new jersey", ", mi", "michigan"
    ]
    return any(k in loc for k in us_keywords)

def get_current_jobs():
    jobs = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        intercepted_jobs = []

        def handle_response(response):
            # Eavesdrop specifically for Eightfold's internal job-loading JSON ping
            if "/api/apply/v2/jobs" in response.url and response.request.method == "GET":
                try:
                    data = response.json()
                    positions = data.get("positions", [])
                    intercepted_jobs.extend(positions)
                except:
                    pass

        # Attach the eavesdropper to the page
        page.on("response", handle_response)

        try:
            print(f"[{COMPANY_NAME}] Loading careers page and intercepting background API...")
            page.goto(URL, wait_until="networkidle", timeout=30000)
            
            # Give the browser time to finish its background fetches
            page.wait_for_timeout(5000) 
            
            # Parse the clean JSON we captured from the network tab
            for job in intercepted_jobs:
                title = job.get("name", "")
                location = job.get("location", "")
                job_id = str(job.get("id", ""))
                
                if re.search(r'\bintern', title.lower()) and is_us_location(location):
                    jobs[job_id] = {
                        "title": title,
                        "location": location,
                        "url": BASE_JOB_URL + job_id
                    }
                    
        except Exception as e:
            print(f"Error scraping {COMPANY_NAME}: {e}")
        finally:
            browser.close()
            
    return jobs

def run_monitor():
    current_jobs = get_current_jobs()
    if current_jobs is not None:
        n, d = database.save_jobs(COMPANY_NAME, current_jobs)
        if n > 0 or d > 0: print(f"[{COMPANY_NAME}] Added {n}, Removed {d}.")
        else: print(f"[{COMPANY_NAME}] No new internships.")

if __name__ == "__main__":
    run_monitor()