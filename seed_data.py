"""Seed demo data: 10 ASHA assignments + 1 demo patient (Yellow risk)."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from mmss_mcp.db.database import init_db, db

ASHA_ASSIGNMENTS = [
    # Lucknow district
    ("Rampur",      "Malihabad",  "Lucknow",  "Meena Devi",    "+919876543201"),
    ("Chandpur",    "Malihabad",  "Lucknow",  "Sunita Yadav",  "+919876543202"),
    ("Bakshi Ka Talab", "BKT",   "Lucknow",  "Rani Sharma",   "+919876543203"),
    ("Gosainganj",  "Gosainganj", "Lucknow",  "Kavita Singh",  "+919876543204"),
    ("Mohanlalganj","Mohanlalganj","Lucknow", "Poonam Verma",  "+919876543205"),
    # Varanasi district
    ("Chiraigaon",  "Chiraigaon", "Varanasi", "Savita Maurya", "+919876543206"),
    ("Arajiline",   "Arajiline",  "Varanasi", "Usha Patel",    "+919876543207"),
    ("Harahua",     "Harahua",    "Varanasi", "Geeta Rai",     "+919876543208"),
    # Sitapur district
    ("Pahadganj",   "Reusa",      "Sitapur",  "Durga Chauhan", "+919876543209"),
    ("Biswan",      "Biswan",     "Sitapur",  "Anita Tiwari",  "+919876543210"),
]

DEMO_PATIENT = {
    "reg_id":       "MMSS-LUC-DEMO01",
    "name":         "Sunita Kumari",
    "age":          22,
    "phone":        "+919999999999",
    "lmp_date":     "2026-01-15",
    "edd":          "2026-10-22",
    "village":      "Rampur",
    "block":        "Malihabad",
    "district":     "Lucknow",
    "asha_name":    "Meena Devi",
    "asha_phone":   "+919876543201",
    "parity":       1,
    "risk_level":   "YELLOW",
    "risk_reasons": '["Moderate Anaemia (Hb 7.8 g/dL)"]',
    "registered_by":"self",
}


def seed():
    init_db()
    with db() as conn:
        for village, block, district, asha_name, asha_phone in ASHA_ASSIGNMENTS:
            conn.execute(
                """INSERT OR IGNORE INTO asha_assignments
                   (village, block, district, asha_name, asha_phone)
                   VALUES (?,?,?,?,?)""",
                (village, block, district, asha_name, asha_phone),
            )

        conn.execute(
            """INSERT OR IGNORE INTO patients
               (reg_id, name, age, phone, lmp_date, edd, village, block, district,
                asha_name, asha_phone, parity, risk_level, risk_reasons, registered_by)
               VALUES (:reg_id,:name,:age,:phone,:lmp_date,:edd,:village,:block,:district,
                       :asha_name,:asha_phone,:parity,:risk_level,:risk_reasons,:registered_by)""",
            DEMO_PATIENT,
        )

        # Seed a YELLOW HRP case for the demo patient
        patient = conn.execute(
            "SELECT id FROM patients WHERE reg_id=?", ("MMSS-LUC-DEMO01",)
        ).fetchone()
        if patient:
            existing = conn.execute(
                "SELECT id FROM hrp_cases WHERE patient_id=? AND status='open'",
                (patient["id"],),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO hrp_cases (patient_id, priority, reasons) VALUES (?,?,?)",
                    (patient["id"], "YELLOW", '["Moderate Anaemia (Hb 7.8 g/dL)"]'),
                )

    print("Seed data inserted:")
    print(f"  {len(ASHA_ASSIGNMENTS)} ASHA assignments")
    print(f"  1 demo patient (MMSS-LUC-DEMO01, Sunita Kumari, YELLOW risk)")


if __name__ == "__main__":
    seed()
