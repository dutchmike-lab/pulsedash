# Launching Dashboards

Two dashboards exist, both using the same codebase (`marketing-dashboard.html` + `server.py`).
Which brands appear is controlled by the `ACTIVE_BRANDS` env var.

---

## Dashboard 1 — Remodeling Concepts (Online)

**URL:** `http://187.77.219.112:5050`  
**Server:** Ubuntu VPS at `187.77.219.112`  
**Code lives at:** `~/pulsedash/` on the server  
**Brands:** RC only (`ACTIVE_BRANDS=rc` set in `~/pulsedash/.env`)

### How it works

The server runs as a background process (`nohup`) that:
- Serves the dashboard HTML at port 5050
- Keeps cached data files in `~/pulsedash/.tmp/` (one per date range: `data_7d.json`, `data_30d.json`, `data_90d.json`)
- Auto-refreshes all data every hour by calling the APIs again
- When you switch 7d / 30d / 90d in the browser, it just swaps which cached file it reads — no API call needed

### Check if it's running

```bash
ssh root@187.77.219.112
pgrep -a -f "server.py"
```

If you see a line like `23154 ... venv/bin/python server.py`, it's running.  
If nothing prints, the server is down and needs to be started.

### Start the server (first time or after a reboot)

```bash
ssh root@187.77.219.112
cd ~/pulsedash
nohup venv/bin/python server.py > server.log 2>&1 &
```

- `nohup` keeps it running after you close the SSH session
- `> server.log 2>&1` sends all output (errors, refresh logs) to `server.log`
- The `&` runs it in the background so you get your terminal back

### Restart the server

```bash
ssh root@187.77.219.112
kill $(pgrep -f "server.py")
sleep 2
cd ~/pulsedash
nohup venv/bin/python server.py > server.log 2>&1 &
```

You need to restart whenever you deploy code changes — Python loads the code once at startup and doesn't pick up file changes while running.

### View server logs (errors, refresh activity)

```bash
ssh root@187.77.219.112
tail -f ~/pulsedash/server.log
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
| Is it running? | `pgrep -a -f "server.py"` |
| View logs | `tail -f ~/pulsedash/server.log` |
| Restart server | `kill $(pgrep -f "server.py") && sleep 2 && cd ~/pulsedash && nohup venv/bin/python server.py > server.log 2>&1 &` |
| Force data refresh | `curl -X POST http://localhost:5050/api/refresh` |
| Deploy code | `git pull` → restart → refresh |
| Start RNR locally (Mac) | `ACTIVE_BRANDS=rnr python server.py --port 5052` |
