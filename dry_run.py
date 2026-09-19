"""
dry_run.py -- run a scraper's get_current_jobs() and print what it found.

NEVER saves: it calls get_current_jobs() only, not run_monitor(), so the
database is untouched. Use this to test a new or changed scraper live.

Usage:
    python dry_run.py ge_aerospace
    python dry_run.py amentum caterpillar --limit 20
"""
import sys
import os
import time
import argparse
import importlib.util

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from location_filter import is_internship_title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scrapers", nargs="+", help="module names under scrapers/")
    ap.add_argument("--limit", type=int, default=60, help="rows to print")
    args = ap.parse_args()

    for name in args.scrapers:
        name = os.path.splitext(os.path.basename(name))[0]
        path = os.path.join(BASE, "scrapers", name + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, "get_current_jobs"):
            print(f"\n##### {name}: no get_current_jobs() -- can't dry-run")
            continue

        t = time.time()
        jobs = mod.get_current_jobs()
        status = "None (FAILURE)" if jobs is None else f"{len(jobs)} jobs"
        print(f"\n##### {name}: {status} in {time.time() - t:.0f}s")
        for j in list((jobs or {}).values())[:args.limit]:
            flag = "" if is_internship_title(j["title"]) else "  <-- title?"
            print(f"  {j['title'][:60]:62} | {j['location'][:50]:50} | "
                  f"{j['url'][:70]}{flag}")


if __name__ == "__main__":
    main()
