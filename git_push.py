import os
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))

# Only these get auto-committed. Never `git add -A` from an automated loop --
# that would sweep up half-finished code edits, .unverified scrapers, and any
# stray files into an unreviewed commit at 3am.
PUSH_FILES = ["master_jobs.db", "status.txt"]


def _git(*args, timeout=120):
    """Run a git command in the project dir. Returns (returncode, output)."""
    try:
        p = subprocess.run(["git"] + list(args), cwd=BASE, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        return p.returncode, (p.stdout or "").strip()
    except FileNotFoundError:
        return 127, "git not installed / not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "git timed out (auth prompt? network hang?)"


def push_results():
    """Commit the database + heartbeat and push to main.

    Every branch here returns rather than raises: this is called from an
    infinite daily loop, and a git problem (offline, rejected push, no
    credentials) must never kill the scraping schedule.
    """
    if not os.path.isdir(os.path.join(BASE, ".git")):
        print("[git] not a git repo; skipping push.")
        return

    # Nothing changed? Skip quietly -- this is the normal no-new-jobs case,
    # and `git commit` would exit 1 on an empty commit.
    rc, out = _git("status", "--porcelain", *PUSH_FILES)
    if rc != 0:
        print(f"[git] status failed: {out}")
        return
    if not out:
        print("[git] no data changes to push.")
        return

    rc, out = _git("add", *PUSH_FILES)
    if rc != 0:
        print(f"[git] add failed: {out}")
        return

    from datetime import datetime
    msg = f"data: scraper run {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    rc, out = _git("commit", "-m", msg)
    if rc != 0:
        # Exit 1 with "nothing to commit" is benign, not an error.
        print(f"[git] commit skipped: {out.splitlines()[-1] if out else rc}")
        return

    # Someone may have pushed from elsewhere since the last run; rebase first
    # so the push isn't rejected as non-fast-forward.
    rc, out = _git("pull", "--rebase", "--autostash", "origin", "main")
    if rc != 0:
        print(f"[git] pull --rebase failed, not pushing: {out}")
        _git("rebase", "--abort")
        return

    rc, out = _git("push", "origin", "main")
    if rc != 0:
        print(f"[git] push failed (commit is saved locally): {out}")
        return

    print(f"[git] pushed: {msg}")