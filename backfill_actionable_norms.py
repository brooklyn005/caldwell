"""
backfill_actionable_norms.py — scans historical dialogues for actionable norms
and marks them in the database. Also creates new actionable NormRecords from
dialogue history that the original backfill may have missed.

Run after migrate_norm_actions.py.
"""
import sys
import json
import re
sys.path.insert(0, '.')

from database.db import SessionLocal, engine
from database.models import Dialogue, NormRecord
from sqlalchemy import text as sa_text

ACTIONABLE_PATTERNS = {
    "hunt": [
        r"we (should|will|can|need to) hunt",
        r"someone (should|needs to|has to) hunt",
        r"hunting (rule|agreement|rotation|schedule|duty|party)",
        r"take turns hunting",
        r"hunting group",
        r"go out (and )?hunt",
    ],
    "fish": [
        r"we (should|will|can) fish",
        r"someone (should|needs to) fish",
        r"fishing (rule|rotation|duty|schedule)",
        r"go (out )?fishing",
    ],
    "cook": [
        r"someone (should|needs to|has to|will) cook",
        r"we (should|will|take turns) cook",
        r"cooking (duty|rotation|rule|schedule|responsibility)",
        r"take turns (with )?cook",
        r"cook for (everyone|the group|people|us all)",
    ],
    "forage": [
        r"we (should|will|can) forage",
        r"foraging (party|group|rotation|duty|schedule)",
        r"go (out )?forag",
    ],
    "gather": [
        r"we (should|will) gather",
        r"someone (should|needs to) gather",
        r"gather(ing)? (and )?distribut",
    ],
    "build": [
        r"we (should|will|can|need to) build",
        r"someone (should|needs to) build",
        r"building (rule|schedule|rotation|duty|project)",
        r"build (shelter|space|structure|something permanent)",
    ],
    "repair": [
        r"someone (should|needs to) fix",
        r"keep (things|it) (working|repaired|maintained)",
        r"maintenance (rule|schedule|duty)",
    ],
    "patrol": [
        r"we (should|will) patrol",
        r"someone (should|needs to) patrol",
        r"(walk|check) the (perimeter|boundary|edges|outskirts)",
        r"keep watch",
    ],
    "teach": [
        r"we (should|will) teach",
        r"someone (should|needs to) teach",
        r"pass(ing)? (on|down) (knowledge|skills)",
        r"share (knowledge|skills|what we know)",
    ],
    "tend": [
        r"someone (should|needs to) tend",
        r"take care of (the|things|sick|injured)",
    ],
}

ACTIONABLE_RES = {
    verb: re.compile("|".join(patterns), re.IGNORECASE)
    for verb, patterns in ACTIONABLE_PATTERNS.items()
}


def detect_verb(text):
    for verb, pattern in ACTIONABLE_RES.items():
        if pattern.search(text):
            return verb
    return None


def backfill(db):
    print("Scanning historical dialogues for actionable norms...\n")

    dialogues = (
        db.query(Dialogue)
        .order_by(Dialogue.sim_day.asc())
        .all()
    )

    # Collect all actionable instances by verb and day
    found = {}  # verb -> [(sim_day, description)]
    for d in dialogues:
        exchanges = json.loads(d.dialogue_json or "[]")
        for ex in exchanges:
            txt = ex.get("text", "")
            if not txt:
                continue
            verb = detect_verb(txt)
            if verb:
                # Extract relevant sentence
                sentences = re.split(r'[.!?]', txt)
                for s in sentences:
                    if ACTIONABLE_RES[verb].search(s) and len(s.strip()) > 15:
                        if verb not in found:
                            found[verb] = []
                        found[verb].append((d.sim_day, s.strip()[:200]))
                        break

    print(f"Found actionable norm language for: {list(found.keys())}\n")

    created = 0
    updated = 0
    for verb, instances in found.items():
        if not instances:
            continue

        # Find the earliest instance — that's when the norm emerged
        earliest_day, earliest_desc = min(instances, key=lambda x: x[0])
        # Best description — longest
        _, best_desc = max(instances, key=lambda x: len(x[1]))

        count = len(instances)
        strength = min(0.9, 0.15 + count * 0.03)

        print(f"  [{verb}] emerged Day {earliest_day}, "
              f"appeared {count}x, strength={strength:.2f}")
        print(f"    Best desc: {best_desc[:70]}...")

        # Check if we already have a norm record for this action verb
        existing = db.execute(sa_text(
            "SELECT id FROM norm_records WHERE action_verb=:verb AND is_actionable=1"
        ), {"verb": verb}).fetchone()

        if existing:
            db.execute(sa_text(
                "UPDATE norm_records SET strength=:s, reinforced_count=:c "
                "WHERE id=:id"
            ), {"s": strength, "c": count, "id": existing[0]})
            updated += 1
            print(f"    → Updated existing norm record")
        else:
            db.execute(sa_text(
                "INSERT INTO norm_records "
                "(norm_type, description, emerged_day, strength, violated_count, "
                "reinforced_count, is_active, is_actionable, action_verb, "
                "action_frequency_days) "
                "VALUES ('action_" + verb + "', :desc, :day, :s, 0, :c, 1, 1, :verb, 2)"
            ), {
                "desc": best_desc,
                "day": earliest_day,
                "s": strength,
                "c": count,
                "verb": verb,
            })
            created += 1
            print(f"    → Created new actionable norm record")

    db.commit()
    print(f"\nDone. Created {created} new actionable norms, updated {updated}.")

    # Show final state
    print("\nAll active actionable norms:")
    rows = db.execute(sa_text(
        "SELECT action_verb, emerged_day, strength, reinforced_count, description "
        "FROM norm_records WHERE is_actionable=1 AND is_active=1 "
        "ORDER BY strength DESC"
    )).fetchall()
    for row in rows:
        verb, day, s, count, desc = row
        print(f"  [{verb}] Day {day} strength={s:.2f} (x{count}) {desc[:60]}...")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        backfill(db)
    finally:
        db.close()
