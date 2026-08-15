import requests
import sys
import os
import re

# Allow script to import database.py from the folder above it
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database
from location_filter import is_us_location, is_internship_title

COMPANY_NAME = "SpaceX"
BOARD_TOKEN = "spacex"
API_URL = f"https://boards-api.greenhouse.io/v1/boards/{BOARD_TOKEN}/jobs"


def get_current_jobs():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        response = requests.get(API_URL, headers=headers)
        response.raise_for_status()
        
        jobs = {}
        for job in response.json().get("jobs", []):
            title = job.get("title", "")
            location = job.get("location", {}).get("name", "")
            
            # Relaxed regex: looks for "intern" anywhere in the title as a word root, ignoring trailing slashes/co-ops
            if is_internship_title(title) and is_us_location(location):
                jobs[str(job["id"])] = {
                    "title": title,
                    "location": location,
                    "url": job["absolute_url"]
                }
        return jobs
    except Exception as e:
        print(f"Error fetching {COMPANY_NAME}: {e}")
        return None

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