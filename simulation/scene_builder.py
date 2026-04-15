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


def build_embodied_scene_frame(
    scene_type: str,
    char_a: Character,
    char_b: Character,
    location: Location,
) -> str:
    """
    Engine-authored physical opening beat — injected as the first 'narrator'
    entry in exchanges before char_a speaks. Rule-based, no API calls.

    Establishes: who is standing where, what their hands are doing,
    what the light and smell is. Characters speak INTO this, not before it.
    """
    name_a = char_a.given_name or "one of them"
    name_b = char_b.given_name or "the other"
    loc_name = location.name if location else "here"

    SENSORY = {
        "Riverside Park":         "The light is low and directional through the vegetation. The air smells of water and green things.",
        "Rooftop Garden":         "Wind moves through up here. The whole settlement is visible below. The sky is larger than it seems from the ground.",
        "Community Center":       "The air inside carries warmth from bodies and cooking. Sounds land differently in this space — softer at the edges, louder in the middle.",
        "Bayou Market":           "The smell of stored food, damp wood, and the accumulated traces of many people passing through. The light is cut by posts and stalls.",
        "The Workshop":           "The smell of worked material — metal shavings, sawdust, something burned earlier in the day. The floor is marked with use.",
        "Warehouse Row":          "The shadows here are deep and the light falls in columns where the roof allows it. Sounds echo off the walls before they die.",
        "The Chapel":             "The space holds quiet differently from outside. Light comes from above — a high window, or a gap that was made to let something in.",
        "Central Square":         "The space is open and exposed. There is no angle from which this conversation is private. Anyone who crosses the square can see it.",
        "The Schoolhouse":        "The chairs are arranged with intention. The room carries the sense of things being taught and received here before.",
        "Caldwell Public Library": "The smell of old paper and enclosed space. The quiet in here is its own kind of weight, accumulated over time.",
        "Lakeview Flats":         "The light is flat and wide — no walls to catch it. The sound of the lake is close, just beneath the other sounds.",
        "The Meridian":           "The center of the settlement. Things converge here. Being here means being in the middle of whatever is happening.",
    }
    sensory = SENSORY.get(loc_name, f"They are at {loc_name}.")

    FRAMES = {
        "argument": (
            f"{sensory} "
            f"{name_a} is on their feet. Not pacing — standing, which is different. "
            f"Their hands are at their sides or gripping something nearby. "
            f"{name_b} is close enough that the distance between them is a choice someone has made. "
            f"Whatever is about to be said has been building toward this."
        ),
        "status_challenge": (
            f"{sensory} "
            f"Both of them are here and both of them know what it means that they are. "
            f"{name_a} has their weight distributed evenly — feet apart, hands visible, nothing fidgeting. "
            f"The kind of stillness that says: I am not moving from this."
        ),
        "quiet_intimacy": (
            f"{sensory} "
            f"{name_a} and {name_b} have arrived here without the pressure of anything urgent. "
            f"{name_a}'s hands are at rest. The distance between them is smaller than it would be with someone they knew less well. "
            f"Neither of them has made a point of that."
        ),
        "teaching": (
            f"{sensory} "
            f"{name_a} has something in their hands — the object, the material, or the tool the teaching concerns. "
            f"{name_b} is positioned to watch and receive. "
            f"The space between them is working space, arranged for transfer."
        ),
        "gossip": (
            f"{sensory} "
            f"{name_a} and {name_b} are closer together than the space requires. "
            f"Their voices are lower than they need to be. "
            f"Someone is not here, and that absence is the subject of this conversation."
        ),
        "resentment": (
            f"{sensory} "
            f"{name_a}'s hands are occupied — doing something, keeping busy in the way that keeps feeling at a manageable distance. "
            f"{name_b} is present, which is the problem. "
            f"The unsaid thing is taking up most of the available room."
        ),
        "correction": (
            f"{sensory} "
            f"{name_a} has gone still. Their hands have stopped moving. "
            f"They are looking at {name_b} in a way that is not incidental — "
            f"the kind of looking that has a purpose and isn't pretending otherwise."
        ),
        "ritual": (
            f"{sensory} "
            f"Both of them have been in this space before and they both carry that. "
            f"Their bodies remember the previous times — where to stand, what direction to face, "
            f"how long the quiet goes before someone opens it."
        ),
        "distribution": (
            f"{sensory} "
            f"There is something here to be divided. {name_a} is positioned at the center of it — "
            f"hands near the supply, eyes moving across the people present. "
            f"{name_b} is among those waiting. Everyone is tracking the portions."
        ),
        "preparation": (
            f"{sensory} "
            f"{name_a}'s hands are busy — checking, organizing, assembling what will be needed. "
            f"The body is already working toward what comes next. "
            f"{name_b} is here in their own version of the same readying."
        ),
        "return": (
            f"{sensory} "
            f"{name_a} has just come back. The effort is still on them — "
            f"in the set of their shoulders, how they're carrying themselves, "
            f"what they've put down or are still holding. "
            f"{name_b} has been here. Waiting, whether that word would be admitted or not."
        ),
    }

    if scene_type.startswith("ambient_"):
        sub = scene_type.replace("ambient_", "")
        AMBIENT_FRAMES = {
            "meal": (
                f"{sensory} "
                f"{name_a} and {name_b} are eating, or near food that's being eaten. "
                f"Hands are involved — carrying, dividing, bringing something to the mouth. "
                f"The practical work of consuming is what's happening right now."
            ),
            "labor": (
                f"{sensory} "
                f"{name_a} is working. Their hands are doing something specific and unfinished. "
                f"{name_b} is nearby — working in parallel, or watching the work happen."
            ),
            "care": (
                f"{sensory} "
                f"One of them is attending to the other. The gesture is practical, not theatrical. "
                f"{name_a} is positioned to help. {name_b} is in the position of receiving it."
            ),
            "grooming": (
                f"{sensory} "
                f"Bodies are being tended to. {name_a}'s hands are occupied — "
                f"on their own body or near {name_b}'s. "
                f"This kind of proximity has a specific intimacy that doesn't require naming."
            ),
            "storytelling": (
                f"{sensory} "
                f"{name_a} is the one telling. Their hands move as they speak. "
                f"{name_b} is listening — which means choosing to be still, and staying."
            ),
            "boredom": (
                f"{sensory} "
                f"Nothing is urgent right now. {name_a} and {name_b} are both in this space "
                f"with no task demanding their hands. "
                f"The quiet hasn't become uncomfortable yet. It's just there."
            ),
            "avoidance": (
                f"{sensory} "
                f"{name_a} and {name_b} are both here, which neither of them planned for. "
                f"They have not acknowledged each other yet. "
                f"Their bodies are angled slightly away. The space between them has a texture."
            ),
        }
        return AMBIENT_FRAMES.get(
            sub,
            f"{sensory} {name_a} and {name_b} are here together in the ordinary way of things."
        )

    return FRAMES.get(
        scene_type,
        f"{sensory} {name_a} and {name_b} are at {loc_name}. The moment is about to begin."
    )


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
