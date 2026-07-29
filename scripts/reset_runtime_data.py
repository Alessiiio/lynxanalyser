#!/usr/bin/env python3
"""Reset operational firm/case/scan data to a virgin state. Keeps users."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Prefer env / .env via config when available
try:
    sys.path.insert(0, str(ROOT))
    import config as _config

    DB_PATH = Path(_config.DATABASE_PATH)
    if not DB_PATH.is_absolute():
        DB_PATH = (ROOT / DB_PATH).resolve()
except Exception:
    DB_PATH = ROOT / "fraud_checks.db"

# Case / firm / scan tables — users and schema stay.
WIPE_TABLES = (
    "case_bank_check_items",
    "case_journal_entries",
    "company_cases",
    "person_company_links",
    "person_watch_scans",
    "watched_person_status_history",
    "watched_persons",
    "network_alerts",
    "check_details",
    "scan_history",
    "analyst_feedback",
)

DIR_WIPES = (
    ROOT / "case_reports",
    ROOT / "compliance_reports",
    ROOT / "data" / "shab_month_cache",
)


def wipe_db() -> None:
    if not DB_PATH.exists():
        print(f"No DB at {DB_PATH} — skip")
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = OFF")
        for table in WIPE_TABLES:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cur.fetchone():
                print(f"  skip missing table {table}")
                continue
            cur.execute(f"DELETE FROM {table}")
            print(f"  cleared {table} ({cur.rowcount} rows)")
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'"
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE app_settings SET value=? WHERE key=?",
                ('{"v": false}', "anonymize_mode"),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                    ("anonymize_mode", '{"v": false}'),
                )
            print("  anonymize_mode → false")
        conn.commit()
    finally:
        conn.close()


def wipe_dirs() -> None:
    for path in DIR_WIPES:
        path.mkdir(parents=True, exist_ok=True)
        n = 0
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_file():
                child.unlink()
                n += 1
            elif child.is_dir():
                shutil.rmtree(child)
                n += 1
        print(f"  emptied {path.relative_to(ROOT)} ({n} items)")


def wipe_lists() -> None:
    gold = ROOT / "data" / "goldlist.txt"
    gold.parent.mkdir(parents=True, exist_ok=True)
    gold.write_text("# Trusted domains (one per line)\n", encoding="utf-8")
    print("  goldlist.txt reset")
    block = ROOT / "data" / "blocklist.json"
    block.write_text("{}\n", encoding="utf-8")
    print("  blocklist.json reset")


def main() -> int:
    print(f"Resetting runtime data under {ROOT}")
    print(f"Database: {DB_PATH}")
    wipe_db()
    wipe_dirs()
    wipe_lists()
    print("Done. Users preserved; firm/case/scan data cleared.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
