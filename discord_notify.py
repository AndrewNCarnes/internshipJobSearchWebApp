"""
discord_notify.py -- post the new internships from a run to a Discord webhook.

Why it reads the database instead of the scrapers' return values:
master_runner runs each scraper as a SUBPROCESS, so it never sees what
save_jobs() returned. The rows themselves are the only shared channel, and
they carry date_added -- so "what's new" is a query, not bookkeeping.

Nothing here ever raises. A Discord outage, a revoked webhook or no network
must not stop the nightly scrape; every failure prints and returns False.

Webhook URL comes from, in order:
  1. the DISCORD_WEBHOOK_URL environment variable
  2. a .discord_webhook file next to this one (gitignored -- the URL is a
     secret: anyone holding it can post to the channel)

Usage:
    from discord_notify import notify_new_jobs
    started = datetime.now()
    ...run scrapers...
    notify_new_jobs(started)

    python discord_notify.py --test          # last 24h, no posting
    python discord_notify.py --test --send   # last 24h, really post
"""
import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime, timedelta
from urllib import request as urlrequest, error as urlerror

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scoring import fit_score, fit_label

# Windows consoles default to cp1252, which cannot encode the emoji in the
# message body -- printing a preview raised UnicodeEncodeError (same trap
# add_company.py hit). The posted payload is always UTF-8 either way.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "master_jobs.db")
WEBHOOK_FILE = os.path.join(BASE_DIR, ".discord_webhook")

DISCORD_LIMIT = 2000        # hard cap per message, enforced by Discord
SAFE_LIMIT = 1900           # leave room for the header
MAX_COMPANIES = 25          # longer than this and it stops being a summary
TOP_MATCHES = 5             # postings worth pushing to a phone
FIT_THRESHOLD = 70          # scoring.fit_score: ~"good" and above
DASHBOARD_URL = "https://internships.streamlit.app"
TIMEOUT = 15


def webhook_url():
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not url and os.path.exists(WEBHOOK_FILE):
        try:
            with open(WEBHOOK_FILE, encoding="utf-8") as f:
                url = f.read().strip()
        except OSError as e:
            print(f"[discord] could not read {WEBHOOK_FILE}: {e}")
    return url


def new_jobs_since(since, db_path=DB_PATH):
    """Rows added at or after `since` (a datetime or 'YYYY-MM-DD HH:MM:SS')."""
    if isinstance(since, datetime):
        since = since.strftime("%Y-%m-%d %H:%M:%S")
    if not os.path.exists(db_path):
        print(f"[discord] no database at {db_path}")
        return []
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT company, title, location, url FROM jobs "
            "WHERE date_added >= ? ORDER BY company, title", (since,)).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as e:
        print(f"[discord] database read failed: {e}")
        return []


def _top_matches(rows, limit=TOP_MATCHES, threshold=FIT_THRESHOLD):
    """The few postings worth pushing to a phone: best fit first, one per
    company so a single employer's 300-posting dump can't fill the list."""
    scored = []
    for company, title, location, url in rows:
        score = fit_score(title, location)
        if score >= threshold:
            scored.append((score, company, title, location, url))
    scored.sort(key=lambda r: (-r[0], r[1]))

    picked, seen_companies = [], set()
    for row in scored:
        if row[1] in seen_companies:
            continue
        seen_companies.add(row[1])
        picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def _match_line(score, company, title, location, url):
    loc = (location or "").split(" | ")[0].strip()
    # <> around the URL stops Discord from unfurling five link previews
    line = f"{fit_label(score).upper()} · [{title.strip()}](<{url}>) — {company}"
    return f"{line} · {loc}" if loc else line


def build_messages(rows):
    """One message per run: the best few matches with links, then the totals.

    The per-posting list used to be the whole message and buried the channel
    (a run can add 790 rows). The counts alone were readable but not
    actionable. This is both: what to click now, and what the run did overall.
    """
    if not rows:
        return []

    by_company = {}
    for company, title, location, url in rows:
        by_company.setdefault(company, []).append((title, location, url))

    header = (f"**🆕 {len(rows)} new internship{'s' if len(rows) != 1 else ''}** "
              f"across {len(by_company)} "
              f"compan{'ies' if len(by_company) != 1 else 'y'}")

    matches = _top_matches(rows)
    if matches:
        match_block = ["", f"**Best matches for you** (fit {FIT_THRESHOLD}+)"]
        match_block += [_match_line(*m) for m in matches]
    else:
        match_block = ["", f"_No postings scored {FIT_THRESHOLD}+ this run._"]

    # busiest first: that is the useful signal at a glance
    ranked = sorted(by_company.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    def assemble(company_limit):
        lines = [f"• {c} — {len(j)}" for c, j in ranked[:company_limit]]
        if len(ranked) > company_limit:
            rest = sum(len(j) for _, j in ranked[company_limit:])
            lines.append(f"• …{len(ranked) - company_limit} more companies — {rest}")
        return "\n".join([header, *match_block, "", "**By company**", *lines,
                          "", DASHBOARD_URL])

    # The matches are the point of the message, so when it runs long it is the
    # company list that gives way, never a match.
    limit = MAX_COMPANIES
    message = assemble(limit)
    while len(message) > SAFE_LIMIT and limit > 3:
        limit -= 3
        message = assemble(limit)
    if len(message) > SAFE_LIMIT:
        message = message[:SAFE_LIMIT - 1] + "…"
    return [message]


def _post(url, content):
    data = json.dumps({"content": content, "allowed_mentions": {"parse": []}}
                      ).encode("utf-8")
    req = urlrequest.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "internship-monitor"})
    with urlrequest.urlopen(req, timeout=TIMEOUT) as resp:
        return 200 <= resp.status < 300


def notify_new_jobs(since, db_path=DB_PATH, dry_run=False):
    """Post new jobs added since `since`. Returns True if something was sent.
    Never raises."""
    try:
        rows = new_jobs_since(since, db_path)
        if not rows:
            print("[discord] no new jobs to report.")
            return False

        messages = build_messages(rows)
        if dry_run:
            print(f"[discord] DRY RUN -- {len(rows)} new job(s), "
                  f"{len(messages)} message(s):\n")
            print("\n---\n".join(messages))
            return False

        url = webhook_url()
        if not url:
            print("[discord] no webhook configured (set DISCORD_WEBHOOK_URL or "
                  "create .discord_webhook); skipping notification.")
            return False

        sent = 0
        for msg in messages:
            try:
                if _post(url, msg):
                    sent += 1
            except urlerror.HTTPError as e:
                print(f"[discord] HTTP {e.code} posting message {sent + 1}: "
                      f"{e.reason}")
                break
            except Exception as e:                      # network, DNS, timeout
                print(f"[discord] post failed: {e}")
                break
        if sent:
            print(f"[discord] posted {sent} message(s) for {len(rows)} new job(s).")
        return sent > 0
    except Exception as e:                              # belt and braces
        print(f"[discord] notification failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24,
                    help="look back this many hours (default 24)")
    ap.add_argument("--test", action="store_true",
                    help="print what would be sent instead of posting")
    ap.add_argument("--send", action="store_true",
                    help="with --test, actually post as well")
    args = ap.parse_args()

    since = datetime.now() - timedelta(hours=args.hours)
    notify_new_jobs(since, dry_run=args.test and not args.send)
    return 0


if __name__ == "__main__":
    sys.exit(main())
