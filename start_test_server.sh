#!/bin/bash
cd /home/miniluv/.picoclaw/workspace/reviewboard
export TESTING=true
export FLASK_ENV=testing
.venv/bin/gunicorn -w 1 -b 0.0.0.0:5001 \
  --access-logfile /tmp/test_access.log \
  --error-logfile /tmp/test_error.log \
  --pid /tmp/test_reviewboard.pid \
  'app:create_app({"TESTING": True})'
