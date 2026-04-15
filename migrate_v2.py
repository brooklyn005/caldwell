"""
migrate_v2.py — v2 architecture migration.

Adds new tables and columns needed for the scene-driven architecture.
Safe to run multiple times — checks existence before altering.

Run this BEFORE starting the simulation after updating the code.
"""
import sys
sys.path.insert(0, ".")

from sqlalchemy import text
from database.db import engine, init_db

print("Caldwell v2 Migration")
print("=" * 40)

# First ensure all ORM tables exist (creates Scene table etc.)
init_db()
print("✓ ORM tables initialized (new tables created if missing)")

with engine.connect() as conn:

    def col_exists(table, col):
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == col for r in rows)

    def table_exists(table):
        row = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=:t"
        ), {"t": table}).fetchone()
        return row is not None

    added = []

    # ── norm_records — action + stage columns ────────────────────────────────
    if table_exists("norm_records"):
        for col, defn in [
            ("action_verb",            "VARCHAR(64)"),
            ("action_frequency_days",  "INTEGER DEFAULT 2"),
            ("last_executed_day",      "INTEGER"),
            ("is_actionable",          "BOOLEAN DEFAULT 0"),
            ("stage",                  "VARCHAR(32) DEFAULT 'proposed'"),
            ("beneficiary_ids_json",   "TEXT DEFAULT '[]'"),
            ("resenter_ids_json",      "TEXT DEFAULT '[]'"),
            ("enforcer_ids_json",      "TEXT DEFAULT '[]'"),
            ("physical_signal",        "TEXT"),
        ]:
            if not col_exists("norm_records", col):
                conn.execute(text(f"ALTER TABLE norm_records ADD COLUMN {col} {defn}"))
                added.append(f"norm_records.{col}")

    # ── characters — departure tracking ──────────────────────────────────────
    if table_exists("characters"):
        for col, defn in [
            ("left_community",   "BOOLEAN DEFAULT 0"),
            ("departure_day",    "INTEGER"),
            ("departure_reason", "TEXT"),
        ]:
            if not col_exists("characters", col):
                conn.execute(text(f"ALTER TABLE characters ADD COLUMN {col} {defn}"))
                added.append(f"characters.{col}")

    # ── scenes — content_category column ─────────────────────────────────────
    if table_exists("scenes"):
        if not col_exists("scenes", "content_category"):
            conn.execute(text(
                "ALTER TABLE scenes ADD COLUMN content_category VARCHAR(32)"
            ))
            added.append("scenes.content_category")

    # ── open_questions — add new columns if table already exists ────────────
    if table_exists("open_questions"):
        for col, defn in [
            ("attempts",              "INTEGER DEFAULT 0"),
            ("dropped",               "BOOLEAN DEFAULT 0"),
            ("current_understanding", "TEXT"),
            ("intermediary_count",    "INTEGER DEFAULT 0"),
        ]:
            if not col_exists("open_questions", col):
                conn.execute(text(f"ALTER TABLE open_questions ADD COLUMN {col} {defn}"))
                added.append(f"open_questions.{col}")
    else:
        print("  Note: open_questions table not found — re-run init_db")

    conn.commit()

if added:
    print(f"✓ Added columns: {', '.join(added)}")
else:
    print("✓ All columns already exist")

print("\nMigration complete. You're ready to run.")
