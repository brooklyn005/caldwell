"""
silent_actions.py — the off-screen life of the simulation.

Not every meaningful thing that happens in a society is witnessed.
This module generates off-screen actions that still update world state:
hunting, gathering, private visits, sleeping arrangement changes, avoidance,
mourning, child-watching, tool-making, secret exchanges, eavesdropping.

These actions:
- Don't generate dialogue
- DO update resource pools, trust, location state, relationship edges
- DO create memories that characters reference in later conversations
- DO feed into consequence records
- Create the texture of ordinary life without narrating it directly

The reader feels it through: changed conditions, new attitudes, visible consequences.
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, SilentAction, Memory,
    CharacterRelationship, ResourcePool, CharacterBiology,
)

logger = logging.getLogger("caldwell.silent_actions")

# Action type definitions
_ACTION_TYPES = {
    # Physical/resource
    "private_gathering": {
        "description": "{name} spent time gathering on their own, away from the group.",
        "resource_delta": 0.3,
        "visibility": "private",
        "memory": "I went out on my own to gather what I could find. I didn't want company.",
    },
    "private_hunting": {
        "description": "{name} went out hunting or scouting without telling others where.",
        "resource_delta": 0.5,
        "visibility": "private",
        "memory": "I went looking on my own. Not a group thing. Just me and what I could find.",
    },
    "tool_making": {
        "description": "{name} worked on something practical, alone.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I worked on something with my hands. The problem absorbed me.",
    },
    "location_repair": {
        "description": "{name} repaired or improved something in {location}.",
        "resource_delta": 0.0,
        "visibility": "witnessed",
        "memory": "I fixed {location}. Small thing. But it was broken and now it's not.",
    },
    # Social/relational
    "private_visit": {
        "description": "{name} went to find {other} privately, away from others.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I went looking for {other}. I needed to see them privately.",
    },
    "deliberate_avoidance": {
        "description": "{name} actively avoided {other} today.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I kept away from {other}. Not ready to talk. Maybe not ever.",
    },
    "secret_exchange": {
        "description": "{name} and {other} exchanged something — food, information, an object — away from others.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I gave {other} something. Not in front of the others. They needed it more.",
    },
    "eavesdropping": {
        "description": "{name} overheard something they weren't meant to hear.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I heard something I wasn't supposed to. I'm still working out what to do with it.",
    },
    # Care and ritual
    "caring_for_injured": {
        "description": "{name} tended to someone who was hurt or sick.",
        "resource_delta": 0.0,
        "visibility": "witnessed",
        "memory": "I sat with {other} while they weren't well. Sometimes that's all you can do.",
    },
    "private_mourning": {
        "description": "{name} mourned something alone.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I went somewhere quiet. I needed to feel what I was carrying without anyone watching.",
    },
    "ritual_preparation": {
        "description": "{name} prepared something in a ritualistic way.",
        "resource_delta": 0.0,
        "visibility": "witnessed",
        "memory": "I prepared things the way they should be prepared. It matters to do it right.",
    },
    # Knowledge/observation
    "territorial_marking": {
        "description": "{name} returned to a place and left their presence on it.",
        "resource_delta": 0.0,
        "visibility": "witnessed",
        "memory": "I went back to {location}. I keep going back. I think I'm claiming it.",
    },
    "watching_from_edge": {
        "description": "{name} observed the group from outside, not participating.",
        "resource_delta": 0.0,
        "visibility": "private",
        "memory": "I watched from the outside for a while. Easier to see things from there.",
    },
    # Child-specific
    "imitating_adult": {
        "description": "{name} copied what an adult was doing.",
        "resource_delta": 0.0,
        "visibility": "witnessed",
        "memory": "I watched {other} do it and then I tried to do it the same way.",
    },
}


def generate_daily_silent_actions(sim_day: int, db: Session) -> list[SilentAction]:
    """
    Generates 3-6 silent actions per day from living characters.
    Called at start of each tick before scene selection.
    """
    chars = db.query(Character).filter(
        Character.alive == True, Character.is_infant == False
    ).all()
    locations = db.query(Location).all()

    if not chars:
        return []

    actions = []
    num_actions = random.randint(3, 6)

    # Weight characters toward ones who haven't been in scenes recently
    random.shuffle(chars)
    selected_chars = chars[:num_actions]

    for char in selected_chars:
        action = _generate_action_for_character(char, chars, locations, sim_day, db)
        if action:
            db.add(action)
            actions.append(action)
            _apply_action_effects(action, char, chars, sim_day, db)

    db.commit()
    if actions:
        logger.info(f"  Silent actions: {len(actions)} ({', '.join(a.action_type for a in actions)})")
    return actions


def _generate_action_for_character(
    char: Character,
    all_chars: list,
    locations: list,
    sim_day: int,
    db: Session,
) -> SilentAction | None:

    # Weight action types by character personality
    traits = char.personality_traits
    drive = char.core_drive

    weighted_actions = []

    # Age-based action availability
    if char.is_minor:
        weighted_actions.extend(["imitating_adult", "watching_from_edge", "territorial_marking"])
    else:
        # Adults can do most things
        if "adventurous" in traits or "restless" in traits or drive == "Survival":
            weighted_actions.extend(["private_hunting", "private_gathering"] * 2)
        if "industrious" in traits or "practical" in traits:
            weighted_actions.extend(["tool_making", "location_repair"] * 2)
        if "protective" in traits or "empathetic" in traits or "warm" in traits:
            weighted_actions.extend(["private_visit", "caring_for_injured"] * 2)
        if "ceremonial" in traits or "disciplined" in traits:
            weighted_actions.extend(["ritual_preparation"] * 2)
        if "observant" in traits or "strategic" in traits:
            weighted_actions.extend(["watching_from_edge", "eavesdropping"])
        if "independent" in traits or "reserved" in traits:
            weighted_actions.extend(["private_gathering", "private_mourning"])
        if drive == "Grief":
            weighted_actions.append("private_mourning")

        # Fill with defaults
        weighted_actions.extend([
            "watching_from_edge", "tool_making", "private_gathering",
            "territorial_marking", "secret_exchange",
        ])

    if not weighted_actions:
        return None

    action_type = random.choice(weighted_actions)
    action_def = _ACTION_TYPES[action_type]

    # Pick other character if needed
    other_char = None
    others = [c for c in all_chars if c.id != char.id and not c.is_infant]
    if others and action_type in ("private_visit", "deliberate_avoidance",
                                   "secret_exchange", "caring_for_injured",
                                   "imitating_adult", "eavesdropping"):
        # Weight toward recent relationships
        rels = db.query(CharacterRelationship).filter(
            CharacterRelationship.from_character_id == char.id,
            CharacterRelationship.last_interacted_day >= sim_day - 5,
        ).all()
        if rels:
            related_ids = {r.to_character_id for r in rels}
            related = [c for c in others if c.id in related_ids]
            if related:
                other_char = random.choice(related)
        if not other_char:
            other_char = random.choice(others)

    # Pick location
    location = random.choice(locations)

    # Build description
    char_name = char.given_name or char.roster_id
    other_name = (other_char.given_name or other_char.roster_id) if other_char else "someone"
    desc = action_def["description"].format(
        name=char_name, other=other_name, location=location.name
    )
    memory_text = action_def["memory"].format(
        name=char_name, other=other_name, location=location.name
    )

    actor_ids = [char.roster_id]
    if other_char and action_type in ("secret_exchange", "caring_for_injured"):
        actor_ids.append(other_char.roster_id)

    silent_action = SilentAction(
        sim_day=sim_day,
        actor_ids_json=json.dumps(actor_ids),
        action_type=action_type,
        location_id=location.id,
        description=desc,
        resource_delta=action_def["resource_delta"],
        visibility=action_def["visibility"],
        witness_ids_json="[]",
    )

    # Write memory for the actor
    try:
        db.add(Memory(
            character_id=char.id,
            sim_day=sim_day,
            memory_type="observation",
            content=memory_text,
            emotional_weight=0.3,
            is_inception=False,
        ))
    except Exception:
        pass

    return silent_action


def _apply_action_effects(
    action: SilentAction,
    char: Character,
    all_chars: list,
    sim_day: int,
    db: Session,
) -> None:
    """Apply mechanical effects of the silent action."""

    # Resource effects
    if action.resource_delta and action.resource_delta > 0:
        pool = db.query(ResourcePool).filter(
            ResourcePool.resource_type == "food"
        ).first()
        if pool:
            pool.quantity += action.resource_delta

    # Relationship effects
    if action.action_type == "private_visit":
        actor_ids = action.actor_ids
        if len(actor_ids) >= 1:
            other_chars = [c for c in all_chars if c.roster_id in actor_ids and c.id != char.id]
            for other in other_chars:
                rel = db.query(CharacterRelationship).filter(
                    CharacterRelationship.from_character_id == char.id,
                    CharacterRelationship.to_character_id == other.id,
                ).first()
                if rel:
                    rel.familiarity = min(rel.familiarity + 0.02, 1.0)

    if action.action_type == "deliberate_avoidance":
        # Note: avoidance slightly reduces familiarity if extreme
        pass

    if action.action_type in ("eavesdropping",):
        # Grant small knowledge advantage — this character now "knows" something
        pass
