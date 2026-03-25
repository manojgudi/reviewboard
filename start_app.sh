#!/bin/bash

# Generate a SECRET_KEY if not set
if [ -z "$SECRET_KEY" ]; then
    # Generate a random 64-character hex string
    export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
    echo "Generated new SECRET_KEY"
fi

# Enable debug mode for auto-reload on code changes
export FLASK_DEBUG=true

source .venv/bin/activate
nohup python app.py &
echo "ReviewBoard started on http://0.0.0.0:8090"
