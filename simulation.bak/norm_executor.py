"""
norm_executor.py — executes actionable community norms each tick.

When characters agree to hunt, cook, build, or perform other regular
activities, those agreements get stored as actionable NormRecords.
This module runs each tick and generates actual action memories for
the characters who would be performing those activities — making norms
real rather than just talk.

Called from engine.py before conversations so actions can be referenced.
"""
import random
import logging
from sqlalchemy.orm import Session
from sqlalchemy import text
from database.models import Character, Memory, Location

logger = logging.getLogger("caldwell.norm_executor")

# Maps action verb → character selection criteria and output templates
ACTION_PROFILES = {
    "hunt": {
        "drives": ["Survival", "Curiosity", "Dominance"],
        "capability": "strength_score",
        "min_age": 16,
        "group_size": (2, 4),
        "frequency": 2,
        "locations": ["Warehouse Row", "The Outskirts", "Bayou Market"],
        "templates": [
            "I went out hunting today with {companions}. We moved through {location} tracking movement, following signs. {result}",
            "The hunting group went out early — me, {companions}. We spread out through {location}. {result}",
            "I hunted today with {companions}. The work is in the patience, the quiet, the watching. {result}",
        ],
        "results_good": [
            "We brought something back. Not much, but real.",
            "We found game and took it. The effort was worth it.",
            "We came back with food. The others will eat tonight.",
        ],
        "results_poor": [
            "We found nothing. We came back empty-handed and tired.",
            "Nothing today. The area has been picked over.",
            "We were out for hours and came back with less than we hoped.",
        ],
    },
    "fish": {
        "drives": ["Survival", "Curiosity", "Knowledge"],
        "capability": "memory_score",
        "min_age": 12,
        "group_size": (1, 3),
        "frequency": 2,
        "locations": ["Bayou Market", "The Outskirts"],
        "templates": [
            "I fished today near {location} with {companions}. {result}",
            "Spent the morning fishing with {companions}. {result}",
        ],
        "results_good": [
            "We pulled up enough to matter. The water gives when you wait.",
            "Good catch today. We brought back more than expected.",
        ],
        "results_poor": [
            "Very little. The water wasn't giving today.",
            "We fished for hours and came back with almost nothing.",
        ],
    },
    "cook": {
        "drives": ["Connection", "Comfort", "Order", "Belonging"],
        "capability": "memory_score",
        "min_age": 14,
        "group_size": (1, 2),
        "frequency": 1,
        "locations": ["Community Center", "Bayou Market"],
        "templates": [
            "I cooked today — took what was available and made something of it. {companions_text}{result}",
            "I spent time cooking. {companions_text}The smell of it moved through the space. {result}",
            "I cooked with {companions} today. {result}",
        ],
        "results_good": [
            "People ate. That feels like something.",
            "It wasn't much but it was warm and people came to it.",
            "There was enough for everyone who showed up.",
        ],
        "results_poor": [
            "The ingredients were sparse. I made what I could.",
            "Not enough to go around the way I wanted.",
        ],
    },
    "forage": {
        "drives": ["Curiosity", "Survival", "Knowledge"],
        "capability": "memory_score",
        "min_age": 12,
        "group_size": (1, 3),
        "frequency": 2,
        "locations": ["The Outskirts", "Bayou Market"],
        "templates": [
            "I went foraging today with {companions} through {location}. {result}",
            "We foraged out toward {location} — me and {companions}. {result}",
        ],
        "results_good": [
            "We found enough to bring back. More than I expected.",
            "Good haul. The knowledge of where to look is building.",
        ],
        "results_poor": [
            "Slim pickings. The easy spots are getting picked over.",
            "We worked hard and found little.",
        ],
    },
    "gather": {
        "drives": ["Order", "Survival", "Belonging"],
        "capability": "strength_score",
        "min_age": 13,
        "group_size": (2, 4),
        "frequency": 2,
        "locations": ["Bayou Market", "Community Center"],
        "templates": [
            "I helped gather and distribute today with {companions}. {result}",
            "We organized the gathering today — {companions} and me. {result}",
        ],
        "results_good": [
            "Things moved more fairly than they might have without us.",
            "People got what they needed. The system worked today.",
        ],
        "results_poor": [
            "There wasn't enough to make everyone happy. That's the reality.",
            "We tried to be fair but there's only so much to go around.",
        ],
    },
    "build": {
        "drives": ["Order", "Curiosity", "Survival", "Meaning"],
        "capability": "strength_score",
        "min_age": 15,
        "group_size": (2, 4),
        "frequency": 3,
        "locations": ["Warehouse Row", "Community Center", "The Outskirts"],
        "templates": [
            "I worked on building today with {companions} at {location}. {result}",
            "We spent time building — {companions} and me. {result}",
            "Worked with {companions} today on construction at {location}. {result}",
        ],
        "results_good": [
            "Something permanent exists now that didn't before.",
            "Progress. Slow but visible. We made the space more livable.",
            "Real work. You can see what we did when you look at it.",
        ],
        "results_poor": [
            "We hit problems. Slower than we wanted.",
            "The work is harder than it looks. But we made some progress.",
        ],
    },
    "repair": {
        "drives": ["Order", "Comfort", "Survival"],
        "capability": "memory_score",
        "min_age": 14,
        "group_size": (1, 3),
        "frequency": 3,
        "locations": ["Community Center", "Warehouse Row"],
        "templates": [
            "I spent time repairing things today with {companions}. {result}",
            "We worked on repairs — {companions} and me. {result}",
        ],
        "results_good": [
            "Things work better now. That matters.",
            "Fixed what was broken. The place is more functional.",
        ],
        "results_poor": [
            "Patched what we could. Some of it needs more than we have.",
        ],
    },
    "patrol": {
        "drives": ["Survival", "Dominance", "Order", "Tribalism"],
        "capability": "strength_score",
        "min_age": 16,
        "group_size": (2, 3),
        "frequency": 2,
        "locations": ["The Outskirts", "Warehouse Row"],
        "templates": [
            "I walked the perimeter today with {companions}. {result}",
            "We patrolled today — me and {companions}. Moving through {location}. {result}",
        ],
        "results_good": [
            "Nothing unusual. That's what you want.",
            "Quiet. The boundary feels like it means something when you walk it.",
        ],
        "results_poor": [
            "Signs of something we couldn't identify. We noted it.",
            "Unsettled. The perimeter feels bigger than we can cover.",
        ],
    },
    "teach": {
        "drives": ["Knowledge", "Meaning", "Connection", "Order"],
        "capability": "memory_score",
        "min_age": 18,
        "group_size": (1, 2),
        "frequency": 3,
        "locations": ["Community Center", "Bayou Market"],
        "templates": [
            "I spent time teaching today — sharing what I know with {companions}. {result}",
            "I worked with {companions} today, passing on what I've figured out. {result}",
        ],
        "results_good": [
            "Knowledge moved from me to someone else. That feels permanent.",
            "They understood. The gap between what they knew and what I know got smaller.",
        ],
        "results_poor": [
            "It's harder to teach than I expected. The knowledge feels obvious to me and foreign to them.",
        ],
    },
    "tend": {
        "drives": ["Connection", "Comfort", "Meaning", "Care"],
        "capability": "memory_score",
        "min_age": 14,
        "group_size": (1, 2),
        "frequency": 1,
        "locations": ["Community Center", "Bayou Market"],
        "templates": [
            "I tended to things today with {companions} — the maintenance work that doesn't announce itself. {result}",
            "I spent time tending today. {companions_text}{result}",
        ],
        "results_good": [
            "The place is a little more ordered than it was.",
            "Small work. But it accumulates.",
        ],
        "results_poor": [
            "There's more that needs doing than one day can hold.",
        ],
    },
}


def _get_actionable_norms(db: Session, sim_day: int) -> list:
    """Fetch active actionable norms that are due for execution."""
    try:
        result = db.execute(text(
            "SELECT id, norm_type, description, action_verb, "
            "action_frequency_days, last_executed_day, strength "
            "FROM norm_records "
            "WHERE is_actionable = 1 AND is_active = 1 "
            "ORDER BY strength DESC"
        )).fetchall()

        due = []
        for row in result:
            norm_id, norm_type, desc, action_verb, freq, last_exec, strength = row
            freq = freq or 2
            last_exec = last_exec or 0
            if sim_day - last_exec >= freq:
                due.append({
                    "id": norm_id,
                    "norm_type": norm_type,
                    "description": desc,
                    "action_verb": action_verb or norm_type.replace("action_", ""),
                    "frequency": freq,
                    "strength": strength or 0.1,
                })
        return due
    except Exception as e:
        logger.debug(f"No actionable norms found: {e}")
        return []


def _select_characters(db: Session, profile: dict, sim_day: int) -> list[Character]:
    """Select appropriate characters to perform this action."""
    preferred_drives = profile.get("drives", [])
    min_age = profile.get("min_age", 13)
    capability = profile.get("capability", "strength_score")
    group_min, group_max = profile.get("group_size", (1, 3))

    all_adults = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.age >= min_age,
    ).all()

    if not all_adults:
        return []

    # Score each character
    scored = []
    for char in all_adults:
        score = 0
        if char.core_drive in preferred_drives:
            score += 3
        cap_val = getattr(char, capability, 5) or 5
        score += cap_val * 0.3
        score += random.random() * 2  # some randomness
        scored.append((score, char))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Pick group size
    n = random.randint(group_min, min(group_max, len(scored)))
    return [char for _, char in scored[:n]]


def _format_companions(chars: list[Character], actor: Character) -> str:
    others = [c for c in chars if c.id != actor.id]
    if not others:
        return "alone"
    names = [c.given_name or c.physical_description[:20] for c in others]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _write_action_memory(
    char: Character,
    companions: list[Character],
    action_verb: str,
    profile: dict,
    sim_day: int,
    db: Session,
):
    """Write a vivid action memory for this character."""
    loc_name = random.choice(profile.get("locations", ["Caldwell"]))
    companions_str = _format_companions(companions, char)
    companions_text = f"I had help — {companions_str}. " if companions_str != "alone" else ""
    result = random.choice(
        profile["results_good"] if random.random() < 0.65 else profile["results_poor"]
    )
    template = random.choice(profile["templates"])

    try:
        action_text = template.format(
            companions=companions_str,
            companions_text=companions_text,
            location=loc_name,
            result=result,
        )
    except KeyError:
        action_text = f"I worked on {action_verb} today with {companions_str}. {result}"

    mem = Memory(
        character_id=char.id,
        sim_day=sim_day,
        memory_type="action",
        content=action_text,
        emotional_weight=0.65,
        is_inception=False,
    )
    db.add(mem)
    logger.info(
        f"NORM ACTION: {char.given_name or char.roster_id} "
        f"performed '{action_verb}' — {action_text[:60]}..."
    )


def _mark_executed(db: Session, norm_id: int, sim_day: int):
    db.execute(text(
        "UPDATE norm_records SET last_executed_day = :day WHERE id = :id"
    ), {"day": sim_day, "id": norm_id})


def _execute_norm_actions_internal(sim_day: int, db: Session) -> list[tuple]:
    """
    Main entry point — called each tick from engine.py before conversations.

    For each actionable norm that is due, selects characters and writes
    action memories so those activities appear as real events that
    conversations can reference.
    """
    due_norms = _get_actionable_norms(db, sim_day)
    scene_pairs = []
    if not due_norms:
        return scene_pairs

    for norm in due_norms:
        action_verb = norm["action_verb"]
        if not action_verb:
            continue

        profile = ACTION_PROFILES.get(action_verb)
        if not profile:
            logger.debug(f"No profile for action verb: {action_verb}")
            continue

        chars = _select_characters(db, profile, sim_day)
        if not chars:
            continue

        # Write memories for each participant
        for char in chars:
            _write_action_memory(char, chars, action_verb, profile, sim_day, db)

        _mark_executed(db, norm["id"], sim_day)

        # Queue scene conversations between participants
        if len(chars) >= 2:
            for i in range(len(chars) - 1):
                scene_pairs.append((
                    chars[i].roster_id,
                    chars[i + 1].roster_id,
                    action_verb,  # pass verb so engine can build scene
                ))

    db.commit()


def execute_norm_actions(sim_day: int, db: Session) -> list[tuple]:
    """
    Public entry point called from engine.py each tick.
    Wraps _execute_norm_actions_internal and returns scene pairs
    for norm-driven conversations.
    """
    return _execute_norm_actions_internal(sim_day, db)
