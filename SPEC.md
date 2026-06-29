# zm — Personal Dashboard Spec

**Live URL:** `zuyu.feifei.food`
**Files:** `/home/tommy/zuyu/`
**Stack:** FastAPI (Python) + SQLite + Vanilla JS
**Port:** 4000

---

## Cloudflare Setup

Uses the **same tunnel token** as `tm.feifei.food`.
You need to add one entry in Cloudflare Zero Trust dashboard:

1. Go to **Zero Trust → Networks → Tunnels**
2. Find the tunnel used by `tm.feifei.food`
3. Click **Edit → Public Hostnames → Add a hostname**
4. Set: `zm.feifei.food` → `http://localhost:4000`

---

## Deployment

```bash
cd /home/tommy/zuyu

# Build
docker build -t zm-app .

# Run (includes wiki volume mount)
docker run -d \
  --name zm-app-server \
  --restart unless-stopped \
  --network host \
  -v zm-data:/data \
  -v /home/tommy/zuyu/wiki:/wiki \
  zm-app

# Restart after changes
docker restart zm-app-server

# Rebuild after code changes
docker build -t zm-app . && docker stop zm-app-server && docker rm zm-app-server && docker run -d --name zm-app-server --restart unless-stopped --network host -v zm-data:/data -v /home/tommy/zuyu/wiki:/wiki zm-app
```

Data (SQLite) persists in Docker volume `zm-data` — survives container restarts and rebuilds.

---

## File Structure

```
zm-app/
  main.py         ← FastAPI backend + all API routes
  requirements.txt
  Dockerfile
  static/
    index.html    ← Entire frontend (HTML + CSS + JS)
  specs/
    calendar.md   ← Calendar tab spec
  SPEC.md         ← This file
```

---

## Design System

| Variable | Value | Usage |
|---|---|---|
| `--bg` | `#07070d` | Page background |
| `--surface` | `#0f0f18` | Sidebar, modals |
| `--surface2` | `#171724` | Cards, inputs |
| `--surface3` | `#1f1f30` | Hover states |
| `--accent` | `#635bff` | Active states, buttons |
| `--green` | `#00c98d` | Run events |
| `--orange` | `#ff8c42` | Tempo events |
| `--blue` | `#3b9eff` | Long run events |
| `--red` | `#ff4d6a` | Delete, danger |
| `--yellow` | `#f5c842` | General events |
| `--text` | `#ededf5` | Primary text |
| `--text-sec` | `#7878a0` | Secondary text |
| Font | Inter | All text |

---

## Adding a New Tab

1. Add a nav item in the sidebar (copy existing `.nav-item`)
2. Add a bottom nav item (copy existing `.bottom-nav-item`)
3. Add a `<section class="tab-section" id="tab-NAME">` in main
4. Remove the `<span class="nav-badge">soon</span>` when building it out
5. Create `specs/NAME.md` for its spec

---

## Current Tabs

| Tab | Status | Spec |
|---|---|---|
| Calendar | ✅ Live | `specs/calendar.md` |
| Wiki | ✅ Live | `specs/wiki.md` |
| Goals | 🔜 Soon | — |
| Focus | 🔜 Soon | — |
| Tasks | 🔜 Soon | — |
| Tracker | 🔜 Soon | — |

---

## Database

SQLite at `/data/zm.db` (Docker volume `zm-data`).

Current tables:
- `events` — calendar events (see `specs/calendar.md`)

To inspect: `docker run --rm -v zm-data:/data python:3.11-slim sqlite3 /data/zm.db ".tables"`
