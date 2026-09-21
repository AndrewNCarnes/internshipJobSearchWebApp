import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor, scrape_workday

COMPANY_NAME = 'Northrop Grumman'
TENANT = 'ngc'
SITE = 'Northrop_Grumman_External_Site'
WD = 'wd1'

# Was a standalone copy of the Workday paging/facet logic that
# workday_common.py now owns. That copy returned the postings collected so far
# whenever a request failed, and an empty dict when the very first request was
# refused -- and save_jobs() deletes every stored row missing from what it is
# given, so a 403 wiped this company's listings. The shared module returns
# None on failure instead, and resolves bare-city sites through the job's
# detail endpoint.


def get_current_jobs():
    return scrape_workday(COMPANY_NAME, TENANT, SITE, WD)


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()
