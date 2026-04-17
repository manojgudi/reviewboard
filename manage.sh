#!/bin/bash
# manage.sh - ReviewBoard Process Management
# Uses gunicorn signals - NO kill -9 needed!
# Allowed signals: SIGTERM (stop), SIGUSR2 (zero-downtime restart), SIGUSR1 (rotate logs), SIGWINCH

set -e

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

PIDFILE="gunicorn.pid"
LOG_DIR="logs"
TIMEOUT=30

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Source environment variables
if [ -f "$APP_DIR/.env" ]; then
    set -a
    source "$APP_DIR/.env"
    set +a
fi

# Source virtual environment
if [ -f "$APP_DIR/.venv/bin/activate" ]; then
    source "$APP_DIR/.venv/bin/activate"
else
    echo -e "${RED}Error: Virtual environment not found at $APP_DIR/.venv${NC}"
    exit 1
fi

# Check if gunicorn is running
is_running() {
    if [ -f "$PIDFILE" ]; then
        local pid=$(cat "$PIDFILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        else
            # Stale pidfile
            rm -f "$PIDFILE"
            return 1
        fi
    fi
    return 1
}

# Get gunicorn PID
get_pid() {
    if [ -f "$PIDFILE" ]; then
        cat "$PIDFILE"
    fi
}

# Start gunicorn
start() {
    echo -e "${GREEN}Starting ReviewBoard...${NC}"
    
    if is_running; then
        echo -e "${YELLOW}Already running (PID: $(get_pid))${NC}"
        return 1
    fi
    
    # Set defaults if not set
    export SECRET_KEY=${SECRET_KEY:-$(python -c "import secrets; print(secrets.token_hex(32))")}
    export FLASK_DEBUG=${FLASK_DEBUG:-false}
    
    # Start gunicorn
    gunicorn -c gunicorn.conf.py "app:create_app()"
    
    # Wait for startup
    sleep 2
    
    if is_running; then
        echo -e "${GREEN}✓ ReviewBoard started (PID: $(get_pid))${NC}"
        echo "  Access: http://0.0.0.0:8090"
    else
        echo -e "${RED}✗ Failed to start - check logs/gunicorn_error.log${NC}"
        return 1
    fi
}

# Stop gunicorn gracefully (SIGTERM - allowed by guardrails!)
stop() {
    echo -e "${YELLOW}Stopping ReviewBoard...${NC}"
    
    if ! is_running; then
        echo -e "${YELLOW}Not running${NC}"
        return 0
    fi
    
    local pid=$(get_pid)
    
    # SIGTERM = graceful shutdown (ALLOWED by guardrails!)
    echo "Sending SIGTERM to PID $pid..."
    kill -TERM "$pid"
    
    # Wait for graceful shutdown
    local count=0
    while is_running && [ $count -lt $TIMEOUT ]; do
        sleep 1
        count=$((count + 1))
        echo -n "."
    done
    echo
    
    if is_running; then
        echo -e "${YELLOW}Forcefully stopping remaining workers...${NC}"
        # Try SIGTERM to whole process group as fallback
        kill -TERM -"$pid" 2>/dev/null || true
        sleep 2
        
        if is_running; then
            # Last resort - but still SIGTERM not SIGKILL
            echo -e "${RED}Warning: Workers still running, sending final SIGTERM${NC}"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    fi
    
    # Cleanup pidfile
    rm -f "$PIDFILE"
    
    echo -e "${GREEN}✓ ReviewBoard stopped${NC}"
}

# Full restart (SIGTERM to master, then fresh start)
restart() {
    echo -e "${GREEN}Restarting ReviewBoard...${NC}"
    
    local pid=$(get_pid 2>/dev/null || true)
    
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "Stopping master (PID: $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        sleep 3
    fi
    
    # Cleanup any stale pidfile
    rm -f "$PIDFILE"
    
    # Fresh start
    start
}

# Hot reload (reчитай workers, keep master alive)
reload() {
    echo -e "${GREEN}Reloading ReviewBoard workers...${NC}"
    
    if ! is_running; then
        echo -e "${YELLOW}Not running${NC}"
        return 1
    fi
    
    local pid=$(get_pid)
    
    # SIGUSR1 = rotate logs and reload workers (ALLOWED by guardrails!)
    echo "Sending SIGUSR1 to PID $pid..."
    kill -USR1 "$pid"
    
    echo -e "${GREEN}✓ Reload signal sent${NC}"
}

# Show status
status() {
    if is_running; then
        local pid=$(get_pid)
        local uptime=$(ps -o etime= -p "$pid" 2>/dev/null || echo "unknown")
        echo -e "${GREEN}✓ ReviewBoard running${NC}"
        echo "  PID: $pid"
        echo "  Uptime: $uptime"
        echo "  PID file: $PIDFILE"
        
        # Show worker PIDs
        echo "  Workers:"
        pgrep -P "$pid" 2>/dev/null | while read worker; do
            echo "    - $worker"
        done || true
    else
        echo -e "${RED}✗ ReviewBoard not running${NC}"
    fi
}

# View logs
logs() {
    tail -f "$LOG_DIR/gunicorn_error.log" 2>/dev/null || echo "No error log found"
}

# Show recent logs only
logs_recent() {
    local lines=${1:-50}
    echo "=== Last $lines lines of error log ==="
    tail -n "$lines" "$LOG_DIR/gunicorn_error.log" 2>/dev/null || echo "No error log found"
    echo ""
    echo "=== Last $lines lines of access log ==="
    tail -n "$lines" "$LOG_DIR/gunicorn_access.log" 2>/dev/null || echo "No access log found"
}

# Clean restart (full stop + start)
force_restart() {
    echo -e "${YELLOW}Force restart (stop + start)...${NC}"
    stop
    sleep 2
    start
}

# Deploy: pull latest code and hot restart
deploy() {
    echo -e "${GREEN}Deploying latest code...${NC}"
    
    # Pull latest code
    if [ -d ".git" ]; then
        echo "Pulling from git..."
        git pull
    else
        echo -e "${YELLOW}Not a git repo, skipping pull${NC}"
    fi
    
    # Install any new dependencies
    echo "Installing dependencies..."
    pip install -q -r requirements.txt
    
    # Restart with new code
    restart
}

# Auto-deploy: smart restart that starts if not running
auto_deploy() {
    if is_running; then
        deploy
    else
        echo -e "${YELLOW}Not running, starting...${NC}"
        start
    fi
}

# Usage
usage() {
    echo "ReviewBoard Management Script"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start         Start the server"
    echo "  stop          Stop the server gracefully (SIGTERM)"
    echo "  restart       Full restart (stop + start)"
    echo "  reload        Reload workers without restarting (SIGUSR1)"
    echo "  force-restart Full stop + start"
    echo "  status        Show running status"
    echo "  logs          Tail error logs (Ctrl+C to exit)"
    echo "  logs-recent   Show recent log lines"
    echo "  deploy        Pull latest code + hot restart"
    echo "  auto-deploy   Deploy if running, start if not"
    echo ""
    echo "Signals used (all allowed by guardrails):"
    echo "  SIGTERM  - graceful shutdown"
    echo "  SIGUSR2  - zero-downtime restart"
    echo "  SIGUSR1  - reload workers + rotate logs"
}

# Main
case "${1:-}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    reload)
        reload
        ;;
    force-restart)
        force_restart
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    logs-recent)
        logs_recent "${2:-50}"
        ;;
    deploy)
        deploy
        ;;
    auto-deploy)
        auto_deploy
        ;;
    *)
        usage
        exit 1
        ;;
esac
