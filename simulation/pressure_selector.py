"""
pressure_selector.py — identifies the dominant social pressures each day.

A day is not a list of ticks. A day is a set of social pressures.
This module reads the current state of the world and returns 1-2 named
pressures with the characters most implicated in each.

Called once per tick before scene selection.
"""
import logging
import random
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.models import (
    Character, CharacterRelationship, CharacterDisposition,
    NormRecord, ResourcePool, EnvironmentEvent, SignificantEvent,
)

logger = logging.getLogger("caldwell.pressure")


def identify_daily_pressures(sim_day: int, db: Session) -> list[dict]:
    """
    Returns a list of 1-2 pressure dicts, each with:
      - type: str (hunt_day, food_shortage, hunt_return, etc.)
      - intensity: float 0-1
      - characters: list[Character] — most implicated
      - description: str — what is actually happening
    """
    pressures = []

    # ── Check each pressure type in priority order ────────────────────────

    # 1. Environmental crisis (highest priority)
    env_pressure = _check_env_crisis(sim_day, db)
    if env_pressure:
        pressures.append(env_pressure)

    # 2. Food shortage
    if len(pressures) < 2:
        food = _check_food_shortage(sim_day, db)
        if food:
            pressures.append(food)

    # 3. Hunt return (day after a hunt norm fired)
    if len(pressures) < 2:
        hunt_return = _check_hunt_return(sim_day, db)
        if hunt_return:
            pressures.append(hunt_return)

    # 4. Norm execution day (hunt, build, patrol scheduled today)
    if len(pressures) < 2:
        norm_day = _check_norm_execution_day(sim_day, db)
        if norm_day:
            pressures.append(norm_day)

    # 5. Labor resentment (someone has unresolved resentment about work burden)
    if len(pressures) < 2:
        labor = _check_labor_resentment(sim_day, db)
        if labor:
            pressures.append(labor)

    # 6. Relationship tension (two characters with low trust and recent contact)
    if len(pressures) < 2:
        tension = _check_relationship_tension(sim_day, db)
        if tension:
            pressures.append(tension)

    # 7. Pair bond development (two characters with rising familiarity)
    if len(pressures) < 2:
        bond = _check_pair_bond(sim_day, db)
        if bond:
            pressures.append(bond)

    # 8. Status challenge (norm recently violated)
    if len(pressures) < 2:
        status = _check_status_challenge(sim_day, db)
        if status:
            pressures.append(status)

    # 9. Open question — handled last; daily_composer caps it to 1 scene max.
    # Kept here so the pressure exists for context, but it no longer dominates.
    if len(pressures) < 2:
        try:
            from simulation.open_question import get_question_driven_pressure
            oq = get_question_driven_pressure(sim_day, db)
            if oq:
                pressures.append(oq)
        except Exception:
            pass

    # Fallback: generic daily life pressure
    if not pressures:
        pressures.append(_daily_life_fallback(sim_day, db))

    logger.info(
        f"  Day {sim_day} pressures: "
        + ", ".join(p["type"] for p in pressures)
    )
    return pressures


# ── Pressure detectors ────────────────────────────────────────────────────────

def _check_env_crisis(sim_day: int, db: Session) -> dict | None:
    event = db.query(EnvironmentEvent).filter(
        EnvironmentEvent.resolved == False,
        EnvironmentEvent.start_day <= sim_day,
    ).order_by(EnvironmentEvent.severity.desc()).first()
    if not event:
        return None
    # Cast: characters with survival drive + highest status
    chars = _get_chars_by_drive(db, ["Survival", "Order", "Dominance"], limit=4)
    return {
        "type": "environmental_crisis",
        "intensity": min(event.severity, 1.0),
        "characters": chars,
        "description": event.description,
        "event_id": event.id,
    }


def _check_food_shortage(sim_day: int, db: Session) -> dict | None:
    pools = db.query(ResourcePool).filter(ResourcePool.resource_type == "food").all()
    if not pools:
        return None
    total = sum(p.quantity for p in pools)
    alive_count = db.query(Character).filter(Character.alive == True, Character.is_infant == False).count()
    days_remaining = total / max(alive_count, 1)
    if days_remaining > 2.5:
        return None
    intensity = max(0.3, 1.0 - (days_remaining / 2.5))
    chars = _get_chars_by_drive(db, ["Survival", "Connection", "Order", "Power"], limit=4)
    return {
        "type": "food_shortage",
        "intensity": intensity,
        "characters": chars,
        "description": f"Food supply is critically low — roughly {days_remaining:.1f} days remain at current consumption.",
    }


def _check_hunt_return(sim_day: int, db: Session) -> dict | None:
    """Check if yesterday was a hunt/forage/fish norm execution day."""
    try:
        row = db.execute(text(
            "SELECT action_verb, id FROM norm_records "
            "WHERE is_actionable = 1 AND is_active = 1 "
            "AND last_executed_day = :yesterday "
            "AND action_verb IN ('hunt', 'forage', 'fish') "
            "LIMIT 1"
        ), {"yesterday": sim_day - 1}).fetchone()
    except Exception:
        return None
    if not row:
        return None
    verb = row[0]
    # Cast: characters who went (survival/dominance drives) + those who waited
    hunters = _get_chars_by_drive(db, ["Survival", "Dominance", "Curiosity"], limit=3)
    waiters = _get_chars_by_drive(db, ["Connection", "Order", "Belonging"], limit=2)
    chars = list({c.id: c for c in hunters + waiters}.values())[:4]
    return {
        "type": "hunt_return",
        "intensity": 0.8,
        "characters": chars,
        "description": f"The {verb}ers returned yesterday. What they brought back — or didn't — is still being felt.",
        "action_verb": verb,
    }


def _check_norm_execution_day(sim_day: int, db: Session) -> dict | None:
    """Check if today is a scheduled norm execution day."""
    try:
        rows = db.execute(text(
            "SELECT action_verb, strength, id FROM norm_records "
            "WHERE is_actionable = 1 AND is_active = 1 "
            "AND (last_executed_day IS NULL OR :day - last_executed_day >= action_frequency_days) "
            "ORDER BY strength DESC LIMIT 1"
        ), {"day": sim_day}).fetchall()
    except Exception:
        return None
    if not rows:
        return None
    verb, strength, norm_id = rows[0]
    if not verb:
        return None
    drive_map = {
        "hunt": ["Survival", "Dominance", "Curiosity"],
        "fish": ["Survival", "Curiosity", "Knowledge"],
        "build": ["Order", "Meaning", "Curiosity"],
        "patrol": ["Survival", "Dominance", "Tribalism"],
        "cook": ["Connection", "Comfort", "Order"],
        "gather": ["Order", "Survival", "Belonging"],
        "teach": ["Knowledge", "Meaning", "Connection"],
    }
    drives = drive_map.get(verb, ["Survival", "Order"])
    chars = _get_chars_by_drive(db, drives, limit=4)
    return {
        "type": f"norm_day_{verb}",
        "intensity": 0.6 + (strength * 0.4),
        "characters": chars,
        "description": f"Today is {verb} day — an expectation has formed that this is when it happens.",
        "action_verb": verb,
        "norm_id": norm_id,
    }


def _check_labor_resentment(sim_day: int, db: Session) -> dict | None:
    """Find characters who are despairing/frustrated with low relationships."""
    despairing = db.query(CharacterDisposition).filter(
        CharacterDisposition.state.in_(["despairing", "frustrated"]),
        CharacterDisposition.last_updated_day >= sim_day - 3,
    ).all()
    if not despairing:
        return None
    char_ids = [d.character_id for d in despairing]
    chars = db.query(Character).filter(
        Character.id.in_(char_ids),
        Character.alive == True,
        Character.is_infant == False,
    ).all()
    if not chars:
        return None
    # Find someone with power/order drive to create the tension
    enforcers = _get_chars_by_drive(db, ["Order", "Power", "Dominance"], limit=2)
    all_chars = list({c.id: c for c in chars[:2] + enforcers}.values())[:4]
    return {
        "type": "labor_resentment",
        "intensity": 0.7,
        "characters": all_chars,
        "subject_id": chars[0].id,  # the person being sought/confronted
        "description": f"{chars[0].given_name or chars[0].roster_id} is carrying something unresolved about how work is distributed.",
    }


def _check_relationship_tension(sim_day: int, db: Session) -> dict | None:
    """Find a pair with low trust and recent interaction."""
    rels = db.query(CharacterRelationship).filter(
        CharacterRelationship.trust_level < -0.15,
        CharacterRelationship.last_interacted_day >= sim_day - 4,
        CharacterRelationship.interaction_count >= 2,
    ).order_by(CharacterRelationship.trust_level.asc()).first()
    if not rels:
        return None
    char_a = db.query(Character).filter(
        Character.id == rels.from_character_id, Character.alive == True
    ).first()
    char_b = db.query(Character).filter(
        Character.id == rels.to_character_id, Character.alive == True
    ).first()
    if not char_a or not char_b:
        return None
    return {
        "type": "relationship_tension",
        "intensity": min(abs(rels.trust_level), 1.0),
        "characters": [char_a, char_b],
        "description": f"There is unresolved tension between {char_a.given_name or char_a.roster_id} and {char_b.given_name or char_b.roster_id}.",
    }


def _check_pair_bond(sim_day: int, db: Session) -> dict | None:
    """Find a pair with rising familiarity and positive trust. Only fires after day 20."""
    if sim_day < 20:
        return None  # Too early — relationships haven't had time to develop meaningfully
    rel = db.query(CharacterRelationship).filter(
        CharacterRelationship.familiarity >= 0.65,
        CharacterRelationship.trust_level >= 0.5,
        CharacterRelationship.last_interacted_day >= sim_day - 3,
    ).order_by(CharacterRelationship.familiarity.desc()).first()
    if not rel:
        return None
    char_a = db.query(Character).filter(
        Character.id == rel.from_character_id, Character.alive == True
    ).first()
    char_b = db.query(Character).filter(
        Character.id == rel.to_character_id, Character.alive == True
    ).first()
    if not char_a or not char_b:
        return None
    return {
        "type": "pair_bond",
        "intensity": rel.familiarity,
        "characters": [char_a, char_b],
        "description": f"{char_a.given_name or char_a.roster_id} and {char_b.given_name or char_b.roster_id} keep finding each other.",
    }


def _check_status_challenge(sim_day: int, db: Session) -> dict | None:
    """Check for recent norm violations that created status instability."""
    norms = db.query(NormRecord).filter(
        NormRecord.is_active == True,
        NormRecord.strength >= 0.3,
    ).order_by(NormRecord.violated_count.desc()).first()
    if not norms or norms.violated_count == 0:
        return None
    chars = _get_chars_by_drive(db, ["Power", "Dominance", "Order", "Tribalism"], limit=3)
    if not chars:
        return None
    return {
        "type": "status_challenge",
        "intensity": min(norms.strength + 0.2, 1.0),
        "characters": chars,
        "description": f"The expectation around '{norms.norm_type}' has been tested. Someone's standing is in question.",
        "norm_id": norms.id,
    }


def _daily_life_fallback(sim_day: int, db: Session) -> dict:
    """Generic daily life scene — people doing the work of existing."""
    chars = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
    ).order_by(Character.id).all()
    selected = random.sample(chars, min(4, len(chars))) if chars else []
    return {
        "type": "daily_life",
        "intensity": 0.4,
        "characters": selected,
        "description": "An ordinary day — the kind that turns out to matter later.",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_chars_by_drive(db: Session, drives: list[str], limit: int = 4) -> list[Character]:
    all_chars = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
    ).all()
    preferred = [c for c in all_chars if c.core_drive in drives]
    others = [c for c in all_chars if c.core_drive not in drives]
    combined = preferred + others
    random.shuffle(preferred)
    return (preferred + others)[:limit]
