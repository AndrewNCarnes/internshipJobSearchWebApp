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
    conn.commit()
    conn.close()

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