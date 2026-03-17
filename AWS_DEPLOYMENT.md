# Step-by-Step AWS Deployment Guide — EduCare Bots

Complete guide to deploy EduCare Bots on an AWS EC2 instance with SQLite. Includes every file you need to create.

---

## Prerequisites

- AWS account
- Domain name (optional; you can use EC2 public IP)
- Google Cloud Console project with OAuth credentials
- Your `.env` values (ANTHROPIC_API_KEY, JWT_SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)

---

## Step 1: Launch EC2 Instance

1. **AWS Console** → EC2 → Launch Instance
2. **Name:** `educare-bots`
3. **AMI:** Ubuntu Server 22.04 LTS
4. **Instance type:** `t3.small` or larger (2 vCPU, 2 GB RAM minimum)
5. **Key pair:** Create new or use existing (download `.pem` file)
6. **Network settings:**
   - Create security group: `educare-sg`
   - Allow **SSH (22)** from your IP
   - Allow **HTTP (80)** from `0.0.0.0/0`
   - Allow **HTTPS (443)** from `0.0.0.0/0`
   - Allow **8000** from `0.0.0.0/0` (or only if using Nginx, skip 8000)
7. **Storage:** 20 GB gp3 (default EBS root — data persists)
8. Launch instance and note the **Public IP**

---

## Step 2: Connect to EC2

```bash
# From your local machine (replace with your key and IP)
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

---

## Step 3: Install Dependencies on Server

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11 and pip
sudo apt install -y python3.11 python3.11-venv python3-pip git

# Verify
python3.11 --version
```

---

## Step 4: Create App Directory and Clone Repo

```bash
# Create app directory
sudo mkdir -p /opt/educare
sudo chown ubuntu:ubuntu /opt/educare
cd /opt/educare

# Clone your repo (replace with your repo URL)
git clone https://github.com/YOUR_USERNAME/educare_bots.git .
# OR: upload files via scp/rsync from your local machine
```

**If you don't use Git**, upload from your machine:

```bash
# From your LOCAL machine
scp -i your-key.pem -r C:\Users\vansh\OneDrive\Desktop\educare_bots\* ubuntu@YOUR_EC2_IP:/opt/educare/
```

---

## Step 5: Create Data Directory (SQLite + uploads)

```bash
sudo mkdir -p /opt/educare/data
sudo chown ubuntu:ubuntu /opt/educare/data
```

---

## Step 6: Create Python Virtual Environment

```bash
cd /opt/educare
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 7: Create `.env` File

Create `.env` from the example and edit:

```bash
cp /opt/educare/deploy/.env.example /opt/educare/.env
nano /opt/educare/.env
```

Replace placeholders with your real values:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-your-anthropic-key
JWT_SECRET_KEY=your-strong-random-secret-at-least-32-chars
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Database — use persistent path
DB_PATH=/opt/educare/data/educare.db

# Redirect URIs — replace YOUR_DOMAIN with your EC2 IP or domain
# Example: https://ec2-xx-xx-xx-xx.compute-1.amazonaws.com
# Or: https://yourdomain.com
GOOGLE_REDIRECT_URI=https://YOUR_DOMAIN/auth/gmail/callback
GOOGLE_SHEETS_REDIRECT_URI=https://YOUR_DOMAIN/auth/sheets/callback
GOOGLE_DOCS_REDIRECT_URI=https://YOUR_DOMAIN/auth/docs/callback
GOOGLE_DRIVE_REDIRECT_URI=https://YOUR_DOMAIN/auth/drive/callback
GOOGLE_CALENDAR_REDIRECT_URI=https://YOUR_DOMAIN/auth/calendar/callback
GOOGLE_MEET_REDIRECT_URI=https://YOUR_DOMAIN/auth/meet/callback
GOOGLE_SLIDES_REDIRECT_URI=https://YOUR_DOMAIN/auth/slides/callback
GOOGLE_FORMS_REDIRECT_URI=https://YOUR_DOMAIN/auth/forms/callback
GOOGLE_CLASSROOM_REDIRECT_URI=https://YOUR_DOMAIN/auth/classroom/callback

# Optional
ACCESS_TOKEN_EXPIRE_MINUTES=60
# MISTRAL_API_KEY=your-mistral-key
```

Save: `Ctrl+O`, `Enter`, `Ctrl+X`.

**Important:** Add these redirect URIs in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → your OAuth client → Authorized redirect URIs.

---

## Step 8: Create Systemd Service File

Copy from the `deploy/` folder in the repo, or create manually:

```bash
sudo cp /opt/educare/deploy/educare.service /etc/systemd/system/educare.service
```

Or create manually with `sudo nano /etc/systemd/system/educare.service` and paste:

```ini
[Unit]
Description=EduCare Bots FastAPI Application
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/educare
Environment="PATH=/opt/educare/.venv/bin"
ExecStart=/opt/educare/.venv/bin/python run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable educare
sudo systemctl start educare
sudo systemctl status educare
```

---

## Step 9: Install and Configure Nginx (Reverse Proxy)

```bash
sudo apt install -y nginx
```

Copy from deploy folder and edit, or create manually:

```bash
sudo cp /opt/educare/deploy/educare.nginx /etc/nginx/sites-available/educare
sudo nano /etc/nginx/sites-available/educare
# Replace YOUR_DOMAIN_OR_IP with your EC2 public IP or domain
```

Or create manually and paste (replace `YOUR_DOMAIN_OR_IP`):

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN_OR_IP;

    client_max_body_size 25M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        proxy_buffering off;
    }
}
```

Enable and restart:

```bash
sudo ln -sf /etc/nginx/sites-available/educare /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

---

## Step 10: Test the Deployment

```bash
# Check service
sudo systemctl status educare

# Check logs
sudo journalctl -u educare -f

# Test API (from server or your machine)
curl http://localhost:8000/docs
# Or: curl http://YOUR_EC2_IP/docs
```

---

## Step 11: (Optional) HTTPS with Let's Encrypt

If you have a domain pointing to your EC2 IP:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```

Update `.env` redirect URIs to `https://yourdomain.com/...` and add those URIs in Google Cloud Console.

---

## Files You Create — Summary

| File | Source | Destination | Purpose |
|------|--------|--------------|---------|
| `.env` | `deploy/.env.example` | `/opt/educare/.env` | Secrets and config |
| `educare.service` | `deploy/educare.service` | `/etc/systemd/system/educare.service` | Run app as service |
| `educare.nginx` | `deploy/educare.nginx` | `/etc/nginx/sites-available/educare` | Reverse proxy |

---

## Quick Reference Commands

```bash
# Start/stop/restart app
sudo systemctl start educare
sudo systemctl stop educare
sudo systemctl restart educare

# View logs
sudo journalctl -u educare -f

# After code changes
cd /opt/educare && git pull
sudo systemctl restart educare
```

---

## Google Cloud Console — Redirect URIs

Add these in **APIs & Services → Credentials → OAuth 2.0 Client IDs → your client → Authorized redirect URIs**:

```
https://YOUR_EC2_IP/auth/gmail/callback
https://YOUR_EC2_IP/auth/sheets/callback
https://YOUR_EC2_IP/auth/docs/callback
https://YOUR_EC2_IP/auth/drive/callback
https://YOUR_EC2_IP/auth/calendar/callback
https://YOUR_EC2_IP/auth/meet/callback
https://YOUR_EC2_IP/auth/slides/callback
https://YOUR_EC2_IP/auth/forms/callback
https://YOUR_EC2_IP/auth/classroom/callback
```

Replace `YOUR_EC2_IP` with your actual IP or domain (e.g. `ec2-1-2-3-4.compute-1.amazonaws.com`).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError` | `source .venv/bin/activate` and `pip install -r requirements.txt` |
| OAuth "Invalid code verifier" | Don't restart server between connect and callback; ensure single instance |
| 502 Bad Gateway | Check `sudo systemctl status educare` and `journalctl -u educare` |
| DB permission denied | `sudo chown -R ubuntu:ubuntu /opt/educare/data` |
| Port 8000 not responding | Check security group allows 80 (or 8000 if not using Nginx) |
