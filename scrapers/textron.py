import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from directemployers_common import (run_directemployers_monitor,
                                    scrape_directemployers)

COMPANY_NAME = "Textron"

# Textron (Bell, Textron Aviation, Textron Systems, TSV, Kautex) posts through
# DirectEmployers / NLX -- see directemployers_common.py. The site's own
# "Internship / Co-Op" filter is job_type=internship-co-op, so we page only
# that slice (~300) instead of all ~900 postings. Taleo sits behind it as the
# apply system but isn't needed to read the board.
SITE = "careers.textron.com"
JOB_TYPE = "internship-co-op"


def get_current_jobs():
    return scrape_directemployers(COMPANY_NAME, SITE, JOB_TYPE)


def run_monitor():
    run_directemployers_monitor(COMPANY_NAME, SITE, JOB_TYPE)


if __name__ == "__main__":
    run_monitor()
