import sqlite3
from location_filter import is_internship_title

conn = sqlite3.connect("master_jobs.db")
rows = conn.execute(
    "SELECT title, location FROM jobs WHERE company = 'Johnson & Johnson'"
).fetchall()

shown = hidden = 0
for title, location in rows:
    ok = is_internship_title(title)
    shown += ok
    hidden += not ok
    print(f"{'SHOWN ' if ok else 'HIDDEN'}  {title.strip()[:66]}")

print(f"\n{len(rows)} rows: {shown} shown, {hidden} hidden by the title filter")