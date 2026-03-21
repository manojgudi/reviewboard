#!/bin/bash

# Generate a SECRET_KEY if not set
if [ -z "$SECRET_KEY" ]; then
    # Generate a random 64-character hex string
    export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
    echo "Generated new SECRET_KEY"
fi

# Disable debug mode in production (set FLASK_DEBUG=false)
export FLASK_DEBUG=false

source .venv/bin/activate
nohup python app.py &
echo "ReviewBoard started on http://0.0.0.0:8090"
