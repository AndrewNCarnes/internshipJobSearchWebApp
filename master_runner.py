import os
import re
import subprocess
import time
import random
import sys
import datetime

import database

from git_push import push_results
from discord_notify import notify_new_jobs

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
    "scrapers/amentum.py",
    "scrapers/caterpillar.py",
    "scrapers/eli_lilly.py",
    "scrapers/ge_aerospace.py",
    "scrapers/ge_vernova.py",
    "scrapers/sierra_space.py",
    "scrapers/textron.py",
    "scrapers/kratos.py",
    "scrapers/general_dynamics.py",
    "scrapers/anduril_industries.py",
    "scrapers/relativity_space.py",
    "scrapers/ursa_major.py",
    "scrapers/stoke_space.py",
    "scrapers/redwire.py",
    "scrapers/firefly_aerospace.py",
    "scrapers/beehive_industries.py",
    "scrapers/terran_orbital.py",
    "scrapers/disney.py",
    "scrapers/henkel.py",
    "scrapers/knorr_bremse.py",
    "scrapers/mitsubishi_heavy_industries.py",
    "scrapers/enfra.py",
    "scrapers/iron_will_ventures.py",
]

def update_heartbeat():
    """Writes the current time to a file so the dashboard knows we are alive."""
    # Absolute path: this used to be a bare "status.txt", which lands in
    # whatever directory the process was launched from. The dashboard and
    # git_push both look for it in the project root.
    with open(STATUS_PATH, "w") as f:
        f.write(datetime.datetime.now().strftime("%m/%d/%Y, %I:%M %p"))

# Every scraper prints this line on success -- it is the contract in §3 of
# PROJECT_CONTEXT.md ("scanned N listings; M are US internships"), with a
# shorter variant from workday_common ("[Company] 12 US internships.").
FOUND_RE = re.compile(r"(\d+)\s+(?:are\s+)?US internships")
FAILED_RE = re.compile(r"scrape failed|returning None|treating as failure",
                       re.IGNORECASE)
COMPANY_RE = re.compile(r"^COMPANY_NAME\s*=\s*[\"'](.+?)[\"']", re.MULTILINE)


def company_of(script):
    """The COMPANY_NAME a scraper saves under -- needed to count its rows,
    since the filename and the company name often differ
    (scrapers/knorr_bremse.py -> "Knorr-Bremse")."""
    try:
        with open(os.path.join(BASE_DIR, script), encoding="utf-8") as f:
            m = COMPANY_RE.search(f.read(4000))
        return m.group(1) if m else ""
    except OSError:
        return ""


def run_all_scrapers():
    print("\n=== STARTING DAILY BATCH ===")

    for script in SCRAPERS:
        print(f"\nTriggering {script}...")
        company = company_of(script)
        started = time.time()
        output, exit_code = "", 0
        try:
            # Absolute path for the same reason as the heartbeat -- the relative
            # "scrapers/x.py" only resolves when cwd is the project root.
            # Output is captured so the run can be recorded, then echoed so the
            # terminal still shows everything it always did.
            proc = subprocess.run([sys.executable, os.path.join(BASE_DIR, script)],
                                  cwd=BASE_DIR, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            output = (proc.stdout or "") + (proc.stderr or "")
            exit_code = proc.returncode
            print(output, end="" if output.endswith("\n") else "\n")
            if exit_code == 0:
                print(f"Successfully finished {script}.")
            else:
                print(f"❌ Error running {script}: exit {exit_code}")
        except Exception as e:                      # launch failure, not a scrape
            exit_code, output = -1, str(e)
            print(f"❌ Could not run {script}: {e}")

        matches = FOUND_RE.findall(output)
        jobs_found = int(matches[-1]) if matches else None
        if exit_code != 0 or FAILED_RE.search(output) or jobs_found is None:
            status = "failed"
        elif jobs_found == 0:
            status = "empty"
        else:
            status = "ok"

        database.record_scraper_run(
            scraper=script, company=company,
            ran_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            seconds=round(time.time() - started, 1), exit_code=exit_code,
            status=status, jobs_found=jobs_found,
            rows_stored=database.count_rows(company) if company else 0)

        time.sleep(random.uniform(10, 30))

    database.prune_scraper_runs()
    print("\n=== DAILY BATCH COMPLETE ===")

if __name__ == "__main__":
    while True:
        # 1. Ping the heartbeat so the dashboard knows we are running
        update_heartbeat()

        # 2. Run all the scripts once. Note the time first: the scrapers run as
        #    subprocesses, so the only record of what they added is the rows'
        #    own date_added -- "new this run" means "added at or after this".
        batch_started = datetime.datetime.now()
        run_all_scrapers()

        # 3. Publish the fresh database so the deployed dashboard sees it
        push_results()

        # 4. Tell Discord what turned up (never raises; no webhook = no-op)
        notify_new_jobs(batch_started)

        # 5. Go to sleep for ~24 hours
        jitter = random.uniform(82800, 90000)
        hours = round(jitter / 3600, 2)
        print(f"\nMaster runner is going to sleep for {hours} hours...")

        time.sleep(jitter)