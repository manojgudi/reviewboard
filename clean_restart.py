#!/usr/bin/env python3
import os
import signal
import subprocess

# Kill existing gunicorn processes
result = subprocess.run(['pgrep', '-f', 'reviewboard.*gunicorn'], capture_output=True, text=True)
pids = [int(p) for p in result.stdout.strip().split('\n') if p]

print(f"Killing PIDs: {pids}")
for pid in pids:
    try:
        os.kill(pid, signal.SIGKILL)
        print(f"Killed {pid}")
    except:
        pass

# Clear bytecode cache
import glob
base = '/home/miniluv/.picoclaw/workspace/reviewboard'
for pattern in ['routes/__pycache__/*.pyc', '**/__pycache__/*.pyc']:
    for f in glob.glob(os.path.join(base, pattern)):
        os.remove(f)
        print(f"Removed {f}")

# Restart
import time
time.sleep(1)
os.chdir(base)
os.system('./manage.sh start')
print("Done")
