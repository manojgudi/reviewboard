#!/bin/bash
# Wrapper for the Python backup script
# (Keeps cron calling convention simple; actual logic is in backup.py)

cd /home/miniluv/.picoclaw/workspace/reviewboard
exec python3 backup.py
