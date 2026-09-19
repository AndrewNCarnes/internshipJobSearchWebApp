import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from directemployers_common import (run_directemployers_monitor,
                                    scrape_directemployers)

COMPANY_NAME = "ENFRA"

# ENFRA (energy infrastructure; requested as "Enfra Solutions") posts on
# enfra.dejobs.org -- DirectEmployers, see directemployers_common.py. Unlike
# Textron the board has no job_type facet, and keyword search is fuzzy, so the
# whole board (~570 postings, ~57 pages) is paged and filtered by title.
SITE = "enfra.dejobs.org"


def get_current_jobs():
    return scrape_directemployers(COMPANY_NAME, SITE)


def run_monitor():
    run_directemployers_monitor(COMPANY_NAME, SITE)


if __name__ == "__main__":
    run_monitor()
