"""
disposition_tracker.py

Maintains a rolling 7-day satisfaction average per character.
Maps that average to a named disposition state and a prompt modifier.

Dispositions:
  despairing     score < -0.6  (extreme chronic frustration)
  frustrated     score -0.6 to -0.2
  neutral        score -0.2 to 0.2  (no modifier injected)
  content        score 0.2 to 0.6
  flourishing    score > 0.6

The modifier is a short paragraph injected into the character's
system prompt — they don't know it's there, it just shapes how
they feel going into the next conversation.
"""
import logging
from sqlalchemy.orm import Session
from database.models import Character, SatisfactionLog, CharacterDisposition

logger = logging.getLogger("caldwell.disposition")

WINDOW_DAYS = 7  # rolling average window

# Disposition thresholds
DESPAIRING_THRESHOLD  = -0.6
FRUSTRATED_THRESHOLD  = -0.2
CONTENT_THRESHOLD     =  0.2
FLOURISHING_THRESHOLD =  0.6

# Prompt modifiers per disposition level
MODIFIERS = {
    "despairing": (
        "Something has been wrong for a long time now. "
        "You can't name it exactly but it sits in you like a stone. "
        "You are shorter with people than you mean to be. "
        "Small things feel like larger insults than they are. "
        "You are not giving up — but you are tired of trying in the same ways."
    ),
    "frustrated": (
        "Things haven't been going the way you hoped lately. "
        "You feel a low-grade tension that doesn't quite go away. "
        "You're still trying, but you notice yourself holding back more than before."
    ),
    "neutral": None,  # no modifier
    "content": (
        "Things have been going reasonably well. "
        "You feel a quiet steadiness — not ecstatic, but solid. "
        "You find yourself a little more willing to extend yourself for others."
    ),
    "flourishing": (
        "You have been getting what you need. "
        "There is a kind of ease in you right now — a confidence "
        "that isn't arrogance but feels like having enough. "
        "You notice you are more generous, more patient, more willing to listen."
    ),
}

# Drive-specific flavor added on top of the base modifier
DRIVE_FLAVOR = {
    "despairing": {
        "Power":      "No one has been listening. You are starting to wonder if you need to make them.",
        "Connection": "You have been reaching and no one has reached back. It is starting to feel like maybe you are asking for something that doesn't exist here.",
        "Knowledge":  "Every conversation has been shallow. You feel intellectually starved.",
        "Order":      "Everything is chaos and no one seems to care. The disorder feels personal by now.",
        "Curiosity":  "Everything has become predictable and dull. You feel trapped in repetition.",
        "Comfort":    "You haven't felt safe in days. Every interaction carries an edge you didn't put there.",
        "Survival":   "You don't know who you can trust. You are starting to assume the answer is no one.",
    },
    "flourishing": {
        "Power":      "People are listening to you. You feel the weight of that — and you want to use it well.",
        "Connection": "You feel genuinely close to people here. It is something you didn't know you needed this much.",
        "Knowledge":  "You are learning constantly. This place is full of things you haven't understood yet.",
        "Order":      "Things are coming together. You can feel a structure forming and it feels right.",
        "Curiosity":  "Every day has been surprising. You feel more alive here than you have anywhere.",
        "Comfort":    "You feel safe. It is not something you take for granted.",
        "Survival":   "You know where you stand with people. That clarity is everything.",
    },
}


def compute_disposition(character: Character, sim_day: int, db: Session) -> dict:
    """
    Compute the current disposition for a character based on
    their rolling 7-day satisfaction average.
    Returns a dict with: state, average, modifier (str or None)
    """
    min_day = max(1, sim_day - WINDOW_DAYS + 1)
    logs = (
        db.query(SatisfactionLog)
        .filter(
            SatisfactionLog.character_id == character.id,
            SatisfactionLog.sim_day >= min_day,
            SatisfactionLog.sim_day <= sim_day,
        )
        .all()
    )

    if not logs:
        return {"state": "neutral", "average": 0.0, "modifier": None}

    avg = sum(l.score for l in logs) / len(logs)
    avg = round(avg, 3)

    if avg < DESPAIRING_THRESHOLD:
        state = "despairing"
    elif avg < FRUSTRATED_THRESHOLD:
        state = "frustrated"
    elif avg < CONTENT_THRESHOLD:
        state = "neutral"
    elif avg < FLOURISHING_THRESHOLD:
        state = "content"
    else:
        state = "flourishing"

    base_mod = MODIFIERS.get(state)
    drive_flavor = DRIVE_FLAVOR.get(state, {}).get(character.core_drive, "")
    if base_mod and drive_flavor:
        modifier = base_mod + " " + drive_flavor
    else:
        modifier = base_mod

    return {"state": state, "average": avg, "modifier": modifier}


def update_disposition_record(character: Character, sim_day: int, db: Session):
    """Compute and persist the current disposition state to DB."""
    result = compute_disposition(character, sim_day, db)

    existing = (
        db.query(CharacterDisposition)
        .filter(CharacterDisposition.character_id == character.id)
        .first()
    )
    if existing:
        existing.state = result["state"]
        existing.rolling_average = result["average"]
        existing.last_updated_day = sim_day
    else:
        record = CharacterDisposition(
            character_id=character.id,
            state=result["state"],
            rolling_average=result["average"],
            last_updated_day=sim_day,
        )
        db.add(record)
    db.commit()

    logger.info(
        f"  Disposition {character.roster_id}: {result['state']} "
        f"(avg={result['average']:+.3f})"
    )
    return result


def get_disposition_modifier(character: Character, sim_day: int, db: Session) -> str | None:
    """
    Get the current disposition modifier string for use in prompt building.
    Returns None if the character is in neutral range.
    """
    result = compute_disposition(character, sim_day, db)
    return result.get("modifier")
