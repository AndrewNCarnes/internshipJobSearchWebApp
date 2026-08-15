import sys
import os
import re
from playwright.sync_api import sync_playwright

# Allow script to import database.py from the folder above it
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

COMPANY_NAME = "Honda"
HONDA_JOBS_URL = "https://careers.honda.com/us/en/search-results?keywords=intern"

def is_us_location(location_name):
    if not location_name:
        return False
    loc = location_name.lower()
    
    international_keywords = ["canada", "toronto", "mexico", "japan", "tokyo", "uk"]
    if any(keyword in loc for keyword in international_keywords):
        return False
        
    if "flexible" in loc or "remote" in loc:
        return True
        
    us_keywords = [
        "united states", "usa", ", us", "us -", ", ca", "california", ", oh", "ohio", 
        "marysville", "raymond", "columbus", ", indiana", ", in", "greensburg", 
        ", nc", "north carolina", ", ga", "georgia", ", al", "alabama", 
        ", sc", "south carolina", ", ny", "new york", ", tx", "texas"
    ]
    return any(keyword in loc for keyword in us_keywords)

def get_current_jobs():
    jobs = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # We will store the clean JSON data the site's background API returns here
        intercepted_api_jobs = []

        def handle_response(response):
            # Phenom ATS loads jobs dynamically through an internal /widgets endpoint
            if "widgets" in response.url and response.request.method == "POST":
                try:
                    data = response.json()
                    # Drill down into Phenom's specific JSON structure to grab the raw data
                    if "refineSearch" in data and "data" in data["refineSearch"]:
                        found_jobs = data["refineSearch"]["data"].get("jobs", [])
                        intercepted_api_jobs.extend(found_jobs)
                except:
                    pass

        # Tell Playwright to listen to all network traffic on the page
        page.on("response", handle_response)
        
        try:
            print(f"[{COMPANY_NAME}] Loading careers page and intercepting background API...")
            page.goto(HONDA_JOBS_URL, wait_until="networkidle", timeout=30000)
            
            # Give the site a few seconds to finish its background API calls
            page.wait_for_timeout(5000)
            
            # Parse the clean JSON we captured straight from the network!
            for job in intercepted_api_jobs:
                title = job.get("title", "")
                
                # Phenom stores locations in various fields depending on the specific job
                location = job.get("cityStateCountry", "") or job.get("location", "")
                job_id = job.get("jobId", "") or job.get("jobSeqNo", "")
                
                if re.search(r'\bintern', title.lower()) and is_us_location(location):
                    # Build the direct URL. Phenom uses: /us/en/job/{jobId}/{title-slug}
                    title_slug = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')
                    url = f"https://careers.honda.com/us/en/job/{job_id}/{title_slug}"
                    
                    jobs[str(job_id)] = {
                        "title": title,
                        "location": location,
                        "url": url
                    }
                    
        except Exception as e:
            print(f"Error scraping {COMPANY_NAME} with Playwright: {e}")
        finally:
            browser.close()
            
    return jobs

def run_monitor():
    print(f"Starting {COMPANY_NAME} USA Internship Check...")
    current_jobs = get_current_jobs()
    
    if current_jobs is not None:
        new_count, deleted_count = database.save_jobs(COMPANY_NAME, current_jobs)
        
        if new_count > 0:
            print(f"[{COMPANY_NAME}] Added {new_count} new internships!")
        else:
            print(f"[{COMPANY_NAME}] No new internships.")
            
        if deleted_count > 0:
            print(f"[{COMPANY_NAME}] Removed {deleted_count} dead internships.")

if __name__ == "__main__":
    run_monitor()