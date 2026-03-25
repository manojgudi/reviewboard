#!/bin/bash
# Stop old worker and restart
pkill -f "rq worker" 2>/dev/null || true
sleep 1
bash /home/miniluv/.picoclaw/workspace/reviewboard/start_worker.sh
