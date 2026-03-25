# gunicorn.conf.py - ReviewBoard Production Configuration

import multiprocessing
import os

# Server socket
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8090")
backlog = 2048

# Worker processes
workers = int(os.getenv("GUNICORN_WORKERS", 2))
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50
timeout = 120
keepalive = 5

# Logging
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "reviewboard"

# Server mechanics
daemon = True  # Fork to background
pidfile = "gunicorn.pid"
umask = 0o007
user = None
group = None
tmp_upload_dir = None

# SSL (handled by reverse proxy like nginx)
keyfile = None
certfile = None

# Preload app for memory efficiency and zero-downtime restarts
preload_app = True

# Graceful timeout (time to finish existing requests during restart)
graceful_timeout = 30

def on_starting(server):
    """Called just before the master process is initialized."""
    print(f"Starting ReviewBoard server...")

def on_reload(server):
    """Called to recycle workers during a reload/restart."""
    print(f"Reloading workers...")

def when_ready(server):
    """Called just after the server is started."""
    print(f"ReviewBoard ready at http://{bind}")

def on_exit(server):
    """Called just before exiting Gunicorn."""
    print(f"Shutting down ReviewBoard...")

def worker_int(worker):
    """Called when a worker receives SIGINT/SIGTERM."""
    print(f"Worker {worker.pid} interrupted")

def worker_abort(worker):
    """Called when a worker receives SIGABRT."""
    print(f"Worker {worker.pid} aborted")
