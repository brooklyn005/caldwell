"""
rhythms.py — recurring community rhythms.

Predictable cycles that give Caldwell social structure: hunt days, washing days,
storytelling nights, food-sorting, tool work, etc. The daily_composer checks
these when composing slot 2 (connection/care/labor/ambient) and prefers a
rhythm-driven scene if one is due.

Each rhythm defines:
  name           — identifier
  cadence        — fires every N sim_days
  offset         — first fires on day (offset), then every cadence thereafter
  scene_type     — scene type this rhythm produces
  location_name  — preferred location
  preferred_drives — core drives of characters who typically participate
  norm_reinforced — the social norm this rhythm builds over time
  scene_context  — physical/social framing injected into the prompt
  dramatic_purpose — prompt guidance for character behavior in this scene

No API calls. No DB table. Rule-based only.
"""
import logging
import random
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from database.models import Character, Location

logger = logging.getLogger("caldwell.rhythms")


@dataclass
class Rhythm:
    name: str
    cadence: int
    offset: int
    scene_type: str
    location_name: str
    preferred_drives: list
    norm_reinforced: str
    scene_context: str
    dramatic_purpose: str


RHYTHMS: list[Rhythm] = [
    Rhythm(
        name="hunt_day",
        cadence=4,
        offset=2,
        scene_type="ambient_labor",
        location_name="Riverside Park",
        preferred_drives=["Survival", "Dominance", "Connection"],
        norm_reinforced="collective_provision",
        scene_context=(
            "Hunt day — or what it has become. People go out together to find food, "
            "scout the edges, bring back what the land offers. The work is physical "
            "and shared, wordless in long stretches. Who goes and who stays is "
            "already understood without it being said."
        ),
        dramatic_purpose=(
            "This is a recurring day of shared provision. Show the work — the physical "
            "reality of searching for food together. What gets said during labor is "
            "often more honest than what gets said face to face. The rhythm of this "
            "day is part of how these people know each other."
        ),
    ),
    Rhythm(
        name="washing_day",
        cadence=3,
        offset=1,
        scene_type="ambient_grooming",
        location_name="Riverside Park",
        preferred_drives=["Connection", "Order", "Belonging"],
        norm_reinforced="bodily_care",
        scene_context=(
            "Washing day. People bring themselves and sometimes each other to the water. "
            "Hair, skin, clothing. The body's maintenance done in company — not quite "
            "private, not quite public. A recurring quiet intimacy that no one has "
            "formally named but everyone keeps returning to."
        ),
        dramatic_purpose=(
            "Show bodies being attended to — simply, practically. This is one of the "
            "rhythms Caldwell has settled into. The familiarity of it is part of the "
            "texture. Something said while washing tends to stay between the people there."
        ),
    ),
    Rhythm(
        name="storytelling_night",
        cadence=5,
        offset=4,
        scene_type="ambient_storytelling",
        location_name="The Chapel",
        preferred_drives=["Meaning", "Connection", "Curiosity"],
        norm_reinforced="oral_history",
        scene_context=(
            "Storytelling night. Someone tells something that happened — to them, "
            "to someone else, or to this place. Not a lesson, not a warning — just "
            "an accounting. The group gathers when this happens, or part of it does. "
            "What gets told becomes what gets remembered. What gets left out also matters."
        ),
        dramatic_purpose=(
            "This is how Caldwell builds its shared memory. Show the telling — what "
            "the speaker chooses to say, how the listener receives it. Let the story "
            "do something between the two people in the room. History is being made "
            "right now, even if it doesn't feel that way yet."
        ),
    ),
    Rhythm(
        name="food_sorting",
        cadence=2,
        offset=0,
        scene_type="distribution",
        location_name="Bayou Market",
        preferred_drives=["Order", "Survival", "Status"],
        norm_reinforced="fair_distribution",
        scene_context=(
            "Food sorting — the work of counting, dividing, deciding who gets what "
            "and how much. It happens every couple of days. There is no agreed rule "
            "yet. What has emerged is a pattern: certain people handle it, others "
            "watch, some don't show up until after. The tensions in this task are real."
        ),
        dramatic_purpose=(
            "This is where the community's values about fairness get tested against "
            "scarcity. Show the practical work and the social weight underneath it. "
            "Who controls the food controls something important, and everyone knows it "
            "even if no one has said it directly."
        ),
    ),
    Rhythm(
        name="teaching_circle",
        cadence=7,
        offset=6,
        scene_type="teaching",
        location_name="Caldwell Public Library",
        preferred_drives=["Curiosity", "Meaning", "Knowledge"],
        norm_reinforced="knowledge_transmission",
        scene_context=(
            "Teaching circle — something has been learned or figured out, and now "
            "someone is passing it on. Not formally. Just one person showing another "
            "how to do a thing, or explaining what they worked out. This happens "
            "once a week, roughly. It's become expected without anyone calling it that."
        ),
        dramatic_purpose=(
            "Show knowledge passing from one person to another. The teaching is real — "
            "a skill, an observation, a way of doing something. Let the teacher teach "
            "from what they actually know. Let the learner bring what they actually "
            "don't know. The relationship between them is made here, not just the knowledge."
        ),
    ),
    Rhythm(
        name="rooftop_gathering",
        cadence=6,
        offset=5,
        scene_type="ritual",
        location_name="Rooftop Garden",
        preferred_drives=["Meaning", "Connection", "Power"],
        norm_reinforced="communal_ritual",
        scene_context=(
            "Rooftop gathering — people come up here every few days for no agreed reason. "
            "The view. The air. The fact that it's above everything else. What happens "
            "here has started to feel like something. Not ceremony exactly. More like "
            "a place where the group is most aware it's a group."
        ),
        dramatic_purpose=(
            "This recurring meeting on the rooftop is becoming a ritual without being "
            "named one. Show what people do when they come here — what they say, what "
            "they look at, what the elevation does to how they speak. Something about "
            "this place makes people more honest, or more careful, or both."
        ),
    ),
    Rhythm(
        name="workshop_day",
        cadence=5,
        offset=2,
        scene_type="ambient_labor",
        location_name="The Workshop",
        preferred_drives=["Order", "Survival", "Meaning"],
        norm_reinforced="practical_skill_sharing",
        scene_context=(
            "Workshop day. Tools are being used — repaired, built, learned. "
            "The Workshop smells like effort. Two people working near each other "
            "on separate problems that occasionally become the same problem. "
            "This day has a recurring quality. People drift toward it."
        ),
        dramatic_purpose=(
            "Show the work. Hands doing things. A problem being solved through "
            "physical intelligence. What gets talked about in here tends to be direct — "
            "this space selects for that. Let the practical task and the conversation "
            "interrupt each other the way they actually do."
        ),
    ),
]


def get_due_rhythms(sim_day: int) -> list[Rhythm]:
    """Returns all rhythms that fall on this sim_day."""
    due = []
    for rhythm in RHYTHMS:
        if sim_day >= rhythm.offset and (sim_day - rhythm.offset) % rhythm.cadence == 0:
            due.append(rhythm)
    return due


def build_rhythm_scene_plan(
    rhythm: Rhythm,
    sim_day: int,
    db: Session,
    used_char_ids: set,
    pair_cooldowns: dict,
    loc_by_name: dict,
    locations: list,
):
    """
    Build a ScenePlan for a rhythm scene. Returns None if no valid cast exists.
    Imported inline to avoid circular import with scene_selector.
    """
    from simulation.scene_selector import ScenePlan

    # Pick location
    location = loc_by_name.get(rhythm.location_name)
    if not location:
        location = random.choice(locations) if locations else None
    if not location:
        return None

    # Cast: prefer characters with matching drives, skip used/infants
    all_alive = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.id.notin_(used_char_ids),
    ).all()

    if len(all_alive) < 2:
        return None

    preferred = [c for c in all_alive if c.core_drive in rhythm.preferred_drives]
    others = [c for c in all_alive if c.core_drive not in rhythm.preferred_drives]

    # Build pool: preferred first, shuffle within groups
    random.shuffle(preferred)
    random.shuffle(others)
    pool = preferred + others

    # Respect pair cooldowns: pick first valid pair
    char_a = pool[0]
    char_b = None
    for candidate in pool[1:]:
        pair_key = tuple(sorted([char_a.id, candidate.id]))
        if pair_cooldowns.get(pair_key, 0) == 0:
            char_b = candidate
            break
    if char_b is None and len(pool) > 1:
        char_b = pool[1]  # fall back to ignoring cooldown if no alternative

    if not char_b:
        return None

    cast = [char_a, char_b]
    names = [c.given_name or c.roster_id for c in cast]
    name_str = " and ".join(names)

    scene_context = f"{name_str} at {location.name}. {rhythm.scene_context}"
    dramatic_purpose = (
        f"RECURRING RHYTHM — {rhythm.name.replace('_', ' ').title()}:\n"
        f"{rhythm.dramatic_purpose}"
    )

    logger.info(
        f"  RHYTHM: {rhythm.name} — {name_str} at {location.name} "
        f"(reinforces: {rhythm.norm_reinforced})"
    )

    return ScenePlan(
        scene_type=rhythm.scene_type,
        dramatic_purpose=dramatic_purpose,
        characters=cast,
        location=location,
        pressure_type=f"rhythm_{rhythm.name}",
        scene_context=scene_context,
        action_verb="",
        is_group=False,
    )
