"""
backfill_norms.py — scans all historical dialogue and populates NormRecord.

Processes 335+ days of conversations looking for agreement language and
emerging community understandings. Run once after installing norm_detector.py.

Takes 2-5 minutes depending on database size.
"""
import sys
import json
import re
import random
sys.path.insert(0, '.')

from database.db import SessionLocal
from database.models import Character, Dialogue, NormRecord

# Agreement patterns — copied from norm_detector to avoid import issues during backfill
AGREEMENT_PATTERNS = [
    r"we (could|should|might|can) agree",
    r"everyone (should|needs to|has to|ought to)",
    r"that('s| is) (the rule|what we do|how it works|understood|settled|decided)",
    r"we('ve| have) decided",
    r"we('ve| have) agreed",
    r"let('s| us) say that",
    r"that('s| is) fair",
    r"that makes sense( for all| to everyone| here)",
    r"no one (should|can|gets to|is allowed to)",
    r"(always|never) (happens|gets|goes|takes|leaves|comes)",
    r"people here (don't|do|always|never|tend to|seem to)",
    r"(that's|it's) (just )?how (it works|things work|we do it) here",
    r"we (all|both) know that",
    r"(understood|agreed|settled|clear)",
]
AGREEMENT_RE = re.compile("|".join(AGREEMENT_PATTERNS), re.IGNORECASE)

NORM_TOPICS = {
    "food": ["food", "eat", "hungry", "market", "share", "hoard", "take", "leave"],
    "space": ["space", "place", "room", "sleep", "territory", "move", "stay"],
    "body": ["touch", "body", "sex", "naked", "bare", "close", "distance"],
    "conflict": ["fight", "argue", "hurt", "harm", "attack", "threaten", "anger"],
    "resource": ["water", "supply", "tool", "use", "keep", "borrow", "belong"],
    "care": ["sick", "hurt", "help", "care", "tend", "support", "leave alone"],
    "decision": ["decide", "choose", "vote", "agree", "discuss", "everyone", "together"],
    "roles": ["good at", "skill", "able", "build", "fix", "tend", "gather", "teach"],
    "norms": ["rule", "norm", "always", "never", "understood", "expected", "what we do"],
    "identity": ["name", "who we are", "belong", "one of us", "part of"],
}


def classify_norm(text: str) -> str:
    text_lower = text.lower()
    scores = {topic: 0 for topic in NORM_TOPICS}
    for topic, keywords in NORM_TOPICS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[topic] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def extract_description(text: str, max_len: int = 220) -> str:
    sentences = re.split(r'[.!?]', text)
    for s in sentences:
        if AGREEMENT_RE.search(s):
            desc = s.strip()
            if len(desc) > 20:
                return desc[:max_len]
    return text[:max_len]


def backfill(db):
    # Load all dialogues ordered by day
    dialogues = (
        db.query(Dialogue)
        .order_by(Dialogue.sim_day.asc(), Dialogue.sim_tick.asc())
        .all()
    )

    print(f"Processing {len(dialogues)} historical conversations...")

    # Track norms found: {norm_type: [(sim_day, description)]}
    found_norms = {}
    total_candidates = 0

    for d in dialogues:
        exchanges = json.loads(d.dialogue_json or "[]")
        sim_day = d.sim_day

        for ex in exchanges:
            text = ex.get("text", "")
            if not text or ex.get("roster_id") == "OPERATOR":
                continue
            if not AGREEMENT_RE.search(text):
                continue

            description = extract_description(text)
            norm_type = classify_norm(text)

            if len(description) < 20:
                continue

            total_candidates += 1

            if norm_type not in found_norms:
                found_norms[norm_type] = []
            found_norms[norm_type].append((sim_day, description))

    print(f"Found {total_candidates} norm candidates across {len(found_norms)} categories")
    print()

    # Now write norms — consolidate by type and time window
    # Keep the strongest/most representative per category, don't flood the table
    written = 0
    for norm_type, instances in found_norms.items():
        # Group into 30-day windows
        windows = {}
        for sim_day, desc in instances:
            window = (sim_day // 30) * 30
            if window not in windows:
                windows[window] = []
            windows[window].append((sim_day, desc))

        for window_start, window_instances in sorted(windows.items()):
            if not window_instances:
                continue

            # Pick the most representative instance — longest description
            best_day, best_desc = max(window_instances, key=lambda x: len(x[1]))
            reinforcement_count = len(window_instances)

            # Strength grows with how many times this norm appeared in the window
            strength = min(1.0, 0.1 + (reinforcement_count * 0.04))

            # Check not already in DB (in case backfill is run twice)
            existing = db.query(NormRecord).filter(
                NormRecord.norm_type == norm_type,
                NormRecord.emerged_day == best_day,
            ).first()

            if existing:
                existing.reinforced_count += reinforcement_count
                existing.strength = min(1.0, existing.strength + 0.05)
            else:
                norm = NormRecord(
                    norm_type=norm_type,
                    description=best_desc,
                    emerged_day=best_day,
                    strength=strength,
                    violated_count=0,
                    reinforced_count=reinforcement_count,
                    is_active=True,
                )
                db.add(norm)
                print(f"  Day {best_day:3d} [{norm_type:10s}] strength={strength:.2f} "
                      f"(x{reinforcement_count}) {best_desc[:60]}...")
                written += 1

    db.commit()
    print(f"\nBackfill complete. {written} norm records written.")

    # Show summary
    print("\nActive norms by strength:")
    norms = db.query(NormRecord).filter(
        NormRecord.is_active == True
    ).order_by(NormRecord.strength.desc()).all()
    for n in norms:
        print(f"  [{n.norm_type:10s}] strength={n.strength:.2f} "
              f"emerged=Day {n.emerged_day} {n.description[:55]}...")


if __name__ == "__main__":
    db = SessionLocal()
    try:
        backfill(db)
    finally:
        db.close()
