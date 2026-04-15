# Launching Dashboards

Two dashboards exist, both using the same codebase (`marketing-dashboard.html` + `server.py`).
Which brands appear is controlled by the `ACTIVE_BRANDS` env var.

---

## Dashboard 1 — Remodeling Concepts (Online)

**Server:** `187.77.219.112:5050`  
**Brands:** RC only (`ACTIVE_BRANDS=rc` in the server's `.env`)

### Start / Restart
```bash
ssh root@187.77.219.112
cd ~/pulsedash
kill $(pgrep -f "server.py")
sleep 2
nohup venv/bin/python server.py > server.log 2>&1 &
```

### Deploy code updates
```bash
ssh root@187.77.219.112
cd ~/pulsedash
git pull
kill $(pgrep -f "server.py")
sleep 2
nohup venv/bin/python server.py > server.log 2>&1 &
curl -X POST http://localhost:5050/api/refresh   # force data refresh
```

### Trigger data refresh (without restart)
```bash
ssh root@187.77.219.112
curl -X POST http://localhost:5050/api/refresh
```
Data auto-refreshes every hour in the background.

---

## Dashboard 2 — Ryann Reed (Local)

**Server:** `localhost:5052`  
**Brands:** RNR only

### Start
```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rnr python server.py --port 5052
```

Open: `http://localhost:5052`

### Run in background
```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rnr nohup python server.py --port 5052 > .tmp/server-rnr.log 2>&1 &
```

---

## Local Dev (both brands, port 5051)

```bash
cd "/Users/dutchmike/Desktop/Claude Agents/Company wide dashboard"
source venv/bin/activate
ACTIVE_BRANDS=rc,rnr python server.py --port 5051
```

---

## Pull data manually

```bash
source venv/bin/activate

# RC only (30 days, default)
python tools/pull_all.py --brands rc

# RNR only
python tools/pull_all.py --brands rnr

# Specific date range
python tools/pull_all.py --brands rc --start 2026-01-01 --end 2026-03-31

# Output to a custom file
python tools/pull_all.py --brands rc --output .tmp/data_rc_custom.json
```

---

## Environment Variables

| Variable | Where | Value |
|----------|-------|-------|
| `ACTIVE_BRANDS` | `~/pulsedash/.env` on server | `rc` |
| `ACTIVE_BRANDS` | local `.env` | `rc,rnr` |

---

## Checklist after code changes

1. `git add` + `git commit` + `git push` locally
2. On server: `git pull`
3. Restart server (see above)
4. Trigger refresh: `curl -X POST http://localhost:5050/api/refresh`
