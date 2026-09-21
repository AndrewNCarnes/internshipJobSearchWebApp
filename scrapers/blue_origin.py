import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor, scrape_workday

COMPANY_NAME = "Blue Origin"
TENANT = "blueorigin"
SITE = "BlueOrigin"
WD = "wd5"

# This file used to carry its own copy of the Workday logic -- it is where
# workday_common.py was extracted FROM. Two behaviours were left behind here:
#
#   1. On an exception it returned the postings collected so far. save_jobs()
#      treats that partial dict as the complete board and deletes every stored
#      row missing from it, so one mid-run crash could wipe most of Blue
#      Origin's listings. workday_common returns None on failure instead.
#   2. It had no second-pass lookup for postings whose site is a bare city
#      with no state, which the shared module resolves via the job's detail
#      endpoint (country code).
#
# The facet logic it pioneered lives on in workday_common: Blue Origin's
# workerSubType facet reports far fewer interns than a text search finds, so
# the facet is only trusted above MIN_FACET_TRUST hits.


def get_current_jobs():
    return scrape_workday(COMPANY_NAME, TENANT, SITE, WD)


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()
