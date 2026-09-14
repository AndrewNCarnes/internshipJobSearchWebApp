import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor

COMPANY_NAME = "Amentum"
# Tenant is "pae" -- the legacy PAE tenant Amentum inherited after the merger,
# NOT "amentum". Guessing the company name here would 404.
TENANT = "pae"
SITE = "Amentum_Careers"
WD = "wd1"


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()