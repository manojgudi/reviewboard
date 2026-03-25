#!/bin/bash
cd /home/miniluv/.picoclaw/workspace/reviewboard
export OLLAMA_ENDPOINT=http://10.51.5.169:11434/v1/chat/completions
export REDIS_URL=redis://localhost:6379/0
nohup .venv/bin/rq worker ai-review-queue --url redis://localhost:6379 > logs/rq_worker.log 2>&1 &
echo "Worker started with PID: $!"
