"""
migrate_norm_actions.py — adds action columns to norm_records table.
Run once. Safe to run again (checks if columns exist first).
"""
import sys
sys.path.insert(0, '.')
from database.db import engine

with engine.connect() as conn:
    # Check existing columns
    result = conn.execute(
        __import__('sqlalchemy').text("PRAGMA table_info(norm_records)")
    )
    existing = {row[1] for row in result}
    print(f"Existing columns: {existing}")

    added = []
    if "action_verb" not in existing:
        conn.execute(__import__('sqlalchemy').text(
            "ALTER TABLE norm_records ADD COLUMN action_verb VARCHAR(64)"
        ))
        added.append("action_verb")

    if "action_frequency_days" not in existing:
        conn.execute(__import__('sqlalchemy').text(
            "ALTER TABLE norm_records ADD COLUMN action_frequency_days INTEGER DEFAULT 2"
        ))
        added.append("action_frequency_days")

    if "last_executed_day" not in existing:
        conn.execute(__import__('sqlalchemy').text(
            "ALTER TABLE norm_records ADD COLUMN last_executed_day INTEGER"
        ))
        added.append("last_executed_day")

    if "is_actionable" not in existing:
        conn.execute(__import__('sqlalchemy').text(
            "ALTER TABLE norm_records ADD COLUMN is_actionable BOOLEAN DEFAULT 0"
        ))
        added.append("is_actionable")

    conn.commit()
    if added:
        print(f"Added columns: {added}")
    else:
        print("All columns already exist — nothing to do")

print("Migration complete")
