import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor, scrape_workday

COMPANY_NAME = "GE Aerospace"
TENANT = "geaerospace"
SITE = "GE_ExternalSite"
WD = "wd5"


def get_current_jobs():
    return scrape_workday(COMPANY_NAME, TENANT, SITE, WD)


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()
