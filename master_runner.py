import os
import subprocess
import time
import random
import sys
import datetime

from git_push import push_results

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_PATH = os.path.join(BASE_DIR, "status.txt")

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
    # Absolute path: this used to be a bare "status.txt", which lands in
    # whatever directory the process was launched from. The dashboard and
    # git_push both look for it in the project root.
    with open(STATUS_PATH, "w") as f:
        f.write(datetime.datetime.now().strftime("%m/%d/%Y, %I:%M %p"))

def run_all_scrapers():
    print("\n=== STARTING DAILY BATCH ===")

    for script in SCRAPERS:
        print(f"\nTriggering {script}...")
        try:
            # Absolute path for the same reason as the heartbeat -- the relative
            # "scrapers/x.py" only resolves when cwd is the project root.
            subprocess.run([sys.executable, os.path.join(BASE_DIR, script)],
                           check=True, cwd=BASE_DIR)
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

        # 3. Publish the fresh database so the deployed dashboard sees it
        push_results()

        # 4. Go to sleep for ~24 hours
        jitter = random.uniform(82800, 90000)
        hours = round(jitter / 3600, 2)
        print(f"\nMaster runner is going to sleep for {hours} hours...")

        time.sleep(jitter)