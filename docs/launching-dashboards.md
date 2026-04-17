# Launching Dashboards

Two dashboards exist, both using the same codebase (`marketing-dashboard.html` + `server.py`).
Which brands appear is controlled by the `ACTIVE_BRANDS` env var.

---

## Dashboard 1 — Remodeling Concepts (Online)

**URL:** `https://dashboard.remodelingconcepts.net`  
**Direct IP (no domain needed):** `http://187.77.219.112`  
**Server:** Hostinger VPS at `187.77.219.112`  
**Code lives at:** `~/pulsedash/` on the server  
**Brands:** RC only (`ACTIVE_BRANDS=rc` set in `~/pulsedash/.env`)

### How it works

The server runs as a **systemd service** (auto-starts on boot, restarts on crash):
- Flask app (`server.py`) runs on port 5050
- nginx sits in front on port 80/443, proxying to port 5050
- Cloudflare proxies `dashboard.remodelingconcepts.net` → server IP
- Cached data files in `~/pulsedash/.tmp/` (one per date range)
- Auto-refreshes all data every hour

### Check if it's running

```bash
ssh root@187.77.219.112
systemctl status pulsedash
systemctl status nginx
```

Both should show `active (running)`.

### Restart the server

```bash
ssh root@187.77.219.112
systemctl restart pulsedash
```

You need to restart whenever you deploy code changes.

### View server logs (errors, refresh activity)

```bash
ssh root@187.77.219.112
journalctl -u pulsedash -f
```

Press `Ctrl+C` to stop watching. Useful for debugging or confirming a refresh ran.

### Trigger a data refresh manually

```bash
ssh root@187.77.219.112
curl -X POST http://localhost:5050/api/refresh
```

This pulls fresh data from all APIs for all 3 date ranges (7d, 30d, 90d). Takes 1–2 minutes.  
The server auto-does this every hour — only use this if you need data right now.

### Check what data is cached

```bash
ssh root@187.77.219.112
ls -lh ~/pulsedash/.tmp/
```

Shows the cached data files and when they were last updated.

---

### Deploy code changes (the full workflow)

Run this on your **Mac** first (after making and testing changes locally):

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
git add <changed files>
git commit -m "description of change"
git push origin master
```

Then SSH to the server and deploy:

```bash
ssh root@187.77.219.112
cd ~/pulsedash
git pull                                          # pull the new code
kill $(pgrep -f "server.py")                      # stop old server
sleep 2
nohup venv/bin/python server.py > server.log 2>&1 &  # start new server
sleep 3
curl -X POST http://localhost:5050/api/refresh    # refresh data with new code
```

If you added a new Python package locally, also run:
```bash
venv/bin/pip install <package-name>
```

---

### The `.env` file on the server

The server's `.env` is **not synced via git** (it's gitignored for security). It lives only on the server at `~/pulsedash/.env`. To view or edit it:

```bash
ssh root@187.77.219.112
cat ~/pulsedash/.env        # view
nano ~/pulsedash/.env       # edit
```

Key lines that must be present:
```
ACTIVE_BRANDS=rc
JOBTREAD_GRANT_KEY=...
GHL_API_KEY_RC=...
CONSTANT_CONTACT_ACCESS_TOKEN=...
ANTHROPIC_API_KEY=...
```

If the server was freshly set up, you'll need to copy `.env` from your Mac to the server:
```bash
scp "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard/.env" root@187.77.219.112:~/pulsedash/.env
```
Then edit the copy on the server to set `ACTIVE_BRANDS=rc`.

---

## Dashboard 2 — Ryann Reed (Local)

**URL:** `http://localhost:5052`  
**Runs on:** your Mac only  
**Brands:** RNR only

### Start

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rnr python server.py --port 5052
```

Open `http://localhost:5052` in your browser. Press `Ctrl+C` in the terminal to stop it.

### Run in background (so you can close the terminal)

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rnr nohup python server.py --port 5052 > .tmp/server-rnr.log 2>&1 &
```

To stop it later: `kill $(pgrep -f "server.py.*5052")`

---

## Local Dev (both brands, port 5051)

Use this when you want to see both RC and RNR together while developing:

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rc,rnr python server.py --port 5051
```

---

## Pull data manually (without the server)

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate

# RC only (30 days, saves to .tmp/data.json)
python tools/pull_all.py --brands rc

# RNR only
python tools/pull_all.py --brands rnr

# Specific date range
python tools/pull_all.py --brands rc --start 2026-01-01 --end 2026-03-31

# Save to a custom file
python tools/pull_all.py --brands rc --output .tmp/my-snapshot.json
```

---

## Quick reference

| Task | Command (run on server unless noted) |
|------|--------------------------------------|
| Is it running? | `systemctl status pulsedash` |
| View logs | `journalctl -u pulsedash -f` |
| Restart server | `systemctl restart pulsedash` |
| Force data refresh | `curl -X POST http://localhost:5050/api/refresh` |
| Deploy code | `git pull` → `systemctl restart pulsedash` → refresh |
| Start RNR locally (Mac) | `ACTIVE_BRANDS=rnr python server.py --port 5052` |
| nginx status | `systemctl status nginx` |
| nginx reload (after config change) | `nginx -t && systemctl reload nginx` |
