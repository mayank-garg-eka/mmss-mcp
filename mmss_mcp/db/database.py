import sqlite3
import os
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./mmss.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS asha_assignments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                village     TEXT NOT NULL,
                block       TEXT NOT NULL,
                district    TEXT NOT NULL,
                asha_name   TEXT NOT NULL,
                asha_phone  TEXT NOT NULL,
                UNIQUE(village, block, district)
            );

            CREATE TABLE IF NOT EXISTS patients (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                reg_id          TEXT NOT NULL UNIQUE,
                name            TEXT NOT NULL,
                age             INTEGER NOT NULL,
                phone           TEXT,
                lmp_date        TEXT NOT NULL,
                edd             TEXT NOT NULL,
                village         TEXT NOT NULL,
                block           TEXT NOT NULL,
                district        TEXT NOT NULL,
                asha_name       TEXT,
                asha_phone      TEXT,
                parity          INTEGER DEFAULT 0,
                risk_level      TEXT DEFAULT 'GREEN',
                risk_reasons    TEXT DEFAULT '[]',
                status          TEXT DEFAULT 'active',
                registered_by   TEXT DEFAULT 'self',
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS clinical_data (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id  INTEGER NOT NULL REFERENCES patients(id),
                bp_systolic INTEGER,
                bp_diastolic INTEGER,
                hb          REAL,
                weight_kg   REAL,
                height_cm   REAL,
                gdm         INTEGER DEFAULT 0,
                hiv         INTEGER DEFAULT 0,
                tb_active   INTEGER DEFAULT 0,
                hypothyroid INTEGER DEFAULT 0,
                heart_disease INTEGER DEFAULT 0,
                epilepsy    INTEGER DEFAULT 0,
                bad_obs_history INTEGER DEFAULT 0,
                prev_lscs   INTEGER DEFAULT 0,
                recorded_at TEXT DEFAULT (datetime('now')),
                source      TEXT DEFAULT 'conversation'
            );

            CREATE TABLE IF NOT EXISTS anc_visits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      INTEGER NOT NULL REFERENCES patients(id),
                visit_number    INTEGER NOT NULL,
                scheduled_date  TEXT NOT NULL,
                completed_at    TEXT,
                notes           TEXT,
                UNIQUE(patient_id, visit_number)
            );

            CREATE TABLE IF NOT EXISTS hrp_cases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id  INTEGER NOT NULL REFERENCES patients(id),
                priority    TEXT NOT NULL CHECK(priority IN ('RED','YELLOW','GREEN')),
                reasons     TEXT NOT NULL DEFAULT '[]',
                status      TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
                created_at  TEXT DEFAULT (datetime('now')),
                resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS escalations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                hrp_case_id     INTEGER NOT NULL REFERENCES hrp_cases(id),
                level           TEXT NOT NULL CHECK(level IN ('ASHA','CALL_CENTRE','PHC_MO','CMO','NHM_STC')),
                notified_phone  TEXT NOT NULL,
                notified_at     TEXT DEFAULT (datetime('now')),
                acknowledged_at TEXT,
                status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','acknowledged'))
            );

            CREATE TABLE IF NOT EXISTS visit_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      INTEGER NOT NULL REFERENCES patients(id),
                reason          TEXT,
                asha_notified   INTEGER DEFAULT 0,
                requested_at    TEXT DEFAULT (datetime('now')),
                status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','completed'))
            );

            CREATE TABLE IF NOT EXISTS births (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id      INTEGER NOT NULL REFERENCES patients(id),
                birth_date      TEXT NOT NULL,
                weight_kg       REAL,
                delivery_type   TEXT,
                facility        TEXT,
                complications   TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS neonatal_followups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                birth_id        INTEGER NOT NULL REFERENCES births(id),
                followup_type   TEXT NOT NULL,
                vaccine_name    TEXT,
                scheduled_date  TEXT NOT NULL,
                completed_at    TEXT
            );
        """)
    print(f"DB initialised at {DB_PATH}")
