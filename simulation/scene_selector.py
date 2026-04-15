"""
scene_selector.py — maps daily pressures to scene types and casts them.

Each scene has:
  - a type (preparation, return, argument, distribution, etc.)
  - a cast (specific characters with stakes in this moment)
  - a location
  - a dramatic purpose injected into the system prompt
  - a scene_context string that grounds the conversation physically

Called from engine.py after pressure_selector runs.
"""
import logging
import random
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from database.models import Character, Location, CharacterRelationship

logger = logging.getLogger("caldwell.scene_selector")


@dataclass
class ScenePlan:
    scene_type: str
    dramatic_purpose: str
    characters: list  # list[Character]
    location: object  # Location
    pressure_type: str
    scene_context: str = ""
    action_verb: str = ""
    is_group: bool = False        # True when 3+ characters
    is_injected: bool = False     # True when operator-injected


# Maps pressure type → scene type(s) to generate
PRESSURE_TO_SCENES = {
    "environmental_crisis":  ["argument", "preparation"],
    "food_shortage":         ["distribution", "argument"],
    "hunt_return":           ["return"],
    "norm_day_hunt":         ["preparation"],
    "norm_day_build":        ["preparation"],
    "norm_day_patrol":       ["preparation"],
    "norm_day_cook":         ["preparation"],
    "norm_day_gather":       ["distribution"],
    "norm_day_teach":        ["teaching"],
    "norm_day_fish":         ["preparation"],
    "labor_resentment":      ["resentment", "argument"],
    "relationship_tension":  ["argument", "correction"],
    "pair_bond":             ["quiet_intimacy", "gossip"],
    "status_challenge":      ["status_challenge", "correction"],
    "open_question":         ["gossip", "teaching", "argument"],  # overridden by scene_hint
    "daily_life":            ["teaching", "gossip", "argument"],
}

# Location affinities per scene type
SCENE_LOCATIONS = {
    "preparation":     ["Warehouse Row", "Community Center", "The Workshop"],
    "return":          ["Community Center", "Central Square", "Bayou Market"],
    "distribution":    ["Community Center", "Bayou Market", "Central Square"],
    "argument":        ["Central Square", "Community Center", "Riverside Park"],
    "correction":      ["Community Center", "Central Square"],
    "resentment":      ["Lakeview Flats", "Riverside Park", "The Meridian"],
    "quiet_intimacy":  ["Riverside Park", "Rooftop Garden", "Lakeview Flats", "The Chapel"],
    "gossip":          ["Riverside Park", "Rooftop Garden", "Bayou Market"],
    "teaching":        ["The Schoolhouse", "Community Center", "Caldwell Public Library"],
    "status_challenge":["Central Square", "Community Center"],
    "ritual":          ["The Chapel", "Central Square", "Rooftop Garden"],
}

SCENE_PURPOSES = {
    "preparation": (
        "Something is about to happen and everyone in this scene knows it. "
        "One or more of you is about to go out and do something hard. "
        "The conversation is happening in the space between deciding and going. "
        "What gets said before someone leaves shapes what they carry with them. "
        "Be specific about what you are about to do and what it costs you."
    ),
    "return": (
        "Someone has just come back. The return itself is the event. "
        "Did they bring what was needed? Did they fail? Did something happen out there? "
        "The people who waited have their own state — relief, resentment, worry, hunger. "
        "What was brought back — or not — will change things. Let that show."
    ),
    "distribution": (
        "There is something to be divided and not everyone will get what they want. "
        "Who controls what gets given? Who is watching? Who is hungry and quiet about it? "
        "Who pushes? Fairness is not yet a settled concept here — it is being decided right now, "
        "in this moment, by what you do."
    ),
    "argument": (
        "Something is genuinely wrong and at least one person in this scene knows it. "
        "This is not a discussion. This is a confrontation looking for a form. "
        "People don't always argue cleanly — they circle, they deflect, they go quiet, "
        "they say something else when they mean something harder. "
        "Let the real thing surface slowly, or not at all."
    ),
    "correction": (
        "Something happened that wasn't supposed to. Or someone did something "
        "that others expected them not to. The correction doesn't have to be loud — "
        "it can be a look, a pause, a repeated question. "
        "The person being corrected knows it. How they respond will be remembered."
    ),
    "resentment": (
        "One person in this scene is carrying something they haven't said directly. "
        "They may not say it now either. But it is shaping everything — how they stand, "
        "what they respond to, what they don't respond to. "
        "The other person may or may not know. Let the weight of the unsaid thing be real."
    ),
    "quiet_intimacy": (
        "Nothing is urgent. These two people have ended up near each other without crisis driving it. "
        "The usual social performance is down. What gets said here is different from what gets said in public. "
        "It can be warm, careful, honest, or tentative — but it is real and specific to who they are. "
        "Physical closeness may or may not happen — what matters is the lowered guard, not the outcome."
    ),
    "gossip": (
        "Someone is not here. That's the point. "
        "The person being talked about has a reputation that is being made or remade "
        "in their absence. What the people in this scene believe about them — "
        "whether it is accurate or not — will shape how they are treated next time. "
        "This is how social reality gets built."
    ),
    "teaching": (
        "One person knows something the other doesn't. The gap between them is the scene. "
        "Teaching is not just information transfer — it is a relationship. "
        "Who has patience? Who gets frustrated? What does the learner reveal "
        "about themselves by how they receive what is being offered?"
    ),
    "status_challenge": (
        "Someone's standing is being questioned — maybe by someone else, maybe by events. "
        "Status here is not a title. It's what people assume about you when you walk in. "
        "It can be gained or lost through a single exchange if that exchange is witnessed. "
        "Who is watching this scene matters as much as what is said in it."
    ),
    "ritual": (
        "This is a behavior that has been repeated enough times that it has acquired weight. "
        "It doesn't need to be explained. Everyone here knows what this moment is, "
        "even if they can't name it. Perform it with the gravity it has earned. "
        "Or resist it — but know what you're resisting."
    ),
}


def select_scenes_for_day(
    pressures: list[dict],
    sim_day: int,
    db: Session,
    max_scenes: int = 3,
    exclude_ids: set | None = None,
) -> list[ScenePlan]:
    """
    Takes the day's pressures and returns a list of ScenePlans.
    Hard cap: max_scenes total.
    exclude_ids: character IDs already committed to injected scenes.
    """
    scenes = []
    used_char_ids: set[int] = set(exclude_ids or {})
    locations = db.query(Location).all()
    loc_by_name = {l.name: l for l in locations}

    for pressure in pressures:
        if len(scenes) >= max_scenes:
            break

        p_type = pressure["type"]
        scene_types = PRESSURE_TO_SCENES.get(p_type, ["daily_life"])

        # open_question pressure carries a scene_hint based on the question type
        if p_type == "open_question" and pressure.get("scene_hint"):
            scene_type = pressure["scene_hint"]
        else:
            scene_type = scene_types[0]

        purpose = SCENE_PURPOSES.get(scene_type, "")

        # For open_question scenes, inject the actual question into the dramatic purpose
        if p_type == "open_question" and pressure.get("question_text"):
            qt = pressure["question_text"]
            char_name = pressure["characters"][0].given_name if pressure["characters"] else "This character"
            purpose = (
                f"{char_name} came here carrying an unresolved question: \"{qt}\" "
                f"This scene exists because that question is pressing on them. "
                f"They are looking for an opening — a detail, a reaction, something that gets them closer. "
                f"They may or may not ask directly. But the question is alive in them right now.\n\n"
                + purpose
            )

        cast = _cast_scene(
            scene_type, pressure["characters"], used_char_ids, sim_day, db,
            subject_id=pressure.get("subject_id"),
        )
        if not cast:
            continue

        location = _pick_location(scene_type, cast, loc_by_name, locations)
        purpose = SCENE_PURPOSES.get(scene_type, "")
        scene_context = _build_scene_context(
            scene_type, cast, location, pressure, sim_day
        )

        plan = ScenePlan(
            scene_type=scene_type,
            dramatic_purpose=purpose,
            characters=cast,
            location=location,
            pressure_type=p_type,
            scene_context=scene_context,
            action_verb=pressure.get("action_verb", ""),
            is_group=len(cast) >= 3,
        )
        scenes.append(plan)
        used_char_ids.update(c.id for c in cast)

        # Some pressures generate a second scene (e.g. gossip after return)
        if len(scenes) < max_scenes and len(scene_types) > 1:
            second_type = scene_types[1]
            second_cast = _cast_second_scene(
                second_type, cast, used_char_ids, db
            )
            if second_cast:
                second_loc = _pick_location(second_type, second_cast, loc_by_name, locations)
                second_purpose = SCENE_PURPOSES.get(second_type, "")
                second_context = _build_scene_context(
                    second_type, second_cast, second_loc, pressure, sim_day
                )
                scenes.append(ScenePlan(
                    scene_type=second_type,
                    dramatic_purpose=second_purpose,
                    characters=second_cast,
                    location=second_loc,
                    pressure_type=p_type,
                    scene_context=second_context,
                    action_verb=pressure.get("action_verb", ""),
                    is_group=len(second_cast) >= 3,
                ))
                used_char_ids.update(c.id for c in second_cast)

    logger.info(
        f"  Selected {len(scenes)} scenes: "
        + ", ".join(f"{s.scene_type}({len(s.characters)})" for s in scenes)
    )
    return scenes


# ── Casting logic ─────────────────────────────────────────────────────────────

def _cast_scene(
    scene_type: str,
    pressure_chars: list,
    used_ids: set[int],
    sim_day: int,
    db: Session,
    subject_id: int | None = None,
) -> list:
    """
    Cast a scene from pressure characters, avoiding already-used ids.
    Prioritizes characters who are co-located.
    For confrontation scenes (resentment, correction, status_challenge),
    guarantees the subject character a slot and makes the scene a trio so
    they can actually speak for themselves.
    """
    available = [c for c in pressure_chars if c.id not in used_ids]

    if len(available) < 2:
        extras = db.query(Character).filter(
            Character.alive == True,
            Character.is_infant == False,
            Character.id.notin_(used_ids),
        ).all()
        random.shuffle(extras)
        available = available + extras

    if len(available) < 2:
        return []

    # For confrontation scenes: ensure the subject is in the cast and speaks first
    # Subject = the person being sought, confronted, or corrected
    CONFRONTATION_SCENES = {"resentment", "correction", "status_challenge"}
    if scene_type in CONFRONTATION_SCENES and subject_id:
        subject = next((c for c in available if c.id == subject_id), None)
        if subject:
            # Subject goes first (they need a voice), observers follow
            others = [c for c in available if c.id != subject_id]
            available = [subject] + others

    # Build a location -> characters map from all available
    loc_groups: dict[int, list] = {}
    for c in available:
        if c.current_location_id:
            loc_groups.setdefault(c.current_location_id, []).append(c)

    pressure_ids = {c.id for c in pressure_chars}
    best_group = []
    for loc_id, group in loc_groups.items():
        has_pressure = any(c.id in pressure_ids for c in group)
        if has_pressure and len(group) > len(best_group):
            best_group = group

    if len(best_group) >= 2:
        # Preserve subject ordering when merging with co-located group
        seen = {c.id for c in best_group}
        available = best_group + [c for c in available if c.id not in seen]

    # Scene-type-specific sizing
    if scene_type in CONFRONTATION_SCENES:
        # Trio: subject + up to 2 observers — everyone gets a voice
        return available[:min(3, len(available))]
    elif scene_type in ("quiet_intimacy", "argument"):
        return available[:2]
    elif scene_type in ("gossip",):
        return available[:2]
    elif scene_type in ("preparation", "return", "distribution"):
        return available[:min(4, len(available))]
    elif scene_type in ("teaching",):
        return available[:2]
    else:
        return available[:min(3, len(available))]


def _cast_second_scene(
    scene_type: str,
    primary_cast: list,
    used_ids: set[int],
    db: Session,
) -> list:
    """Cast a secondary scene — often people reacting to the primary."""
    available = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.id.notin_(used_ids),
    ).all()
    if len(available) < 2:
        return []
    random.shuffle(available)

    if scene_type in ("quiet_intimacy", "gossip", "resentment"):
        return available[:2]
    return available[:min(3, len(available))]


# ── Location picking ──────────────────────────────────────────────────────────

def _pick_location(
    scene_type: str,
    cast: list,
    loc_by_name: dict,
    locations: list,
) -> object:
    preferred_names = SCENE_LOCATIONS.get(scene_type, [])

    # First: find a location where multiple cast members already are
    if len(cast) >= 2:
        loc_counts: dict[int, int] = {}
        for c in cast:
            if c.current_location_id:
                loc_counts[c.current_location_id] = loc_counts.get(c.current_location_id, 0) + 1
        # Any location with 2+ cast members wins
        shared = [lid for lid, count in loc_counts.items() if count >= 2]
        if shared:
            from database.models import Location as Loc
            # prefer a shared location that's also in the preferred list
            for name in preferred_names:
                loc = loc_by_name.get(name)
                if loc and loc.id in shared:
                    return loc
            # any shared location
            for loc in locations:
                if loc.id in shared:
                    return loc

    # Second: a preferred location where cast[0] already is
    if cast:
        char_loc_id = cast[0].current_location_id
        if char_loc_id:
            for name in preferred_names:
                loc = loc_by_name.get(name)
                if loc and loc.id == char_loc_id:
                    return loc

    # Fall back to any preferred location
    for name in preferred_names:
        loc = loc_by_name.get(name)
        if loc:
            return loc

    return random.choice(locations)


# ── Scene context builder ─────────────────────────────────────────────────────

def _build_scene_context(
    scene_type: str,
    cast: list,
    location: object,
    pressure: dict,
    sim_day: int,
) -> str:
    """
    Builds the physical scene context injected into the system prompt.
    This is what IS HAPPENING RIGHT NOW — not backstory, not summary.
    """
    names = [c.given_name or c.physical_description[:30] for c in cast]
    name_str = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + f", and {names[-1]}"
    loc_name = location.name if location else "here"
    verb = pressure.get("action_verb", "")

    contexts = {
        "preparation": (
            f"{name_str} at {loc_name}. "
            f"{'A hunt' if verb == 'hunt' else 'Work'} is happening {'today' if verb else 'soon'}. "
            f"Bodies in the act of getting ready — checking what they have, what they'll need, "
            f"what the others are doing. The going is almost here."
        ),
        "return": (
            f"{name_str} at {loc_name}. "
            f"{'The hunters have just come back' if verb == 'hunt' else 'They have just returned'}. "
            f"Whatever they brought — or didn't — is already present in the space. "
            f"People are looking at what was carried in. The tiredness is visible."
        ),
        "distribution": (
            f"{name_str} at {loc_name}. "
            f"There is food — or what passes for it. "
            f"Someone is controlling who gets what and in what order. "
            f"The others are waiting. Everyone is tracking the portions."
        ),
        "argument": (
            f"{name_str} at {loc_name}. "
            f"Something is wrong between these two — or wrong in general and they are the ones in the room. "
            f"The tension has been building. Right now they are in the same space and it has no way to not be present."
        ),
        "correction": (
            f"{name_str} at {loc_name}. "
            f"One person did something that the other believes they should not have done. "
            f"The correction is happening now — in words, in silence, in how they are standing."
        ),
        "resentment": (
            f"{name_str} at {loc_name}. "
            f"One of them is carrying something they haven't said. "
            f"The other may or may not know. The weight of the unsaid thing is in the room."
        ),
        "quiet_intimacy": (
            f"{name_str} at {loc_name}. "
            f"They have ended up in the same place without crisis driving it. "
            f"Something between them has been building — not necessarily romantic, "
            f"but real. A moment where the usual social distance is smaller than usual."
        ),
        "gossip": (
            f"{name_str} at {loc_name}. "
            f"Someone who is not here is being discussed. "
            f"What gets said about them now will outlast this conversation."
        ),
        "teaching": (
            f"{name_str} at {loc_name}. "
            f"One of them knows something the other is trying to learn. "
            f"The transfer is happening right now — slow, specific, not always clean."
        ),
        "status_challenge": (
            f"{name_str} at {loc_name}. "
            f"Someone's standing is being tested — by words, by what was done, "
            f"by who is watching. The outcome of this conversation will be remembered."
        ),
        "ritual": (
            f"{name_str} at {loc_name}. "
            f"A repeated behavior is being performed again. It has weight now. "
            f"People know what this moment is even without a name for it."
        ),
    }

    return contexts.get(scene_type, f"{name_str} at {loc_name}.")
