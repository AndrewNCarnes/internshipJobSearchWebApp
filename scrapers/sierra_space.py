import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor

COMPANY_NAME = "Sierra Space"
# Two tenants are live: "sierraspace" (current) and "snc" (legacy Sierra Nevada
# Corp), both serving the same site path. If this one starts returning 403 or
# an empty board, try TENANT = "snc" before assuming the scraper broke.
TENANT = "sierraspace"
SITE = "Sierra_Space_External_Career_Site"
WD = "wd1"


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()