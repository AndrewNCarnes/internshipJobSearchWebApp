import subprocess
import time
import random
import sys
import datetime

# Add new scraper scripts here as you build them
SCRAPERS = [
    "scrapers/blue_origin.py",
    "scrapers/boeing.py",
    "scrapers/castelion.py",
    "scrapers/ford.py",
    "scrapers/hermeus.py",
    "scrapers/honda.py",
    "scrapers/honeywell.py",
    "scrapers/johnson_and_johnson.py",
    "scrapers/kraft_heinz.py",
    "scrapers/l3harris.py",
    "scrapers/lockheed_martin.py",
    "scrapers/northrop_grumman.py",
    "scrapers/otto_aerospace.py",
    "scrapers/raytheon.py",
    "scrapers/rocketlab.py",
    "scrapers/roush_engineering.py",
    "scrapers/saab.py",
    "scrapers/siemens.py",
    "scrapers/spacex.py",
    "scrapers/spx.py",
    "scrapers/ula.py",
    "scrapers/vast_space.py",
    "scrapers/viable_engineering.py",
]

def update_heartbeat():
    """Writes the current time to a file so the dashboard knows we are alive."""
    with open("status.txt", "w") as f:
        f.write(datetime.datetime.now().strftime("%m/%d/%Y, %I:%M %p"))

def run_all_scrapers():
    print("\n=== STARTING DAILY BATCH ===")
    
    for script in SCRAPERS:
        print(f"\nTriggering {script}...")
        try:
            subprocess.run([sys.executable, script], check=True)
            print(f"Successfully finished {script}.")
        except subprocess.CalledProcessError as e:
            print(f"❌ Error running {script}: {e}")
            
        time.sleep(random.uniform(10, 30))
        
    print("\n=== DAILY BATCH COMPLETE ===")

if __name__ == "__main__":
    while True:
        # 1. Ping the heartbeat so the dashboard knows we are running
        update_heartbeat()
        
        # 2. Run all the scripts once
        run_all_scrapers()
        
        # 3. Go to sleep for ~24 hours
        jitter = random.uniform(82800, 90000) 
        hours = round(jitter / 3600, 2)
        print(f"\nMaster runner is going to sleep for {hours} hours...")
        
        time.sleep(jitter)