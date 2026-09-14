import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workday_common import run_workday_monitor

COMPANY_NAME = "Caterpillar"
TENANT = "cat"
SITE = "CaterpillarCareers"
WD = "wd5"


def run_monitor():
    run_workday_monitor(COMPANY_NAME, TENANT, SITE, WD)


if __name__ == "__main__":
    run_monitor()