#!/usr/bin/env python3
"""
Reviewboard Incremental Backup Script
======================================
Backs up reviewboard DB + manuscripts incrementally using Python's
os.link() for hard-links (same as rsync --link-dest).

Run twice daily via cron: 6am and 6pm
Schedule:
    0 6 * * * /home/miniluv/.picoclaw/workspace/reviewboard/backup.sh >> /encrypted/backups/reviewboard/backup.log 2>&1
    0 18 * * * /home/miniluv/.picoclaw/workspace/reviewboard/backup.sh >> /encrypted/backups/reviewboard/backup.log 2>&1
"""

import os
import sys
import io
import shutil
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime

# === CONFIG ===
BACKUP_ROOT = Path("/encrypted/backups/reviewboard")
REVIEWBOARD_DIR = Path("/home/miniluv/.picoclaw/workspace/reviewboard")
RETENTION_DAYS = 30
LOG_FILE = BACKUP_ROOT / "backup.log"

# Files/dirs to back up (relative to REVIEWBOARD_DIR)
# DB files
DB_FILES = ["reviewboard.db", "app.db"]

# Everything else — preserve tree under $BACKUP_DIR/
# We back up the whole reviewboard tree except exclusions below.
INCLUDE_SUBDIRS = ["static", "routes", "templates", "models.py",
                   "app.py", "requirements.txt", "gunicorn.conf.py",
                   "start_app.sh", "manage.sh", "services"]

# Exclude patterns (filenames / dirnames)
EXCLUDE_NAMES = {
    "__pycache__", ".pytest_cache", ".venv", ".git",
    ".pyc", ".pyo", ".pyd", ".db-journal",
    "test_reviewboard.db", "test_ai_review.py",
    "setup_test_db.py", "reset_ai_review.py",
    "check_db.py", "nohup.out", "gunicorn.pid",
    ".env.local", ".env.production",
}

# === HELPERS ===

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_latest_backup():
    """Return the most recent backup directory, or None."""
    if not BACKUP_ROOT.is_dir():
        return None
    candidates = [
        d for d in BACKUP_ROOT.iterdir()
        if d.is_dir() and d.name.startswith("20")
           and d.name[4] == "-" and d.name[7] == "-" and d.name[10] == "_"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime)


def prune_old_backups(cutoff_ts):
    """Remove backup dirs older than cutoff_ts (Unix timestamp)."""
    pruned = 0
    for d in BACKUP_ROOT.iterdir():
        if not (d.is_dir() and d.name.startswith("20") and d.name[4] == "-" and d.name[7] == "-" and d.name[10] == "_"):
            continue
        if d.stat().st_mtime < cutoff_ts:
            log(f"PRUNE: Removing old backup: {d.name}")
            shutil.rmtree(d)
            pruned += 1
    if pruned:
        log(f"PRUNE: Removed {pruned} old backup(s).")
    else:
        log("PRUNE: No old backups to remove.")


def sqlite_checkpoint(db_path):
    """
    Run PRAGMA wal_checkpoint(TRUNCATE) on the SQLite DB to push all WAL
    changes into the main db file, then close.  This ensures the .db file
    is a consistent snapshot without requiring downtime.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.execute("PRAGMA optimize;")
        conn.close()
        log(f"  SQLite checkpoint OK: {db_path.name}")
    except Exception as e:
        log(f"  SQLite checkpoint WARN ({db_path.name}): {e}")


def should_exclude(rel_path: str) -> bool:
    """Return True if this path matches an exclude pattern."""
    parts = rel_path.replace("\\", "/").split("/")
    return any(p in EXCLUDE_NAMES or p.endswith(".pyc") for p in parts)


def copy_file(src: Path, dst: Path, preserve_mtime: bool = True):
    """Copy a single file, creating parent dirs as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if not preserve_mtime:
        os.utime(dst, None)


def incremental_sync(src_root: Path, dst_root: Path, prev_root: Path = None):
    """
    Walk src_root and copy all files to dst_root.
    If prev_root is given and a file exists there with the same inode/mtime,
    hard-link it instead of copying (saves space).
    """
    files_linked = 0
    files_copied = 0
    dirs_seen = set()

    for dirpath, dirnames, filenames in os.walk(src_root):
        dirpath = Path(dirpath)
        rel_dir = dirpath.relative_to(src_root)

        # Filter out excluded directories in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if not should_exclude(str(rel_dir / d))]

        for filename in filenames:
            if should_exclude(filename):
                continue

            src_file = dirpath / filename
            rel_file = src_file.relative_to(src_root)
            dst_file = dst_root / rel_file

            # Ensure destination dir exists
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            dirs_seen.add(dst_file.parent)

            try:
                src_stat = src_file.stat()

                # Check if file is unchanged vs previous backup
                if prev_root is not None:
                    prev_file = prev_root / rel_file
                    if prev_file.exists():
                        try:
                            prev_stat = prev_file.stat()
                            # Same inode (already hard-linked) or same mtime+size → link
                            if (
                                prev_stat.st_ino == src_stat.st_ino
                                or (
                                    prev_stat.st_mtime == src_stat.st_mtime
                                    and prev_stat.st_size == src_stat.st_size
                                )
                            ):
                                # Unlink dst if it exists (might be stale)
                                if dst_file.exists():
                                    dst_file.unlink()
                                os.link(prev_file, dst_file)
                                files_linked += 1
                                continue
                        except OSError:
                            pass  # Fall through to copy

                # Copy the file
                shutil.copy2(src_file, dst_file)
                files_copied += 1

            except Exception as e:
                log(f"  WARN: Could not process {rel_file}: {e}")

    return files_linked, files_copied


# === MAIN ===

def main():
    log("=" * 50)
    log("BACKUP STARTED")

    # 1. Ensure backup root exists
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

    # 2. SQLite checkpoint — flush WAL to main db file
    log("SQLite checkpoint...")
    for db_name in DB_FILES:
        db_path = REVIEWBOARD_DIR / db_name
        if db_path.is_file():
            sqlite_checkpoint(db_path)
        else:
            log(f"  SKIP (not found): {db_name}")

    # 3. Determine new backup dir and previous backup
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp
    prev_backup = get_latest_backup()

    log(f"New backup dir : {backup_dir}")
    if prev_backup:
        log(f"Previous backup  : {prev_backup} (--link-dest)")
    else:
        log("Previous backup  : None (full backup)")

    backup_dir.mkdir(parents=True, exist_ok=True)

    # 4. Incremental sync
    log("Starting incremental sync...")
    linked, copied = incremental_sync(REVIEWBOARD_DIR, backup_dir, prev_backup)
    log(f"  Files hard-linked : {linked}")
    log(f"  Files copied      : {copied}")

    # 5. Prune old backups
    cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
    log(f"Pruning backups older than {RETENTION_DAYS} days...")
    prune_old_backups(cutoff)

    # 6. Summary
    total_size = sum(f.stat().st_size for f in backup_dir.rglob("*") if f.is_file())
    log(f"Backup complete: {backup_dir}")
    log(f"Total backup size: {total_size / (1024*1024):.1f} MB")
    log("ALL DONE")
    log("=" * 50)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL ERROR: {e}")
        sys.exit(1)
