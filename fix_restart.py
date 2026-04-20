#!/usr/bin/env python3
import os
import signal
import subprocess

# Find all gunicorn processes for reviewboard
result = subprocess.run(['pgrep', '-f', 'reviewboard.*gunicorn'], capture_output=True, text=True)
pids = [int(p) for p in result.stdout.strip().split('\n') if p]

print(f"Found PIDs: {pids}")

for pid in pids:
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to {pid}")
    except ProcessLookupError:
        print(f"PID {pid} not found")

# Wait a moment for graceful shutdown
import time
time.sleep(2)

# Start fresh
os.chdir('/home/miniluv/.picoclaw/workspace/reviewboard')
os.system('./manage.sh start')
print("Server restarted")
