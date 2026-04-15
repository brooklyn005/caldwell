"""
action_processor.py — processes operator-injected action events.

When an operator injects a scene (e.g. "M-03 asks M-07 to go hunting"),
that scene OVERRIDES the normal pressure-based pairing for those characters.
It becomes their conversation for the day — a full extended scene, not
a 2-sentence reaction.

Flow:
  1. Write a setup memory for each participant (the framing of what happened)
  2. Move characters to an appropriate location for the scene
  3. Return ScenePlan objects that the engine runs before pressure-based scenes
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, ActionEvent, Memory, Location, SignificantEvent
)
from simulation.cost_tracker import CostTracker

logger = logging.getLogger("caldwell.action")

# Maps keywords in scene description → best location
SCENE_LOCATION_HINTS = {
    "hunt":      ["Warehouse Row", "The Outskirts"],
    "fishing":   ["Bayou Market", "The Outskirts"],
    "cook":      ["Community Center", "Bayou Market"],
    "build":     ["Warehouse Row", "The Workshop"],
    "repair":    ["The Workshop", "Community Center"],
    "teach":     ["The Schoolhouse", "Community Center"],
    "library":   ["Caldwell Public Library"],
    "garden":    ["Rooftop Garden"],
    "river":     ["Riverside Park"],
    "market":    ["Bayou Market"],
    "square":    ["Central Square"],
    "chapel":    ["The Chapel"],
    "workshop":  ["The Workshop"],
}


def _strip_roster_ids(text: str, db: Session) -> str:
    chars = db.query(Character).all()
    for c in chars:
        display = c.given_name if c.given_name else c.physical_description[:45]
        text = text.replace(c.roster_id, display)
    return text


def _pick_location_from_scene(scene_text: str, db: Session) -> Location | None:
    """Infer best location from keywords in the scene description."""
    text_lower = scene_text.lower()
    for keyword, loc_names in SCENE_LOCATION_HINTS.items():
        if keyword in text_lower:
            for name in loc_names:
                loc = db.query(Location).filter(Location.name == name).first()
                if loc:
                    return loc
    return None


def _write_memory(character, content, sim_day, db, emotional_weight=0.85):
    db.add(Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type="observation",
        content=content,
        emotional_weight=emotional_weight,
        is_inception=False,
    ))


async def process_action_events(
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
    broadcast_fn=None,
) -> list:
    """
    Find and process all action events due on this day.
    Returns list of ScenePlan-compatible dicts for injected scenes.
    Characters are moved to scene location. Full conversation follows.
    """
    from simulation.scene_selector import ScenePlan, SCENE_PURPOSES

    pending = (
        db.query(ActionEvent)
        .filter(
            ActionEvent.inject_on_day == sim_day,
            ActionEvent.processed == False,
        )
        .all()
    )

    if not pending:
        return []

    scene_plans = []

    for event in pending:
        plan = await _process_single_event(
            event, sim_day, db, cost_tracker, broadcast_fn
        )
        if plan:
            scene_plans.append(plan)
        event.processed = True
        event.processed_day = sim_day
        db.commit()

    return scene_plans


async def _process_single_event(
    event: ActionEvent,
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
    broadcast_fn,
):
    from simulation.scene_selector import ScenePlan, SCENE_PURPOSES

    # Resolve characters
    all_roster_ids = event.participant_ids + event.witness_ids
    characters = {}
    for rid in all_roster_ids:
        c = db.query(Character).filter(
            Character.roster_id == rid, Character.alive == True
        ).first()
        if c:
            characters[rid] = c

    if not characters:
        logger.warning(f"Action event {event.id}: no valid characters found")
        return None

    # Clean scene description
    clean_scene = _strip_roster_ids(event.scene_description, db)

    logger.info(f"Day {sim_day}: Injected scene — {clean_scene[:80]}...")

    if broadcast_fn:
        await broadcast_fn({
            "type": "action_event",
            "data": {
                "sim_day": sim_day,
                "scene": clean_scene,
                "participants": event.participant_ids,
                "witnesses": event.witness_ids,
            },
        })

    # ── Pick location — infer from scene text or use primary character's location ──
    location = _pick_location_from_scene(clean_scene, db)
    if not location:
        primary = characters.get(event.participant_ids[0]) if event.participant_ids else None
        if primary and primary.current_location_id:
            location = db.query(Location).filter(
                Location.id == primary.current_location_id
            ).first()
    if not location:
        location = db.query(Location).filter(
            Location.name == "Central Square"
        ).first()

    # ── Move all participants to the scene location ──────────────────────────
    for char in characters.values():
        char.current_location_id = location.id
    db.commit()

    # ── Write setup memory for each participant ──────────────────────────────
    participant_chars = [
        characters[rid] for rid in event.participant_ids if rid in characters
    ]
    witness_chars = [
        characters[rid] for rid in event.witness_ids if rid in characters
    ]

    for char in participant_chars:
        others = [c for c in participant_chars if c.id != char.id]
        other_names = ", ".join(c.given_name or c.physical_description[:30] for c in others)
        _write_memory(
            char,
            f"[Day {sim_day}] {clean_scene}"
            + (f" (with {other_names})" if other_names else ""),
            sim_day, db, emotional_weight=0.9,
        )

    for char in witness_chars:
        _write_memory(
            char,
            f"[Day {sim_day}] I witnessed: {clean_scene[:200]}",
            sim_day, db, emotional_weight=0.7,
        )

    db.commit()

    # ── Log to significant events ────────────────────────────────────────────
    char_ids = [c.id for c in characters.values()]
    db.add(SignificantEvent(
        sim_day=sim_day,
        event_type="action_inject",
        description=f"[Operator scene] {clean_scene[:400]}",
        character_ids_json=json.dumps(sorted(char_ids)),
        emotional_weight=0.95,
    ))
    db.commit()

    # ── Build ScenePlan — this becomes their conversation for the day ────────
    if len(participant_chars) < 2:
        logger.info(f"  Single-character inject — memory written, no scene generated")
        return None

    is_group = len(participant_chars) >= 3
    scene_type = "preparation" if any(
        w in clean_scene.lower() for w in ["hunt", "build", "patrol", "fish", "forage"]
    ) else "argument" if any(
        w in clean_scene.lower() for w in ["ask", "demand", "confront", "challenge", "argue"]
    ) else "quiet_intimacy"

    dramatic_purpose = SCENE_PURPOSES.get(scene_type, "")

    # Build a rich scene context grounded in the injected description
    names = [c.given_name or c.physical_description[:30] for c in participant_chars]
    name_str = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + f", and {names[-1]}"

    scene_context = (
        f"{name_str} at {location.name}. "
        f"{clean_scene} "
        f"This is happening right now. Their bodies are in this moment. "
        f"Let the conversation grow from it."
    )

    logger.info(
        f"  Injected scene → {scene_type} with "
        f"{[c.roster_id for c in participant_chars]} at {location.name}"
    )

    return ScenePlan(
        scene_type=scene_type,
        dramatic_purpose=dramatic_purpose,
        characters=participant_chars,
        location=location,
        pressure_type="injected",
        scene_context=scene_context,
        is_group=is_group,
        is_injected=True,
    )
