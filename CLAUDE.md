# CLAUDE.md — mmss-mcp

## What This Is

MMSS Yojna maternal care MCP server for the UP Government hackathon. FastMCP 2.14.5, HTTP transport, port 8889, SQLite DB. Zero changes to scribesnap/Matrix/Echo — this repo is only the tool server.

## Architecture

```
WhatsApp (Interakt) → scribesnap medassist bridge (zero changes*)
    → Matrix/Echo agent runtime
    → dev-console: MMSS agent (system prompt "mmss-maternal-care-v1" in Langfuse)
    → mmss-mcp (this repo) on localhost:8889
    → SQLite (mmss.db)
```

*One pending change in scribesnap: `app/bridge/medassist/bridge.py` `_build_payload` needs image/document forwarding added (~line 490).

## Key Patterns

**Tool registration:** Each tool module exports `register_*_tools(mcp: FastMCP)`. Register in `server.py:create_server()`.

**Return format:** Always `{"success": True, "data": {...}}` or `{"success": False, "error": str}`.

**Logging:** `await ctx.info/error(f"[tool_name] message")`. Use `Context = CurrentContext()` for ctx param.

**DB access:** Use `with db() as conn:` context manager (auto-commit/rollback). `conn.row_factory = sqlite3.Row` so columns are accessible by name.

**Tool parameter annotations:** Use `Annotated[type, "description"]` — this is what the LLM sees.

## Current State

| Day | Status | What was built |
|-----|--------|----------------|
| Day 1 | Done | DB schema, HRP classifier, `register_pregnancy`, `get_patient_status`, `classify_hrp_risk`, seed data, server health/status endpoints |
| Day 2 | Next | `extract_from_photo`, `schedule_anc_visits`, `log_anc_visit`, `trigger_escalation`, `acknowledge_escalation` |
| Day 3 | Pending | Escalation engine (APScheduler), notification service (Interakt template API), dev-console agent creation, end-to-end WhatsApp test |
| Day 4–5 | P1 | Birth/neonatal tools, war room dashboard |

## HRP Classifier

`mmss_mcp/services/hrp_classifier.py` — pure rule-based, no LLM.

Input: `ClinicalInput` dataclass (all fields optional). Output: `{"level": "RED"|"YELLOW"|"GREEN", "reasons": [...]}`.

Precedence: RED wins over YELLOW. If any RED flag triggers, return RED immediately (no YELLOW check).

## Escalation SOP (MMSS)

```
RED:    ASHA → 24hr → CALL_CENTRE → 24hr → PHC_MO → 72hr → CMO → 72hr → NHM_STC
YELLOW: ASHA → 48hr → PHC_MO
GREEN:  ASHA → 7d   → PHC_MO
```

`DEMO_MODE=true` compresses all delays to 2 minutes. Check `os.getenv("DEMO_MODE") == "true"`.

## Photo Extraction (Day 2)

Use Anthropic SDK with claude-sonnet-4-6. Pass image URL as `{"type": "image", "source": {"type": "url", "url": media_url}}`. Prompt: extract test name, value, unit from lab report — return JSON. Feed result into `classify_hrp_risk`.

## Outbound WhatsApp Alerts

Interakt requires pre-approved templates for non-user-initiated messages. Call `POST https://api.interakt.ai/v1/public/message/` with `INTERAKT_API_KEY`. Templates: `hrp_alert`, `anc_reminder`.

## Environment

```
ANTHROPIC_API_KEY    — Claude Vision for extract_from_photo
INTERAKT_API_KEY     — outbound WhatsApp alerts
INTERAKT_API_URL     — https://api.interakt.ai/v1/public/message/
DEMO_MODE            — true = 2-min escalation delays
DB_PATH              — ./mmss.db
MCP_PORT             — 8889
```

## Running Locally

```bash
source .venv/bin/activate
python seed_data.py       # once
mmss-mcp-server           # starts on :8889
```

Verify: `curl localhost:8889/health` → `OK`

## Files

```
mmss_mcp/
  server.py                  — FastMCP server, register tool modules here
  db/database.py             — SQLite init + db() context manager
  tools/
    registration_tools.py    — register_pregnancy, get_patient_status, classify_hrp_risk
    hrp_tools.py             — extract_from_photo (Day 2)
    anc_tools.py             — schedule_anc_visits, log_anc_visit (Day 2)
    escalation_tools.py      — trigger_escalation, acknowledge_escalation (Day 2)
    neonatal_tools.py        — register_birth, neonatal followup (Day 4)
  services/
    hrp_classifier.py        — rule-based RED/YELLOW/GREEN classifier
    photo_extractor.py       — Claude Vision lab report reader (Day 2)
    escalation_engine.py     — APScheduler background timers (Day 3)
    notification_service.py  — Interakt template API (Day 3)
seed_data.py                 — 10 ASHA assignments + 1 demo patient (Sunita Kumari, YELLOW)
```

## Seed Data Villages

Lucknow: Rampur/Malihabad, Chandpur/Malihabad, Bakshi Ka Talab/BKT, Gosainganj, Mohanlalganj
Varanasi: Chiraigaon, Arajiline, Harahua
Sitapur: Pahadganj/Reusa, Biswan

Demo patient MMSS-LUC-DEMO01 (Sunita Kumari) is in Rampur/Lucknow → links to ASHA "Meena Devi" (+919876543201).
