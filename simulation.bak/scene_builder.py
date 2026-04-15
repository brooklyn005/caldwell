"""
scene_builder.py — constructs physical scene contexts for conversations.

A scene describes what is physically happening right now at a location —
who is doing what, what it looks/smells/sounds like — so that conversations
happen INSIDE a physical moment rather than floating in abstract space.

Scenes are injected into both characters' prompts so they share the same
physical reality before the first word is spoken.
"""
import random
import logging
from sqlalchemy.orm import Session
from database.models import Character, Memory, Location

logger = logging.getLogger("caldwell.scene")


# ── Scene templates by activity ───────────────────────────────────────────────

ACTIVITY_SCENES = {
    "cook": [
        (
            "{actor} is cooking. The smell of it moves through the space — something hot, "
            "something real. Their hands are occupied. {observer_line}"
        ),
        (
            "{actor} is working over the food — cutting, arranging, tending to whatever "
            "is being prepared. {observer_line}"
        ),
        (
            "Food is being made. {actor} is doing the work of it — the actual physical "
            "labor that turns raw things into something people can eat. {observer_line}"
        ),
    ],
    "hunt": [
        (
            "The hunting group has just returned — {actor} among them. "
            "They are tired. There is something of the effort still on them. "
            "{result_line} {observer_line}"
        ),
        (
            "{actor} is preparing to go out. Checking what they need, assessing the route. "
            "There is purpose in their movement. {observer_line}"
        ),
        (
            "The group is out hunting — moving through the outskirts, watching, waiting. "
            "The work is quiet and physical. {observer_line}"
        ),
    ],
    "fish": [
        (
            "{actor} is fishing — sitting at the water's edge, line out, patient. "
            "The waiting is part of it. {observer_line}"
        ),
        (
            "{actor} has come back with fish. Not much, but something. "
            "The smell of the water is still on them. {observer_line}"
        ),
    ],
    "build": [
        (
            "{actor} is building something — physical work, deliberate. "
            "The sounds of it carry: scraping, moving, the weight of materials. "
            "{observer_line}"
        ),
        (
            "Construction work is happening. {actor} is part of it — "
            "hands occupied, attention on the task. {observer_line}"
        ),
        (
            "{actor} is working on a structure. The progress is visible but slow. "
            "Real labor. {observer_line}"
        ),
    ],
    "repair": [
        (
            "{actor} is fixing something that was broken. The work requires attention — "
            "careful hands, patience. {observer_line}"
        ),
        (
            "Repair work is happening. {actor} is assessing what's wrong and working to "
            "correct it. {observer_line}"
        ),
    ],
    "forage": [
        (
            "{actor} is moving through the area, looking carefully — foraging. "
            "Their eyes are on the ground and the edges of things. {observer_line}"
        ),
        (
            "{actor} has come back from foraging. There is something in their hands or nearby "
            "— what they found. {observer_line}"
        ),
    ],
    "gather": [
        (
            "{actor} is organizing and distributing — moving things, making decisions "
            "about who gets what. {observer_line}"
        ),
        (
            "The gathering and distribution of food is happening. {actor} is at the center "
            "of it. {observer_line}"
        ),
    ],
    "patrol": [
        (
            "{actor} is moving through the perimeter — alert, watching. "
            "The patrol is deliberate, methodical. {observer_line}"
        ),
        (
            "{actor} has just come back from checking the edges of the place. "
            "There's a specific alertness in how they hold themselves. {observer_line}"
        ),
    ],
    "teach": [
        (
            "{actor} is teaching — explaining something, demonstrating, passing knowledge "
            "to someone who doesn't have it yet. {observer_line}"
        ),
        (
            "Knowledge is being transferred. {actor} is doing the teaching — "
            "patient, deliberate. {observer_line}"
        ),
    ],
    "tend": [
        (
            "{actor} is tending — maintaining something that needs regular attention. "
            "Quiet, necessary work. {observer_line}"
        ),
    ],
}

OBSERVER_LINES = {
    "watching": [
        "You are watching.",
        "You stopped to watch.",
        "You are nearby, watching without making it obvious.",
        "You came in while this was happening and stayed.",
    ],
    "helping": [
        "You are helping — your hands are in it too.",
        "You joined in. The work is shared now.",
        "You stepped in to help without being asked.",
    ],
    "arriving": [
        "You just arrived. This is what you walked into.",
        "You came by and found this happening.",
        "You showed up and this is what's in front of you.",
    ],
    "together": [
        "You are both doing this together.",
        "The work is shared between you.",
        "You're working on this side by side.",
    ],
}

RESULT_LINES = {
    "good": [
        "They brought something back.",
        "The effort paid off.",
        "There's something to show for it.",
    ],
    "poor": [
        "They came back with less than hoped.",
        "The effort produced little.",
        "Empty-handed, mostly.",
    ],
}

LOCATION_SENSORY = {
    "Community Center": "The air inside is warmer than outside. Voices carry.",
    "Bayou Market": "The smell of the place — old food, air, people — is familiar now.",
    "Warehouse Row": "The shadows here are deep. Things scrape and echo.",
    "The Outskirts": "The light is different out here. More open. More exposed.",
    "Residences": "This is someone's space. That's felt the moment you enter.",
    "The Rooftop": "The view out here is the whole place. Wind moves through it.",
}


def build_scene_from_activity(
    actor: Character,
    observer: Character,
    action_verb: str,
    location: Location,
    relationship: str = "watching",
) -> str | None:
    """
    Build a scene description for a conversation that happens during or
    immediately after an activity.

    actor: the character performing the action
    observer: the character who is watching/helping/arriving
    action_verb: hunt, cook, build, etc.
    location: where this is happening
    relationship: watching / helping / arriving / together
    """
    templates = ACTIVITY_SCENES.get(action_verb)
    if not templates:
        return None

    template = random.choice(templates)
    actor_name = actor.given_name or actor.physical_description[:30]

    observer_options = OBSERVER_LINES.get(relationship, OBSERVER_LINES["watching"])
    observer_line = random.choice(observer_options)

    result_line = random.choice(
        RESULT_LINES["good"] if random.random() < 0.6 else RESULT_LINES["poor"]
    )

    try:
        scene = template.format(
            actor=actor_name,
            observer_line=observer_line,
            result_line=result_line,
        )
    except KeyError:
        scene = f"{actor_name} is {action_verb}ing. {observer_line}"

    # Add location sensory detail
    loc_name = location.name if location else ""
    sensory = LOCATION_SENSORY.get(loc_name, "")
    if sensory:
        scene = sensory + " " + scene

    return scene.strip()


def build_scene_from_memories(
    char_a: Character,
    char_b: Character,
    sim_day: int,
    db: Session,
) -> tuple[str | None, str | None]:
    """
    Look for action memories from today for either character.
    If found, build a scene where one is the actor and one is the observer.
    Returns (scene_description, activity_topic_seed) or (None, None).
    """
    # Check if either character has an action memory from today
    for actor, observer in [(char_a, char_b), (char_b, char_a)]:
        action_mem = (
            db.query(Memory)
            .filter(
                Memory.character_id == actor.id,
                Memory.sim_day == sim_day,
                Memory.memory_type.in_(["action", "directive_executed"]),
            )
            .order_by(Memory.emotional_weight.desc())
            .first()
        )

        if not action_mem:
            continue

        content = action_mem.content.lower()

        # Detect which activity
        verb = None
        activity_keywords = {
            "cook": ["cook", "cooking", "food", "meal", "prepare"],
            "hunt": ["hunt", "hunting", "game", "prey", "tracking"],
            "build": ["build", "building", "construct", "structure"],
            "repair": ["repair", "fix", "fixing", "mend"],
            "fish": ["fish", "fishing", "catch"],
            "forage": ["forag", "gather", "search"],
            "patrol": ["patrol", "perimeter", "watch", "guard"],
            "teach": ["teach", "teaching", "explain", "show"],
            "tend": ["tend", "tending", "maintain"],
        }
        for v, keywords in activity_keywords.items():
            if any(kw in content for kw in keywords):
                verb = v
                break

        if not verb:
            continue

        loc = db.query(Location).filter(
            Location.id == actor.current_location_id
        ).first()

        # Determine relationship
        relationship = random.choice(["watching", "arriving", "together"])

        scene = build_scene_from_activity(actor, observer, verb, loc, relationship)
        if not scene:
            continue

        # Build activity topic seed
        actor_name = actor.given_name or actor.physical_description[:30]
        observer_name = observer.given_name or observer.physical_description[:30]

        if relationship == "together":
            topic = (
                f"You are doing this work together right now — the {verb}ing is happening. "
                f"Your hands are occupied. {observer_name} is here with you, doing their part. "
                f"This is a chance to talk while you work — say what's on your mind."
            )
        elif relationship == "watching":
            topic = (
                f"You are watching {actor_name} {verb}. "
                f"They are occupied with the work. You are here, watching it happen. "
                f"You could stay quiet, or you could say something."
            )
        else:  # arriving
            topic = (
                f"You just arrived and found {actor_name} in the middle of {verb}ing. "
                f"This is what you walked into. The activity is already in progress."
            )

        return scene, topic

    return None, None


def build_directive_scene(
    director: Character,
    executor: Character,
    directive_content: str,
    location: Location,
) -> tuple[str | None, str | None]:
    """
    Build a scene where executor is carrying out a directive from director.
    Director may be present to observe.
    """
    director_name = director.given_name or director.physical_description[:30]
    executor_name = executor.given_name or executor.physical_description[:30]
    loc_name = location.name if location else "here"

    # Extract the action from the directive
    content_lower = directive_content.lower()
    verb = None
    for v in ["cook", "hunt", "build", "fish", "forage", "repair", "patrol", "gather", "tend"]:
        if v in content_lower:
            verb = v
            break

    sensory = LOCATION_SENSORY.get(loc_name, "")

    if verb:
        scene = (
            f"{sensory} "
            f"{executor_name} is {verb}ing — doing what {director_name} asked. "
            f"The work is happening. {director_name} is here, watching it be done."
        ).strip()
        topic_for_director = (
            f"You told {executor_name} to {verb}. They are doing it now, right in front of you. "
            f"You are watching. What do you think? What do you say?"
        )
        topic_for_executor = (
            f"{director_name} told you to {verb} and you are doing it. "
            f"They are watching you work. The task is in your hands. "
            f"What goes through your mind? Do you say anything while you work?"
        )
        return scene, topic_for_executor
    else:
        scene = (
            f"{sensory} "
            f"{executor_name} is carrying out something {director_name} asked of them. "
            f"The work is happening. {director_name} is present."
        ).strip()
        topic_for_executor = (
            f"{director_name} asked something of you and you're doing it. "
            f"They're here, watching. What goes through your mind?"
        )
        return scene, topic_for_executor
