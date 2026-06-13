# mmss-mcp

MMSS Yojna — Maternal Care MCP Server for UP Government Hackathon.

FastMCP HTTP server on port 8889 that powers a WhatsApp-based pregnancy registration and maternal care platform, plus an offline-capable ASHA/ANM supervisor dashboard. The LLM orchestration layer (Matrix/Echo/dev-console) is unchanged — this repo is exclusively the tool + API server.

---

## Architecture

```
WhatsApp (Interakt) → scribesnap medassist bridge
    → Matrix/Echo agent runtime (dev-console config)
    → mmss-mcp  ← this repo
    → SQLite DB

Browser (ASHA/ANM) → http://localhost:8889/dashboard
    → GET /api/patients, /api/escalations, ...
    → SQLite DB
```

---

## Quick Start

```bash
# 1. Clone and set up environment
python3 -m venv .venv
source .venv/bin/activate
pip install -e "."

# 2. Configure
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY and INTERAKT_API_KEY

# 3. Seed demo data
python seed_data.py

# 4. Start server
mmss-mcp-server
# or: python -m mmss_mcp.server
```

Server starts on `http://0.0.0.0:8889`.

- **MCP endpoint**: `http://localhost:8889/mcp`
- **ASHA Dashboard**: `http://localhost:8889/dashboard`

---

## ASHA/ANM Dashboard

A progressive web app (PWA) for ASHA and ANM field workers, served directly from the MCP server.

**Features**
- Patient list with RED/YELLOW/GREEN risk badges, ANC due indicators, escalation alerts
- Inbox view — one thread per registered mother
- Bulk WhatsApp reminder sender (ANC due, vaccine, iron tablet, custom)
- Risk override — ASHA can escalate/downgrade a patient's risk flag with a reason
- ABHA verification flow (mock — calls `/api/patients/{id}/verify`)
- CSV export for block/district reporting (`/api/patients/export.csv`)

**PWA / Offline**

ASHA workers in rural UP often have poor connectivity. The dashboard works offline:

- Service worker (`/static/sw.js`) caches the shell on first load
- API responses are cached; stale data served automatically when offline
- Hindi offline banner shown when serving from cache: _"📶 ऑफ़लाइन — पुराना डेटा दिख रहा है"_
- Installable on Android via the Web App Manifest (`/static/manifest.json`)

To install on Android: open `http://<server>/dashboard` in Chrome → three-dot menu → **Add to Home screen**.

---

## Endpoints

### Infrastructure

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp` | POST | MCP tool endpoint (streamable HTTP) |
| `/health` | GET | Returns `OK` |
| `/status` | GET | JSON stats: patients registered, HRP cases open, escalations pending |
| `/dashboard` | GET | ASHA/ANM dashboard HTML (PWA) |
| `/static/*` | GET | Static assets (manifest.json, sw.js) |

### Dashboard API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stats` | GET | Header counts: total patients, RED/YELLOW counts, pending escalations, ANC due this week |
| `/api/patients` | GET | All active patients with risk, ANC progress, escalation flag |
| `/api/patients/export.csv` | GET | CSV export for CMO/block reporting |
| `/api/patients/{reg_id}` | GET | Full patient detail: clinical data, open HRP case, ANC schedule |
| `/api/patients/{reg_id}/risk` | POST | ASHA risk override `{ level, reason }` — creates HRP case if RED/YELLOW |
| `/api/patients/{reg_id}/verify` | POST | ABHA verification `{ type, value }` — mock; plug in NHA API for production |
| `/api/escalations` | GET | All pending escalations with patient and case info |
| `/api/escalations/{id}/ack` | POST | Acknowledge an escalation |
| `/api/visit-requests` | GET | Pending ASHA home visit requests |
| `/api/reminders` | POST | Send bulk WhatsApp reminders `{ text, recipients }` via notification_service |

---

## MCP Tools

### Registration & Status
| Tool | Description |
|------|-------------|
| `register_pregnancy` | Register new patient, auto-compute EDD, auto-link ASHA worker via village lookup |
| `get_patient_status` | Full status: risk level, ANC visits, open HRP cases, escalations |

### Clinical
| Tool | Description |
|------|-------------|
| `classify_hrp_risk` | RED/YELLOW/GREEN classification from clinical inputs per MMSS Yojna criteria |
| `extract_from_photo` | Claude Vision reads lab report image → extracts test values |

### ANC
| Tool | Description |
|------|-------------|
| `schedule_anc_visits` | Create 9 ANC visit schedule from LMP date |
| `log_anc_visit` | Mark visit done, re-run risk classification |

### Escalation
| Tool | Description |
|------|-------------|
| `trigger_escalation` | Fire MMSS SOP escalation chain (RED/YELLOW/GREEN paths) |
| `acknowledge_escalation` | Close escalation loop when ANM/ASHA responds |
| `resolve_hrp_case` | Mark HRP case resolved — stops further escalations |
| `request_asha_visit` | Log non-urgent home visit request, notify ASHA via WhatsApp |
| `verify_asha` | Check if a WhatsApp number belongs to a registered ASHA/ANM worker |

### Location
| Tool | Description |
|------|-------------|
| `get_nearby_facilities` | Return PHC/CHC/hospitals near a lat/lng coordinate |

### Day 4 (Planned)
| Tool | Description |
|------|-------------|
| `register_birth` | Post-delivery record |
| `schedule_neonatal_followup` | Vaccination reminder schedule (BCG, DPT, OPV...) |
| `report_maternal_death` | MDSR — case ID, geo-tag, alert CMO + War Room |
| `get_war_room_summary` | District-level aggregated stats |

---

## HRP Risk Classification

Rule-based per MMSS Yojna criteria (CEO slide 5, April 2026):

- **RED**: BP ≥ 140, Hb < 7 g/dL, age < 16 or > 40, weight < 40 or > 75 kg, GDM, HIV, active TB, hypothyroid, heart disease, epilepsy, BOH, multiple pregnancy, APH, IUD in pregnancy, and more
- **YELLOW**: Age 16–18, Hb 7–9 g/dL, parity ≥ 4 (grand multipara), prev LSCS, height < 145 cm, IUGR, hydramnios ≥ 28 weeks, IVF, post-dated > 42 weeks, and more
- **GREEN**: Normal findings, mild anaemia ≥ 9 g/dL

---

## Escalation SOP

Per MMSS protocol (set `DEMO_MODE=true` to compress delays to 2 minutes for demo):

| Risk | Escalation Chain |
|------|-----------------|
| RED | ASHA → 24hr → Call Centre → 24hr → PHC MO → 72hr → CMO → 72hr → NHM+STC |
| YELLOW | ASHA → 48hr → PHC MO |
| GREEN | ASHA → 7 days → PHC MO |

---

## Database

SQLite (`./mmss.db` by default, override via `DB_PATH` env var).

Tables: `patients`, `clinical_data`, `anc_visits`, `hrp_cases`, `escalations`, `visit_requests`, `births`, `neonatal_followups`, `asha_assignments`

---

## Seed Data

`seed_data.py` inserts:
- 10 ASHA assignments across Lucknow, Varanasi, and Sitapur districts
- 1 demo patient: Sunita Kumari (MMSS-LUC-DEMO01), YELLOW risk, Rampur/Lucknow

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required for `extract_from_photo` (Claude Vision) |
| `INTERAKT_API_KEY` | — | Required for outbound WhatsApp alerts |
| `INTERAKT_API_URL` | `https://api.interakt.ai/v1/public/message/` | Interakt API base URL |
| `CALL_CENTRE_PHONE` | — | Fallback phone when ASHA phone not on record |
| `DEMO_MODE` | `true` | Compress escalation delays to 2 minutes |
| `DB_PATH` | `./mmss.db` | SQLite file path |
| `MCP_PORT` | `8889` | HTTP server port |

---

## Dev-Console Agent Config

```json
{
  "name": "MMSS Maternal Care Bot",
  "prompt_config": {"name": "mmss-maternal-care-v1"},
  "primary_llm_config": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "temperature": 0.3,
    "max_iterations": 10
  },
  "mcp_configs": [{
    "transport": "streamable_http",
    "url": "http://localhost:8889/mcp"
  }],
  "allowed": ["text", "audio", "file"],
  "idle_session_expiry": 3600
}
```

---

## Interakt Webhook

Configure Interakt to POST to:
```
https://<scribesnap-host>/wh/interakt?agent_id=<MMSS_AGENT_ID>
```

Two pre-approved templates required in Interakt account:
- `hrp_alert` — alert ANM/ASHA about high-risk patient
- `anc_reminder` — remind patient about upcoming ANC visit
- `visit_request` — notify ASHA of a pending home visit request

---

## File Structure

```
mmss-mcp/
├── mmss_mcp/
│   ├── server.py              # FastMCP server, static mount, /dashboard route
│   ├── api/
│   │   └── dashboard_routes.py  # REST API for ASHA dashboard (10 endpoints)
│   ├── tools/
│   │   ├── registration_tools.py
│   │   ├── hrp_tools.py
│   │   ├── anc_tools.py
│   │   ├── escalation_tools.py  # includes verify_asha
│   │   └── location_tools.py
│   ├── services/
│   │   ├── escalation_engine.py  # APScheduler SOP chain
│   │   └── notification_service.py  # Interakt WhatsApp sender
│   └── db/
│       └── database.py          # SQLite schema + context manager
├── static/
│   ├── dashboard.html           # ASHA/ANM PWA dashboard
│   ├── manifest.json            # Web App Manifest (installable)
│   └── sw.js                    # Service worker (offline-first)
└── seed_data.py
```
