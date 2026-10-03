import sqlite3
import os
from datetime import datetime

# Lock the database path to the directory where this database.py file is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "master_jobs.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            location TEXT,
            url TEXT,
            date_added TEXT
        )
    ''')
    # One row per scraper per batch. Without this there is no record that a
    # scraper ran at all: Honda sat at 0 jobs for weeks and Ford's filters
    # broke silently, because a scraper that finds nothing looks exactly like
    # a scraper that works on a quiet board.
    c.execute('''
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scraper TEXT,
            company TEXT,
            ran_at TEXT,
            seconds REAL,
            exit_code INTEGER,
            status TEXT,          -- ok | empty | failed
            jobs_found INTEGER,   -- what the scraper reported this run
            rows_stored INTEGER   -- what the database holds for it afterwards
        )
    ''')
    c.execute("CREATE INDEX IF NOT EXISTS idx_runs_scraper ON scraper_runs(scraper, ran_at)")
    conn.commit()
    conn.close()


def record_scraper_run(scraper, company, ran_at, seconds, exit_code, status,
                       jobs_found, rows_stored):
    """Log one scraper's result. Never raises: a logging problem must not take
    down the nightly batch."""
    try:
        init_db()
        conn = sqlite3.connect(DB_NAME)
        conn.execute(
            "INSERT INTO scraper_runs (scraper, company, ran_at, seconds, "
            "exit_code, status, jobs_found, rows_stored) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (scraper, company, ran_at, seconds, exit_code, status,
             jobs_found, rows_stored))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"[health] could not record run for {scraper}: {e}")


def prune_scraper_runs(keep_per_scraper=30):
    """Keep the last N runs per scraper. The database is committed to git, so
    this table must not grow without bound."""
    try:
        conn = sqlite3.connect(DB_NAME)
        conn.execute("""
            DELETE FROM scraper_runs WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY scraper ORDER BY ran_at DESC) AS rn
                    FROM scraper_runs
                ) WHERE rn <= ?
            )
        """, (keep_per_scraper,))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"[health] could not prune run history: {e}")


def count_rows(company):
    """How many jobs are stored for this company right now."""
    try:
        conn = sqlite3.connect(DB_NAME)
        n = conn.execute("SELECT COUNT(*) FROM jobs WHERE company = ?",
                         (company,)).fetchone()[0]
        conn.close()
        return n
    except sqlite3.Error:
        return 0

def save_jobs(company, jobs_dict):
    init_db()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    new_jobs = 0
    active_unique_ids = []
    
    # 1. Add new jobs
    for raw_job_id, details in jobs_dict.items():
        unique_id = f"{company}_{raw_job_id}"
        active_unique_ids.append(unique_id)
        
        c.execute("SELECT job_id FROM jobs WHERE job_id = ?", (unique_id,))
        if not c.fetchone():
            c.execute('''
                INSERT INTO jobs (job_id, company, title, location, url, date_added)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (unique_id, company, details['title'], details['location'], details['url'], datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            new_jobs += 1
            
    # 2. Clean up dead jobs
    c.execute("SELECT job_id FROM jobs WHERE company = ?", (company,))
    existing_jobs = [row[0] for row in c.fetchall()]
    
    deleted_jobs = 0
    for existing_id in existing_jobs:
        if existing_id not in active_unique_ids:
            c.execute("DELETE FROM jobs WHERE job_id = ?", (existing_id,))
            deleted_jobs += 1
            
    conn.commit()
    conn.close()
    
    return new_jobs, deleted_jobs