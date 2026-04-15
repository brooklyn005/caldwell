"""
action_generator.py — generates a physical action for each character each tick.

Runs BEFORE conversations so actions ground the conversations in physical reality.
Characters eat, sleep, build, explore, tend plants, fix things, sit alone —
whatever their body and personality drive them toward in their current location.

This is what makes them feel like they're living rather than just talking.
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import Character, Location, Memory, SatisfactionLog
from simulation.ai_caller import call_scoring_model
from simulation.biology import get_or_create_biology, EATING_LOCATIONS, SLEEP_LOCATIONS, BATHROOM_LOCATIONS

logger = logging.getLogger("caldwell.action")

# What each location affords physically
LOCATION_AFFORDANCES = {
    "Central Square": [
        "sitting under one of the oak trees watching who comes and goes",
        "walking the perimeter of the square slowly",
        "standing at the fountain watching the water",
        "finding a bench and staying still for a while",
        "moving through the space looking for something useful or interesting",
    ],
    "Bayou Market": [
        "going through what appeared this morning and eating",
        "eating slowly and paying attention to what the food actually tastes like",
        "taking more than you need and carrying it elsewhere",
        "eating quickly and leaving",
        "sitting with the food in front of you for a while before eating",
        "sharing what appeared with whoever else is here",
    ],
    "The Workshop": [
        "picking up tools and figuring out what they do",
        "trying to build or repair something",
        "looking through what materials are here and thinking about what could be made",
        "working with your hands on something that needs fixing",
        "watching the generator run and trying to understand how it works",
    ],
    "Caldwell Public Library": [
        "pulling books off shelves and opening them",
        "reading something slowly and carefully",
        "looking for a specific kind of information without knowing where to find it",
        "sitting in the light from a tall window with a book",
        "writing something down on paper",
    ],
    "Community Center": [
        "moving chairs around to make the space feel different",
        "using the kitchen to prepare food for anyone who comes",
        "sitting on the stage alone",
        "eating in the large empty room",
        "doing something physical in the open floor space",
    ],
    "Riverside Park": [
        "sitting by the water doing nothing",
        "walking the length of the park slowly",
        "crossing the footbridge back and forth",
        "finding a spot under a tree and staying there",
        "putting your feet in the creek",
        "lying in the grass looking up",
    ],
    "The Meridian": [
        "sleeping or resting in your space",
        "lying down and letting your body recover",
        "organizing the space you've claimed",
        "looking out the window at the city below",
        "sitting in the quiet of your own room",
    ],
    "Lakeview Flats": [
        "resting on the porch",
        "sleeping properly for the first time in a while",
        "sitting inside in the quiet",
        "tending to the space you've claimed",
        "lying on a bed and letting exhaustion take over",
    ],
    "Rooftop Garden": [
        "tending the herbs that grow here",
        "picking mint or rosemary and smelling it",
        "sitting on the roof looking at the horizon",
        "watching the rest of the city from above",
        "working with the soil in the planting beds",
    ],
    "The Chapel": [
        "sitting in the empty space in silence",
        "walking the perimeter of the bare room",
        "standing in the center and listening to the acoustics",
        "sitting on the floor rather than standing",
        "spending time alone with your thoughts in the quiet",
    ],
    "The Schoolhouse": [
        "sitting at one of the bolted desks",
        "writing on the chalkboard",
        "going through the paper and pencils",
        "reading what someone else wrote here before",
        "trying to organize what you know into something teachable",
    ],
    "Warehouse Row": [
        "exploring the dark corners of the warehouses",
        "moving things around to make the space more usable",
        "finding somewhere private and using it",
        "sitting in the shadows just watching the entrance",
        "looking for anything left behind by whoever was here before",
    ],
}


async def generate_action(
    character: Character,
    sim_day: int,
    db: Session,
) -> str | None:
    """
    Generate a physical action for this character this tick.
    Returns the action text which gets written to memory.
    """
    bio = get_or_create_biology(character, db)
    loc = db.query(Location).filter(
        Location.id == character.current_location_id
    ).first()
    if not loc:
        return None

    loc_name = loc.name
    affordances = LOCATION_AFFORDANCES.get(loc_name, ["moving through the space"])
    affordance_hint = random.choice(affordances)

    # Build biological urgency context
    bio_context = []
    if bio.hunger > 6.5:
        bio_context.append(f"You are genuinely hungry (hunger {bio.hunger:.1f}/10).")
    elif bio.hunger > 3.5:
        bio_context.append(f"You feel some hunger.")

    if bio.fatigue > 7.0:
        bio_context.append(f"You are exhausted (fatigue {bio.fatigue:.1f}/10). Your body wants rest.")
    elif bio.fatigue > 4.5:
        bio_context.append(f"You feel tired.")

    if bio.bathroom_urgency > 6.0:
        bio_context.append(f"You need to find a private space — an urgent physical need.")

    if not bio_context:
        bio_context.append("Your basic needs are manageable right now.")

    bio_text = " ".join(bio_context)

    # Get one recent memory for grounding
    recent_mem = (
        db.query(Memory)
        .filter(Memory.character_id == character.id)
        .order_by(Memory.sim_day.desc())
        .first()
    )
    mem_context = f"Recently: {recent_mem.content[:80]}" if recent_mem else ""

    identity = character.given_name if character.given_name else f"a person — {character.physical_description[:50]}"

    # Check for pending directives — someone told this character to do something
    directive_mem = (
        db.query(Memory)
        .filter(
            Memory.character_id == character.id,
            Memory.memory_type == "directive",
            Memory.sim_day == sim_day,
        )
        .first()
    )

    if directive_mem:
        directive_content = directive_mem.content
        directive_mem.memory_type = "directive_executed"
        db.commit()

        # Find who is watching for scene context
        others_watching = db.query(Character).filter(
            Character.current_location_id == character.current_location_id,
            Character.alive == True,
            Character.is_infant == False,
            Character.id != character.id,
        ).limit(3).all()
        others_dir = ""
        if others_watching:
            names = [c.given_name or c.physical_description[:25] for c in others_watching]
            others_dir = f"Others present who may be watching: {', '.join(names)}\n"

        loc = db.query(Location).filter(Location.id == character.current_location_id).first()
        loc_name = loc.name if loc else "Caldwell"

        prompt = (
            f"You are {identity}. Age {character.age}.\n"
            f"Someone directed you to do something and you are doing it now.\n\n"
            f"The directive: {directive_content}\n\n"
            f"Location: {loc_name}\n"
            f"Physical state: {bio_text}\n"
            f"{others_dir}\n"
            f"Describe in 5-7 sentences what this looks like as it happens.\n"
            f"Make it a real scene — the physicality, the space, what you feel.\n"
            f"If others are present they are part of the scene.\n"
            f"First person. Present tense. Sensory and specific."
        )

        d_text, in_tok, out_tok = await call_scoring_model(
            system_prompt=(
                "Generate a vivid scene of a character executing a directive — 5-7 sentences. "
                "First person present tense. Physical, sensory, real. "
                "Include the texture of the action, the space, any observers. "
                "Not just what they do — what it feels and looks like."
            ),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        action = d_text.strip()
        if action and len(action) > 10:
            return action

    # Find who else is at this location for scene context
    others_here = db.query(Character).filter(
        Character.current_location_id == character.current_location_id,
        Character.alive == True,
        Character.is_infant == False,
        Character.id != character.id,
    ).limit(3).all()
    others_text = ""
    if others_here:
        names = [c.given_name or c.physical_description[:25] for c in others_here]
        others_text = f"Others present: {', '.join(names)}\n"

    prompt = (
        f"You are {identity}. Age {character.age}.\n"
        f"Your nature: {character.natural_tendency}\n\n"
        f"RIGHT NOW:\n"
        f"Location: {loc_name} — {loc.description[:120]}\n"
        f"Physical state: {bio_text}\n"
        f"{others_text}"
        f"{mem_context}\n\n"
        f"Possible things you might do here: {affordance_hint}\n\n"
        f"Describe in 4-6 sentences what you PHYSICALLY DO right now.\n"
        f"Make it a real scene — what you see, what you touch, what your body does.\n"
        f"If others are present, you may notice them, react to them, interact.\n"
        f"First person. Present tense. Concrete and physical.\n"
        f"Do not philosophize — but you can feel something as you act.\n"
        f"Let the scene breathe. Give it texture."
    )

    text, in_tok, out_tok = await call_scoring_model(
        system_prompt=(
            "Generate a vivid physical scene for a character — 4-6 sentences. "
            "First person present tense. Concrete, sensory, real. "
            "Include what the character sees, touches, hears. "
            "If others are present they can appear in the scene. "
            "Not abstract — embodied and specific."
        ),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=250,
    )

    action = text.strip()
    if not action or len(action) < 10:
        return None

    return action


def record_action_memory(
    character: Character,
    action_text: str,
    sim_day: int,
    db: Session,
):
    """Write the action to memory so conversations can reference it."""
    bio = get_or_create_biology(character, db)

    # Higher weight if biologically urgent — this action mattered more
    weight = 0.5
    if bio.hunger > 6.5 or bio.fatigue > 7.0 or bio.bathroom_urgency > 6.0:
        weight = 0.7

    mem = Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type="action",
        content=action_text,
        emotional_weight=weight,
        is_inception=False,
    )
    db.add(mem)
    db.commit()


def record_biological_satisfaction(
    character: Character,
    sim_day: int,
    db: Session,
):
    """
    Record satisfaction from meeting biological needs.
    Eating when hungry, sleeping when exhausted — these feel good
    independent of conversation outcomes.
    """
    bio = get_or_create_biology(character, db)
    loc = db.query(Location).filter(
        Location.id == character.current_location_id
    ).first()
    if not loc:
        return

    score = 0.0
    loc_name = loc.name

    if loc_name in EATING_LOCATIONS and bio.hunger > 4.0:
        score += 0.5 * (bio.hunger / 10.0)  # hungrier = more satisfying to eat

    if loc_name in SLEEP_LOCATIONS and bio.fatigue > 4.0:
        score += 0.4 * (bio.fatigue / 10.0)

    if loc_name in BATHROOM_LOCATIONS and bio.bathroom_urgency > 4.0:
        score += 0.3 * (bio.bathroom_urgency / 10.0)

    if score > 0.05:
        db.add(SatisfactionLog(
            character_id=character.id,
            sim_day=sim_day,
            score=round(min(1.0, score), 2),
            drive="biological",
        ))
        db.commit()


# ── Biological location override ──────────────────────────────────────────────

def get_biological_destination(
    character: Character,
    db: Session,
) -> Location | None:
    """
    If a character has urgent biological needs, return the location
    they should be forced to move to. Body wins over personality.
    """
    bio = get_or_create_biology(character, db)

    # Hunger override — go find food
    if bio.hunger > 6.5:
        eating_locs = db.query(Location).filter(
            Location.name.in_(EATING_LOCATIONS)
        ).all()
        if eating_locs:
            return random.choice(eating_locs)

    # Fatigue override — go rest
    if bio.fatigue > 7.5:
        sleep_locs = db.query(Location).filter(
            Location.name.in_(SLEEP_LOCATIONS)
        ).all()
        if sleep_locs:
            return random.choice(sleep_locs)

    # Bathroom urgency — find privacy
    if bio.bathroom_urgency > 7.5:
        private_locs = db.query(Location).filter(
            Location.name.in_(BATHROOM_LOCATIONS)
        ).all()
        if private_locs:
            return random.choice(private_locs)

    return None
