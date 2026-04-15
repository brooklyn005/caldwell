"""
daily_composer.py — the Daily Composition Engine.

Replaces direct pressure→scene mapping with top-down day composition.
The key innovation: days are COMPOSED, not assembled from strongest pressures.

A day template specifies required slot categories, caps on repetitive types,
and pair/location cooldowns. Only within those constraints does pressure
drive scene selection.

Day template structure:
  SLOT 1 (required): tension/conflict/political
  SLOT 2 (required): connection/care/labor/teaching/ritual/ambient
  SLOT 3 (required): consequence/world-state/aftermath or ambient
  SLOT 4 (optional): discovery/romance/secrecy/status shift

Hard caps enforced every day:
  - open_question scenes: max 1
  - argument scenes: max 1 (2 if nothing else fires)
  - same pair repeating: 3-day cooldown
  - Central Square: max 2 scenes/day
  - status_challenge: max 1

The result: no day is dominated by a single scene type.
Civilization feels like it has CADENCE, not just serial conflicts.
"""
import json
import logging
import random
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, TickLog, DayComposition, CharacterRelationship,
    Scene, OpenQuestion,
)
from simulation.scene_selector import (
    ScenePlan, SCENE_PURPOSES, SCENE_LOCATIONS, PRESSURE_TO_SCENES,
    _cast_scene, _pick_location, _build_scene_context, _cast_second_scene,
)
from simulation.pressure_selector import identify_daily_pressures
from simulation.rhythms import get_due_rhythms, build_rhythm_scene_plan

logger = logging.getLogger("caldwell.composer")

# ── Slot category definitions ─────────────────────────────────────────────────

# Each slot is a category. Scene types that qualify for each slot.
SLOT_CATEGORIES = {
    "tension": [
        "argument", "status_challenge", "correction", "resentment", "distribution"
    ],
    "connection": [
        "quiet_intimacy", "teaching", "gossip", "preparation", "return",
        "ambient_meal", "ambient_labor", "ambient_care", "ritual"
    ],
    "ambient": [
        "ambient_meal", "ambient_labor", "ambient_care", "ambient_boredom",
        "ambient_grooming", "ambient_storytelling", "ambient_avoidance",
        "teaching", "gossip", "ritual"
    ],
    "consequence": [
        "gossip",  # aftermath spreading
        "correction",  # someone being held accountable
        "ritual",    # a behavior hardening
        "resentment",  # aftermath of earlier conflict
        "teaching",  # knowledge transmission following an event
    ],
    "discovery": [
        "quiet_intimacy", "status_challenge", "teaching", "ritual",
        "gossip"
    ],
}

# Maps scene types to their primary slot category (for cap tracking)
SCENE_SLOT_CATEGORY = {
    "argument": "tension",
    "status_challenge": "tension",
    "correction": "tension",
    "resentment": "tension",
    "distribution": "tension",
    "quiet_intimacy": "connection",
    "teaching": "connection",
    "gossip": "connection",
    "preparation": "connection",
    "return": "connection",
    "ritual": "connection",
    **{f"ambient_{t}": "ambient" for t in [
        "meal", "labor", "care", "boredom", "grooming", "storytelling", "avoidance"
    ]},
}

# Day archetypes — what kind of day is this overall
DAY_ARCHETYPES = [
    ("ordinary_day",    0.30),
    ("anxious_day",     0.12),
    ("accusation_day",  0.08),
    ("hungry_day",      0.08),
    ("pairing_day",     0.10),
    ("mourning_day",    0.05),
    ("repair_day",      0.10),
    ("reordering_day",  0.07),
    ("ritual_day",      0.05),
    ("storm_day",       0.05),
]

ARCHETYPE_SCENE_BIAS = {
    "ordinary_day":    {"ambient_meal": 2, "teaching": 2, "gossip": 1},
    "anxious_day":     {"resentment": 3, "argument": 2, "correction": 2},
    "accusation_day":  {"argument": 4, "status_challenge": 3, "correction": 2},
    "hungry_day":      {"distribution": 4, "argument": 2, "resentment": 2},
    "pairing_day":     {"quiet_intimacy": 4, "gossip": 2},
    "mourning_day":    {"ambient_care": 3, "resentment": 2, "ritual": 2},
    "repair_day":      {"ambient_labor": 3, "preparation": 2, "teaching": 2},
    "reordering_day":  {"status_challenge": 3, "correction": 3, "gossip": 2},
    "ritual_day":      {"ritual": 4, "ambient_meal": 2, "teaching": 1},
    "storm_day":       {"preparation": 3, "distribution": 2, "ambient_care": 2},
}

# Day labels by archetype
ARCHETYPE_LABELS = {
    "ordinary_day":    "An ordinary day — the kind that turns out to matter later.",
    "anxious_day":     "Something is wrong and no one is quite saying what.",
    "accusation_day":  "Someone is being held to account today. Everyone feels it.",
    "hungry_day":      "Food is the question today. Everything else follows from it.",
    "pairing_day":     "Two people keep finding each other. Something is forming.",
    "mourning_day":    "Something has been lost or is close to being lost. The weight shows.",
    "repair_day":      "A day of practical work. Things are being fixed.",
    "reordering_day":  "The shape of who matters here is shifting.",
    "ritual_day":      "Something is being done the way it has come to be done. The custom holds.",
    "storm_day":       "Something external is pressing in. The group responds together.",
}


def compose_day(
    sim_day: int,
    db: Session,
    max_scenes: int = 4,
    exclude_ids: set | None = None,
    injected_plans: list = None,
) -> tuple[list[ScenePlan], DayComposition]:
    """
    Main entry point. Composes a balanced day of scenes.

    Returns (scene_plans, day_composition_record)
    """
    exclude_ids = exclude_ids or set()
    injected_plans = injected_plans or []

    # Determine pair cooldowns from recent tick logs
    pair_cooldowns = _get_pair_cooldowns(sim_day, db)

    # Determine location overuse
    location_cooldowns = _get_location_cooldowns(sim_day, db)

    # Choose day archetype
    archetype = _choose_archetype(sim_day, db)
    archetype_label = ARCHETYPE_LABELS.get(archetype, "")
    scene_bias = ARCHETYPE_SCENE_BIAS.get(archetype, {})

    logger.info(f"  Day {sim_day} archetype: {archetype}")

    # Get all available pressures
    pressures = identify_daily_pressures(sim_day, db)

    # Plan required slots
    required_slots = _plan_required_slots(archetype, pressures)

    # Track what's been used
    used_char_ids = set(exclude_ids)
    used_char_ids.update(c.id for p in injected_plans for c in p.characters)

    scenes_built: list[ScenePlan] = []
    scene_type_counts: dict[str, int] = {}
    suppressed: list[str] = []

    locations = db.query(Location).all()
    loc_by_name = {l.name: l for l in locations}

    # ── Check for due rhythms (slot 2 preference) ─────────────────────────
    due_rhythms = get_due_rhythms(sim_day)
    rhythm_used = False

    # ── Fill required slots ────────────────────────────────────────────────
    for slot_index, slot_category in enumerate(required_slots):
        if len(scenes_built) >= max_scenes - len(injected_plans):
            break

        # Slot 2 (index 1): prefer a rhythm-driven scene if one is due
        if slot_index == 1 and due_rhythms and not rhythm_used:
            for rhythm in due_rhythms:
                if rhythm.scene_type in SLOT_CATEGORIES.get(slot_category, []) or slot_category in ("connection", "ambient"):
                    rhythm_plan = build_rhythm_scene_plan(
                        rhythm=rhythm,
                        sim_day=sim_day,
                        db=db,
                        used_char_ids=used_char_ids,
                        pair_cooldowns=pair_cooldowns,
                        loc_by_name=loc_by_name,
                        locations=locations,
                    )
                    if rhythm_plan:
                        scenes_built.append(rhythm_plan)
                        scene_type_counts[rhythm_plan.scene_type] = scene_type_counts.get(rhythm_plan.scene_type, 0) + 1
                        used_char_ids.update(c.id for c in rhythm_plan.characters)
                        if rhythm_plan.location and rhythm_plan.location.name:
                            location_cooldowns[rhythm_plan.location.name] = location_cooldowns.get(rhythm_plan.location.name, 0) + 1
                        rhythm_used = True
                        break
            if rhythm_used:
                continue

        plan = _fill_slot(
            slot_category=slot_category,
            pressures=pressures,
            scene_type_counts=scene_type_counts,
            scene_bias=scene_bias,
            pair_cooldowns=pair_cooldowns,
            location_cooldowns=location_cooldowns,
            used_char_ids=used_char_ids,
            sim_day=sim_day,
            db=db,
            loc_by_name=loc_by_name,
            locations=locations,
        )
        if plan:
            scenes_built.append(plan)
            scene_type_counts[plan.scene_type] = scene_type_counts.get(plan.scene_type, 0) + 1
            used_char_ids.update(c.id for c in plan.characters)
            if plan.location and plan.location.name:
                location_cooldowns[plan.location.name] = location_cooldowns.get(plan.location.name, 0) + 1
        else:
            suppressed.append(slot_category)

    # ── Fill optional 4th slot if space ──────────────────────────────────
    if len(scenes_built) < max_scenes - len(injected_plans):
        plan = _fill_slot(
            slot_category="discovery",
            pressures=pressures,
            scene_type_counts=scene_type_counts,
            scene_bias=scene_bias,
            pair_cooldowns=pair_cooldowns,
            location_cooldowns=location_cooldowns,
            used_char_ids=used_char_ids,
            sim_day=sim_day,
            db=db,
            loc_by_name=loc_by_name,
            locations=locations,
        )
        if plan:
            scenes_built.append(plan)

    # ── Record day composition ────────────────────────────────────────────
    comp = DayComposition(
        sim_day=sim_day,
        day_archetype=archetype,
        day_label=archetype_label,
        required_slots_json=json.dumps(required_slots),
        actual_scenes_json=json.dumps([s.scene_type for s in scenes_built]),
        suppressed_pressures_json=json.dumps(suppressed),
        pair_cooldowns_json=json.dumps({str(k): v for k, v in pair_cooldowns.items()}),
        daybook_text=_generate_daybook(archetype, scenes_built, sim_day),
    )
    db.add(comp)
    db.commit()

    logger.info(
        f"  Composed {len(scenes_built)} scenes: "
        + ", ".join(s.scene_type for s in scenes_built)
        + f" | archetype: {archetype}"
    )

    return scenes_built, comp


def _choose_archetype(sim_day: int, db: Session) -> str:
    """Pick a day archetype, weighted by current world state."""
    # Check for food shortage
    from database.models import ResourcePool
    pools = db.query(ResourcePool).filter(ResourcePool.resource_type == "food").all()
    total_food = sum(p.quantity for p in pools) if pools else 10
    alive = db.query(Character).filter(Character.alive == True, Character.is_infant == False).count()
    days_left = total_food / max(alive, 1)

    if days_left < 1.5:
        return "hungry_day"

    # Check for recent argument dominance
    recent_types = _get_recent_scene_types(sim_day, db, days=5)
    argument_count = recent_types.count("argument") + recent_types.count("status_challenge")
    if argument_count >= 4:
        # Force a quieter day
        return random.choice(["ordinary_day", "repair_day", "pairing_day"])

    # Default weighted choice
    types, weights = zip(*DAY_ARCHETYPES)
    return random.choices(types, weights=weights, k=1)[0]


def _plan_required_slots(archetype: str, pressures: list[dict]) -> list[str]:
    """Plan what slot categories are required for this day."""
    # Base template
    slots = ["tension", "connection", "ambient"]

    # Archetype overrides
    if archetype in ("mourning_day", "repair_day"):
        slots = ["connection", "ambient", "ambient"]
    elif archetype in ("accusation_day", "reordering_day"):
        slots = ["tension", "tension", "consequence"]
    elif archetype == "pairing_day":
        slots = ["connection", "connection", "ambient"]
    elif archetype == "ritual_day":
        slots = ["connection", "ambient", "connection"]
    elif archetype == "storm_day":
        slots = ["tension", "connection", "ambient"]

    # Check if there's a strong open_question pressure — allow max 1
    oq_count = sum(1 for p in pressures if p["type"] == "open_question")
    # Already handled as max 1 via caps in _fill_slot

    return slots


def _fill_slot(
    slot_category: str,
    pressures: list[dict],
    scene_type_counts: dict,
    scene_bias: dict,
    pair_cooldowns: dict,
    location_cooldowns: dict,
    used_char_ids: set,
    sim_day: int,
    db: Session,
    loc_by_name: dict,
    locations: list,
) -> ScenePlan | None:
    """Attempt to fill one scene slot from the given category."""

    valid_scene_types = SLOT_CATEGORIES.get(slot_category, [])

    # Apply hard caps
    filtered_types = []
    for st in valid_scene_types:
        current_count = scene_type_counts.get(st, 0)
        # argument: max 1 per day
        if st == "argument" and current_count >= 1:
            continue
        # status_challenge: max 1 per day
        if st == "status_challenge" and current_count >= 1:
            continue
        # open_question-driven: handled upstream, skip if already in scene_type_counts
        filtered_types.append(st)

    if not filtered_types:
        return None

    # Weight by scene_bias (archetype modifier)
    weights = [scene_bias.get(st, 1) for st in filtered_types]

    # Find a pressure that maps to one of these scene types
    matching_pressures = []
    for pressure in pressures:
        p_type = pressure["type"]
        possible_scene_types = PRESSURE_TO_SCENES.get(p_type, [])

        # open_question: only allow 1 per day in total
        if p_type == "open_question":
            if scene_type_counts.get("_oq_used", 0) >= 1:
                continue

        for st in possible_scene_types:
            if st in filtered_types:
                matching_pressures.append((pressure, st, weights[filtered_types.index(st)]))
                break

    if matching_pressures:
        # Weight by pressure intensity × scene bias
        total_weight = sum(w for _, _, w in matching_pressures)
        r = random.random() * total_weight
        chosen_pressure, chosen_scene_type, _ = matching_pressures[0]
        for pressure, st, w in matching_pressures:
            r -= w
            if r <= 0:
                chosen_pressure, chosen_scene_type = pressure, st
                break
    else:
        # No matching pressure — use ambient/fallback
        chosen_pressure = {
            "type": "daily_life",
            "characters": [],
            "description": "ordinary life",
            "action_verb": "",
        }
        chosen_scene_type = random.choices(
            filtered_types,
            weights=weights,
            k=1
        )[0]

    # Cast the scene
    cast = _cast_scene_with_cooldowns(
        chosen_scene_type,
        chosen_pressure.get("characters", []),
        used_char_ids,
        pair_cooldowns,
        sim_day,
        db,
        subject_id=chosen_pressure.get("subject_id"),
    )
    if not cast:
        return None

    # Check for ambient scene types — these need special handling
    if chosen_scene_type.startswith("ambient_"):
        return _build_ambient_scene(
            chosen_scene_type, cast, loc_by_name, locations,
            sim_day, chosen_pressure
        )

    # Pick location with cooldown awareness
    location = _pick_location_with_cooldowns(
        chosen_scene_type, cast, loc_by_name, locations, location_cooldowns
    )

    # Build purpose and context
    purpose = SCENE_PURPOSES.get(chosen_scene_type, "")
    if chosen_pressure["type"] == "open_question" and chosen_pressure.get("question_text"):
        qt = chosen_pressure["question_text"]
        char_name = (chosen_pressure["characters"][0].given_name
                     if chosen_pressure.get("characters") else "This character")
        purpose = (
            f"{char_name} came here carrying an unresolved question: \"{qt}\" "
            f"This scene exists because that question is pressing on them.\n\n" + purpose
        )
        if "scene_type_counts" in dir():
            scene_type_counts["_oq_used"] = scene_type_counts.get("_oq_used", 0) + 1

    scene_context = _build_scene_context(
        chosen_scene_type, cast, location, chosen_pressure, sim_day
    )

    return ScenePlan(
        scene_type=chosen_scene_type,
        dramatic_purpose=purpose,
        characters=cast,
        location=location,
        pressure_type=chosen_pressure["type"],
        scene_context=scene_context,
        action_verb=chosen_pressure.get("action_verb", ""),
        is_group=len(cast) >= 3,
    )


def _cast_scene_with_cooldowns(
    scene_type: str,
    pressure_chars: list,
    used_ids: set,
    pair_cooldowns: dict,
    sim_day: int,
    db: Session,
    subject_id: int | None = None,
) -> list:
    """Cast a scene, respecting pair cooldowns."""
    # First try normal cast
    cast = _cast_scene(scene_type, pressure_chars, used_ids, sim_day, db, subject_id=subject_id)

    if len(cast) < 2:
        return cast

    # Check pair cooldown
    pair_key = tuple(sorted([cast[0].id, cast[1].id]))
    if pair_cooldowns.get(pair_key, 0) > 0:
        # Try to find alternative second character
        all_available = db.query(Character).filter(
            Character.alive == True,
            Character.is_infant == False,
            Character.id.notin_(used_ids),
            Character.id != cast[0].id,
        ).all()
        # Filter out cooldown pairs
        non_cooled = [c for c in all_available
                      if pair_cooldowns.get(tuple(sorted([cast[0].id, c.id])), 0) == 0]
        if non_cooled:
            cast = [cast[0], random.choice(non_cooled)]

    return cast


def _pick_location_with_cooldowns(
    scene_type: str,
    cast: list,
    loc_by_name: dict,
    locations: list,
    location_cooldowns: dict,
) -> object:
    """Pick location, avoiding overused ones."""
    from simulation.scene_selector import SCENE_LOCATIONS

    preferred_names = SCENE_LOCATIONS.get(scene_type, [])

    # Cap Central Square at 2 scenes/day
    max_uses = 2 if "Central Square" in preferred_names else 99

    # Filter out overused locations
    preferred_filtered = [
        name for name in preferred_names
        if location_cooldowns.get(name, 0) < max_uses
    ]

    if preferred_filtered:
        for name in preferred_filtered:
            loc = loc_by_name.get(name)
            if loc:
                return loc

    # Fall through to any non-overused location
    available = [l for l in locations if location_cooldowns.get(l.name, 0) < 3]
    if available:
        return random.choice(available)

    return random.choice(locations)


def _build_ambient_scene(
    scene_type: str,
    cast: list,
    loc_by_name: dict,
    locations: list,
    sim_day: int,
    pressure: dict,
) -> ScenePlan:
    """Build an ambient life scene — ordinary but meaningful."""

    # Ambient scene types and their contexts
    AMBIENT_CONTEXTS = {
        "ambient_meal": (
            "People are eating. Or trying to. The practical reality of having or not having food "
            "enough is in this space. Who shares with whom. Whether there is silence or talk. "
            "What the meal says about how things are between people right now."
        ),
        "ambient_labor": (
            "Work is happening. Physical, practical work. Hands busy. The conversation — "
            "if there is one — happens around the task, not instead of it. What people "
            "talk about when their bodies are occupied tells you something real."
        ),
        "ambient_care": (
            "Someone is attending to someone else. Not dramatically — just the small acts "
            "of noticing when someone needs something and providing it. This is how society "
            "holds itself together. Let that show."
        ),
        "ambient_boredom": (
            "Nothing is urgent right now. Two people exist in the same space without crisis "
            "driving them. This kind of stillness is rare and has its own texture. What "
            "comes out when there's nothing that has to be said?"
        ),
        "ambient_grooming": (
            "Bodies being maintained — hair, skin, simple hygiene. The intimacy of "
            "attending to the physical self, possibly with another person nearby. "
            "Bodies are real and this is a moment when that's most visible."
        ),
        "ambient_storytelling": (
            "Someone is recounting something. Not to resolve it — just to say it happened. "
            "The act of putting experience into words for someone else is how memory becomes "
            "shared history. What gets told, and how, shapes what the group remembers."
        ),
        "ambient_avoidance": (
            "Two people who have been avoiding each other have ended up in the same space. "
            "The avoidance is visible — in how they position themselves, what they look at, "
            "what they don't say. The silence between them is its own kind of conversation."
        ),
    }

    # Ambient location preferences
    AMBIENT_LOCATIONS = {
        "ambient_meal": ["Community Center", "Bayou Market", "Central Square", "Lakeview Flats"],
        "ambient_labor": ["The Workshop", "Warehouse Row", "Community Center"],
        "ambient_care": ["Lakeview Flats", "Riverside Park", "Community Center"],
        "ambient_boredom": ["Riverside Park", "Rooftop Garden", "Lakeview Flats"],
        "ambient_grooming": ["Riverside Park", "Lakeview Flats", "Rooftop Garden"],
        "ambient_storytelling": ["Community Center", "The Chapel", "Riverside Park"],
        "ambient_avoidance": ["Central Square", "Bayou Market", "Community Center"],
    }

    preferred = AMBIENT_LOCATIONS.get(scene_type, ["Riverside Park", "Community Center"])
    location = None
    for name in preferred:
        location = loc_by_name.get(name)
        if location:
            break
    if not location:
        location = random.choice(locations)

    context = AMBIENT_CONTEXTS.get(scene_type, "An ordinary moment in an ordinary day.")
    purpose = (
        "This scene is not about conflict. It's about the texture of living here together. "
        "Show what ordinary life actually looks like in Caldwell — the physical reality, "
        "the small negotiations, the habits forming, the ways people are (or aren't) at ease. "
        "Let the scene be what it is. Something can still change in it, but it doesn't have to. "
        "The reader should feel what a day is like here beyond the crises."
    )

    names = [c.given_name or c.roster_id for c in cast]
    name_str = " and ".join(names[:2]) if len(names) <= 2 else ", ".join(names[:2])
    scene_context = f"{name_str} at {location.name}. {context}"

    return ScenePlan(
        scene_type=scene_type,
        dramatic_purpose=purpose,
        characters=cast,
        location=location,
        pressure_type="daily_life",
        scene_context=scene_context,
        action_verb="",
        is_group=len(cast) >= 3,
    )


def _get_pair_cooldowns(sim_day: int, db: Session) -> dict:
    """Returns pairs that recently appeared in scenes together (3-day cooldown)."""
    cooldown_days = 3
    recent_scenes = db.query(Scene).filter(
        Scene.sim_day >= sim_day - cooldown_days
    ).all()

    cooldowns = {}
    for scene in recent_scenes:
        ids = json.loads(scene.participant_ids_json or '[]')
        if len(ids) >= 2:
            pair = tuple(sorted([ids[0], ids[1]]))
            days_ago = sim_day - scene.sim_day
            remaining = cooldown_days - days_ago
            cooldowns[pair] = max(cooldowns.get(pair, 0), remaining)

    return cooldowns


def _get_location_cooldowns(sim_day: int, db: Session) -> dict:
    """Returns locations used today already."""
    today_scenes = db.query(Scene).filter(Scene.sim_day == sim_day).all()
    counts: dict[str, int] = {}
    for scene in today_scenes:
        if scene.location_id:
            from database.models import Location as Loc
            loc = db.query(Loc).filter(Loc.id == scene.location_id).first()
            if loc:
                counts[loc.name] = counts.get(loc.name, 0) + 1
    return counts


def _get_recent_scene_types(sim_day: int, db: Session, days: int = 5) -> list[str]:
    """Returns list of recent scene types."""
    recent = db.query(Scene).filter(
        Scene.sim_day >= sim_day - days
    ).all()
    return [s.scene_type for s in recent]


def _generate_daybook(archetype: str, scenes: list[ScenePlan], sim_day: int) -> str:
    """Generate a one-paragraph daybook description."""
    label = ARCHETYPE_LABELS.get(archetype, "")
    scene_types = [s.scene_type for s in scenes]

    extras = []

    # Note any rhythm-driven scenes
    rhythm_names = [
        s.pressure_type.replace("rhythm_", "").replace("_", " ")
        for s in scenes if s.pressure_type.startswith("rhythm_")
    ]
    if rhythm_names:
        extras.append(f"The {rhythm_names[0]} came around again. It held.")

    if "quiet_intimacy" in scene_types:
        extras.append("Something private is forming between two people.")
    if "ritual" in scene_types:
        extras.append("A custom was observed today — quietly, but it was observed.")
    if "teaching" in scene_types:
        extras.append("Something passed from one person to another.")
    if "ambient_meal" in scene_types:
        extras.append("People ate together. The small negotiations of that were visible.")
    if "status_challenge" in scene_types:
        extras.append("Someone's standing is being tested.")

    if extras:
        return label + " " + " ".join(extras[:2])
    return label
