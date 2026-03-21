# Deployment Guide

This guide covers deploying Review Board to production.

## Prerequisites

- Linux server (Ubuntu 20.04+ recommended)
- Python 3.10+
- Nginx
- SSL certificate (Let's Encrypt recommended)
- System dependencies: `poppler-utils`

## Step 1: Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install system dependencies
sudo apt install -y python3-venv poppler-utils nginx certbot python3-certbot-nginx

# Create application user
sudo useradd -m -s /bin/bash reviewboard
sudo mkdir -p /opt/reviewboard
sudo chown reviewboard:reviewboard /opt/reviewboard
```

## Step 2: Application Installation

```bash
# Switch to app user
sudo su - reviewboard

# Clone repository
cd /opt/reviewboard
git clone <your-repo-url> .

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create static uploads directory
mkdir -p static/uploads
chmod 755 static/uploads
```

## Step 3: Environment Configuration

```bash
# Create environment file
cat > .env << 'EOF'
SECRET_KEY=your-64-character-hex-secret-key-here
FLASK_DEBUG=false
EOF

chmod 600 .env
```

Generate a SECRET_KEY:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Step 4: Systemd Service

```bash
# Create systemd service
sudo cat > /etc/systemd/system/reviewboard.service << 'EOF'
[Unit]
Description=Review Board Web Service
After=network.target

[Service]
Type=notify
User=reviewboard
Group=reviewboard
WorkingDirectory=/opt/reviewboard
Environment="PATH=/opt/reviewboard/.venv/bin"
EnvironmentFile=/opt/reviewboard/.env
ExecStart=/opt/reviewboard/.venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:8090 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    'app:create_app()'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable reviewboard
sudo systemctl start reviewboard
```

## Step 5: Nginx Configuration

```bash
# Copy nginx config
sudo cp docs/nginx.conf.example /etc/nginx/sites-available/reviewboard

# Edit with your domain
sudo nano /etc/nginx/sites-available/reviewboard

# Enable site
sudo ln -s /etc/nginx/sites-available/reviewboard /etc/nginx/sites-enabled/

# Test and reload
sudo nginx -t
sudo systemctl reload nginx
```

## Step 6: SSL Certificate

```bash
# Obtain Let's Encrypt certificate
sudo certbot --nginx -d reviewboard.example.com

# Auto-renewal is enabled by default
# Verify with: sudo certbot renew --dry-run
```

## Step 7: Firewall Configuration

```bash
# Enable firewall
sudo ufw allow 22/tcp   # SSH
sudo ufw allow 443/tcp  # HTTPS
sudo ufw allow 80/tcp   # HTTP (for certbot)
sudo ufw enable
```

## Step 8: Initial Setup

1. Visit https://reviewboard.example.com
2. Create admin user via direct database access or admin panel
3. Configure users and settings

## Maintenance

### Viewing Logs

```bash
# Application logs (journald)
sudo journalctl -u reviewboard -f

# Nginx access logs
sudo tail -f /var/log/nginx/reviewboard_access.log

# Security logs
tail -f /opt/reviewboard/security.log
```

### Database Backups

```bash
# Create backup script
cat > /opt/reviewboard/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/reviewboard"
DATE=$(date +%Y%m%d-%H%M%S)

mkdir -p "$BACKUP_DIR"

# Backup database
cp /opt/reviewboard/reviewboard.db "$BACKUP_DIR/reviewboard-$DATE.db"

# Backup uploads
tar -czf "$BACKUP_DIR/uploads-$DATE.tar.gz" -C /opt/reviewboard static/uploads

# Keep last 30 days
find "$BACKUP_DIR" -name "reviewboard-*.db" -mtime +30 -delete
find "$BACKUP_DIR" -name "uploads-*.tar.gz" -mtime +30 -delete
EOF

chmod +x /opt/reviewboard/backup.sh

# Add to crontab
(crontab -l 2>/dev/null; echo "0 2 * * * /opt/reviewboard/backup.sh") | crontab -
```

### Updates

```bash
# Pull latest code
cd /opt/reviewboard
git pull

# Update dependencies
source .venv/bin/activate
pip install -r requirements.txt

# Restart service
sudo systemctl restart reviewboard
```

## Troubleshooting

### Service won't start

```bash
# Check status
sudo systemctl status reviewboard

# View logs
sudo journalctl -u reviewboard -n 50

# Test gunicorn directly
source .venv/bin/activate
cd /opt/reviewboard
gunicorn 'app:create_app()' --bind 127.0.0.1:8090
```

### 502 Bad Gateway

- Check if gunicorn is running: `sudo systemctl status reviewboard`
- Check nginx error logs: `sudo tail -f /var/log/nginx/reviewboard_error.log`
- Verify firewall allows connections: `sudo ufw status`

### Database locked

```bash
# Remove lock file if stale
rm /opt/reviewboard/reviewboard.db-journal
sudo systemctl restart reviewboard
```

### PDF uploads fail

- Verify poppler-utils installed: `pdftoppm -v`
- Check uploads directory permissions: `ls -la /opt/reviewboard/static/uploads`
- Check disk space: `df -h`

## Security Checklist

- [ ] SECRET_KEY is strong and unique
- [ ] HTTPS configured with valid certificate
- [ ] Firewall enabled with only ports 22, 80, 443 open
- [ ] Database backups configured
- [ ] Log monitoring set up
- [ ] Regular security updates enabled
- [ ] No debug mode in production
