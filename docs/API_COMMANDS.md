# AI Reviewboard - Development Commands

## Quick Reference

### 1. Reset AI Review for a Ticket

```bash
# Reset only (clear stuck jobs)
cd /home/miniluv/.picoclaw/workspace/reviewboard
python reset_ai_review.py <ticket_id>

# Reset AND restart (fresh AI review)
python reset_ai_review.py <ticket_id> --retry

# Show status only
python reset_ai_review.py <ticket_id> --status
```

### 2. Find & Fix Stuck Jobs

```bash
# List all stuck jobs (processing > 10 min)
python reset_ai_review.py --list-stuck

# Fix all stuck jobs
python reset_ai_review.py --fix-stuck
```

---

## API Testing with cURL

Base URL: `http://localhost:8090`

### Authentication
First, get a session cookie (assuming admin/admin123):

```bash
# Login and save cookie
curl -c cookies.txt -X POST http://localhost:8090/auth/login \
  -d "username=admin&password=admin123"
```

### AI Review Endpoints

#### Start AI Review
```bash
curl -b cookies.txt -X POST http://localhost:8090/api/ai-review/<ticket_id> \
  -H "Content-Type: application/json"
```

#### Check Status
```bash
curl -b cookies.txt http://localhost:8090/api/ai-review/<ticket_id>/status
```

#### Get Results
```bash
curl -b cookies.txt http://localhost:8090/api/ai-review/<ticket_id>/results
```

#### Cancel Review
```bash
curl -b cookies.txt -X DELETE http://localhost:8090/api/ai-review/<ticket_id>
```

#### Get AI Config (Admin)
```bash
curl -b cookies.txt http://localhost:8090/api/ai-review/config
```

### Ticket Management

#### List Tickets
```bash
curl -b cookies.txt http://localhost:8090/api/tickets
```

#### Get Ticket Details
```bash
curl -b cookies.txt http://localhost:8090/tickets/<ticket_id>
```

#### Update Ticket Status
```bash
# Set to "open"
curl -b cookies.txt -X POST http://localhost:8090/tickets/<ticket_id>/status \
  -d "status=open"

# Set to "in_review"
curl -b cookies.txt -X POST http://localhost:8090/tickets/<ticket_id>/status \
  -d "status=in_review"

# Set to "closed"
curl -b cookies.txt -X POST http://localhost:8090/tickets/<ticket_id>/status \
  -d "status=closed"
```

---

## Server Management

```bash
cd /home/miniluv/.picoclaw/workspace/reviewboard

# Check status
./manage.sh status

# View logs
./manage.sh logs-recent 50

# Restart
./manage.sh restart

# Deploy latest code
./manage.sh deploy
```

---

## Database Operations

```bash
cd /home/miniluv/.picoclaw/workspace/reviewboard

# Open SQLite shell
sqlite3 reviewboard.db

# Common queries:
SELECT * FROM tickets;
SELECT * FROM ai_review_jobs ORDER BY created_at DESC LIMIT 10;
SELECT * FROM users;
```

---

## Redis/RQ Operations

```bash
# Check Redis
redis-cli

# List queued jobs
redis-cli LLEN rq:queue:default

# Clear RQ queue (if needed)
redis-cli FLUSHDB
```

---

## Testing Flow

1. **Find a ticket** with a PDF attached:
   ```bash
   sqlite3 reviewboard.db "SELECT id, title, status, pdf_filename FROM tickets WHERE pdf_filename IS NOT NULL;"
   ```

2. **Check current AI review state**:
   ```bash
   python reset_ai_review.py <ticket_id> --status
   ```

3. **Reset if needed**:
   ```bash
   python reset_ai_review.py <ticket_id> --retry
   ```

4. **Monitor logs**:
   ```bash
   tail -f /home/miniluv/.picoclaw/workspace/reviewboard/logs/gunicorn_error.log
   ```

5. **Check job status via API**:
   ```bash
   curl -b cookies.txt http://localhost:8090/api/ai-review/<ticket_id>/status
   ```

---

## Common Issues

### Job Stuck in "processing"
```bash
python reset_ai_review.py <ticket_id> --retry
```

### All jobs stuck
```bash
python reset_ai_review.py --fix-stuck
```

### Ollama not responding
```bash
# Check if Ollama is running
curl http://10.51.5.169:11434/api/tags

# Check endpoint config
curl -b cookies.txt http://localhost:8090/api/ai-review/config
```

### PDF not processing
- Check PDF exists: `ls -la uploads/`
- Check logs: `./manage.sh logs-recent`
- Verify PDF has extractable text (not scanned images)
