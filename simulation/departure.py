"""
departure.py — organic character departure from Caldwell.

Characters who fail to form bonds with anyone, remain persistently
despairing, and have no functional role in the community will eventually
leave. This is not death — they are marked as left_community=True.

This naturally prunes the cast to the people who matter.

Rules:
- sim_day must be > 10 (give everyone time to settle in)
- Character must be adult (not minor, not infant)
- No relationships with familiarity > 0.2 (no real bonds)
- Disposition must be despairing or frustrated for multiple ticks
- At most 1 departure per tick
- Children never depart alone

Called from engine.py at end of tick.
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, CharacterRelationship, CharacterDisposition,
    Memory, SignificantEvent,
)

logger = logging.getLogger("caldwell.departure")

DEPARTURE_REASONS = [
    "drifted away from the group — no one was certain when exactly they stopped coming back",
    "left one morning without telling anyone. No one went after them.",
    "stopped appearing at the common spaces. After a few days it was clear they weren't coming back.",
    "walked out toward the outskirts and didn't return. It wasn't a surprise to the people who noticed.",
    "faded from the group gradually — present less and less until they weren't.",
]


def check_departures(sim_day: int, db: Session) -> list[dict]:
    """
    Returns list of departure event dicts for any characters who leave.
    Max 1 departure per call.
    """
    if sim_day <= 10:
        return []

    candidates = _find_departure_candidates(sim_day, db)
    if not candidates:
        return []

    # Pick one — lowest familiarity wins (most isolated)
    char = candidates[0]
    return [_execute_departure(char, sim_day, db)]


def _find_departure_candidates(sim_day: int, db: Session) -> list[Character]:
    """
    Find adults who have not formed bonds and are persistently unhappy.
    """
    alive = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.is_minor == False,
    ).all()

    candidates = []
    for char in alive:
        # Check disposition
        disp = db.query(CharacterDisposition).filter(
            CharacterDisposition.character_id == char.id,
        ).first()
        if not disp:
            continue
        if disp.state not in ("despairing", "frustrated"):
            continue
        if disp.last_updated_day < sim_day - 5:
            continue  # stale — disposition may have improved

        # Check relationships — any meaningful bonds?
        rels = db.query(CharacterRelationship).filter(
            CharacterRelationship.from_character_id == char.id,
        ).all()
        max_familiarity = max((r.familiarity for r in rels), default=0.0)
        if max_familiarity > 0.2:
            continue  # they have someone — they stay

        # Check rolling average — persistently negative?
        if disp.rolling_average > -0.1:
            continue  # not consistently bad enough

        candidates.append((max_familiarity, char))

    # Sort by most isolated first
    candidates.sort(key=lambda x: x[0])
    return [c for _, c in candidates]


def _execute_departure(char: Character, sim_day: int, db: Session) -> dict:
    """Mark character as having left and write departure memories."""
    reason = random.choice(DEPARTURE_REASONS)
    char.alive = False

    # Write a departure memory for the character themselves
    db.add(Memory(
        character_id=char.id,
        sim_day=sim_day,
        memory_type="feeling",
        content=f"I left Caldwell on day {sim_day}. There was nothing holding me here strongly enough.",
        emotional_weight=0.9,
        is_inception=False,
    ))

    # Write a memory for any character who interacted with them
    rels = db.query(CharacterRelationship).filter(
        CharacterRelationship.to_character_id == char.id,
        CharacterRelationship.interaction_count >= 1,
    ).all()
    for rel in rels[:4]:  # only the ones who knew them at all
        witness = db.query(Character).filter(
            Character.id == rel.from_character_id,
            Character.alive == True,
        ).first()
        if witness:
            db.add(Memory(
                character_id=witness.id,
                sim_day=sim_day,
                memory_type="observation",
                content=(
                    f"{char.given_name or 'someone I knew'} {reason}. "
                    f"I'm not sure what to make of it."
                ),
                emotional_weight=0.6,
                is_inception=False,
            ))

    # Log as significant event
    db.add(SignificantEvent(
        sim_day=sim_day,
        event_type="departure",
        description=f"{char.given_name or char.roster_id} {reason}",
        character_ids_json=f"[{char.id}]",
        emotional_weight=0.5,
    ))

    db.commit()

    name = char.given_name or char.roster_id
    logger.info(f"  DEPARTURE: {name} ({char.roster_id}) left on day {sim_day}")

    return {
        "type": "departure",
        "roster_id": char.roster_id,
        "given_name": char.given_name,
        "reason": reason,
        "sim_day": sim_day,
    }
