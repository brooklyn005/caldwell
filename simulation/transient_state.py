"""
transient_state.py — per-character daily emotional state.

This is the "weather", not the "climate". It provides immediate texture
to prompts and scene selection. Resets/updates each tick.

State is derived from:
- Recent satisfaction scores (dispositions)
- Biology (hunger, fatigue)
- Recent relationship events
- Recent scene participation
- Random variation (people have good and bad days)

Injected into system prompt as a concrete emotional paragraph.
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, CharacterTransientState, CharacterDisposition,
    CharacterBiology, CharacterRelationship, ConsequenceRecord,
)

logger = logging.getLogger("caldwell.transient_state")

# All possible emotional tags
_POSITIVE_TAGS = [
    "emboldened", "hopeful", "warm", "relieved", "curious_open",
    "energized", "tender", "playful", "proud", "grateful",
]

_NEGATIVE_TAGS = [
    "guarded", "raw", "lonely", "yearning", "ashamed",
    "suspicious", "irritated", "flooded", "grieving", "afraid",
    "exhausted_emotionally", "resentful", "competitive",
]

_NEUTRAL_ACTIVE_TAGS = [
    "watchful", "focused", "performing", "strategic", "cautious",
    "protective", "restless", "heavy",
]


def update_all_transient_states(sim_day: int, db: Session) -> None:
    """Called at start of each tick. Updates all living characters."""
    chars = db.query(Character).filter(
        Character.alive == True, Character.is_infant == False
    ).all()
    for char in chars:
        _update_character_state(char, sim_day, db)
    db.commit()
    logger.info(f"  Updated transient states for {len(chars)} characters")


def _update_character_state(char: Character, sim_day: int, db: Session) -> None:
    state = db.query(CharacterTransientState).filter(
        CharacterTransientState.character_id == char.id
    ).first()
    if not state:
        state = CharacterTransientState(character_id=char.id, sim_day=sim_day)
        db.add(state)

    state.sim_day = sim_day

    # ── Derive from disposition ────────────────────────────────────────────
    disp = db.query(CharacterDisposition).filter(
        CharacterDisposition.character_id == char.id
    ).first()
    disposition_state = disp.state if disp else "neutral"

    # ── Derive from biology ────────────────────────────────────────────────
    bio = db.query(CharacterBiology).filter(
        CharacterBiology.character_id == char.id
    ).first()

    hunger = bio.hunger if bio else 4.0
    fatigue = bio.fatigue if bio else 3.0
    state.hunger_level = hunger
    state.fatigue_level = fatigue

    # ── Build emotional tags ───────────────────────────────────────────────
    tags = []

    # Disposition → base tags
    if disposition_state == "despairing":
        tags.extend(random.sample(["lonely", "guarded", "heavy", "grieving"], 2))
    elif disposition_state == "frustrated":
        tags.extend(random.sample(["resentful", "irritated", "guarded"], 2))
    elif disposition_state == "content":
        tags.extend(random.sample(["warm", "hopeful", "open"], 1))
    elif disposition_state == "flourishing":
        tags.extend(random.sample(["emboldened", "warm", "energized"], 2))

    # Biology → tags
    if hunger >= 7.5:
        tags.append("irritated")
        tags.append("focused")  # focused on food
    elif hunger >= 5.0:
        tags.append("restless")

    if fatigue >= 7.5:
        tags.append("heavy")
        tags.append("guarded")
    elif fatigue >= 5.5:
        tags.append("exhausted_emotionally")

    # Recent relationship → yearning/suspicious
    recent_rels = db.query(CharacterRelationship).filter(
        CharacterRelationship.from_character_id == char.id,
        CharacterRelationship.last_interacted_day >= sim_day - 3,
    ).all()

    high_trust_recent = any(r.trust_level >= 0.7 and r.familiarity >= 0.6 for r in recent_rels)
    low_trust_recent = any(r.trust_level < -0.2 for r in recent_rels)

    if high_trust_recent and random.random() < 0.4:
        tags.append("tender")
    if low_trust_recent:
        tags.append("suspicious")

    # Recent consequences affecting this character
    recent_consequences = db.query(ConsequenceRecord).filter(
        ConsequenceRecord.sim_day >= sim_day - 2,
        ConsequenceRecord.reader_visible == True,
    ).all()
    for cons in recent_consequences:
        ids = cons.affected_ids
        if char.roster_id in ids or (hasattr(char, 'id') and char.id in ids):
            if "shame" in cons.consequence_type or "exposure" in cons.consequence_type:
                state.shame_active = True
                if "ashamed" not in tags:
                    tags.append("ashamed")
            if "alliance" in cons.consequence_type or "trust" in cons.consequence_type:
                if "warm" not in tags:
                    tags.append("warm")

    # Random variation — people have good and bad days
    if random.random() < 0.2:
        random_tag = random.choice(_NEGATIVE_TAGS + _NEUTRAL_ACTIVE_TAGS)
        if random_tag not in tags:
            tags.append(random_tag)
    if random.random() < 0.15:
        random_tag = random.choice(_POSITIVE_TAGS)
        if random_tag not in tags:
            tags.append(random_tag)

    # Minor-specific: children/teens have heightened states
    if char.is_minor:
        if random.random() < 0.3:
            tags.append(random.choice(["watchful", "yearning", "restless", "curious_open"]))

    # Deduplicate and cap at 4 tags
    seen = set()
    unique_tags = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)
    state.emotional_tags = unique_tags[:4]

    # Guardedness
    n_negative = sum(1 for t in unique_tags if t in _NEGATIVE_TAGS)
    base_guard = 0.3 + (n_negative * 0.15) + (random.random() * 0.1)
    if disposition_state in ("despairing", "frustrated"):
        base_guard += 0.2
    state.guardedness = min(base_guard, 1.0)

    # Loneliness
    if not recent_rels or max((r.familiarity for r in recent_rels), default=0) < 0.3:
        state.loneliness = 0.7 + random.random() * 0.3
    else:
        state.loneliness = 0.1 + random.random() * 0.3


def get_transient_state_for_prompt(char: Character, sim_day: int, db: Session) -> str:
    """
    Returns a natural-language paragraph for injection into the system prompt.
    Describes today's emotional weather for this character.
    """
    state = db.query(CharacterTransientState).filter(
        CharacterTransientState.character_id == char.id,
        CharacterTransientState.sim_day == sim_day,
    ).first()

    if not state:
        return ""

    tags = state.emotional_tags
    hunger = state.hunger_level
    fatigue = state.fatigue_level
    guardedness = state.guardedness
    loneliness = state.loneliness

    parts = []

    # Physical state
    if hunger >= 7.5:
        parts.append("Your stomach is loud today. It shapes everything — your patience, your mood, what you're willing to do.")
    elif hunger >= 5.5:
        parts.append("You're hungry. Not desperate, but aware of it. It sits in the background of everything.")

    if fatigue >= 7.5:
        parts.append("You're running on empty. Your body wants to stop. Everything costs more than it should.")
    elif fatigue >= 5.5:
        parts.append("You didn't sleep well, or enough. The edge is gone from your thinking.")

    # Emotional tags
    tag_phrases = {
        "guarded": "You're not opening up today. Something feels exposed and you're keeping it covered.",
        "raw": "Something is close to the surface. You can feel it. You don't necessarily want to.",
        "lonely": "You've been alone in a way that has nothing to do with how many people are around.",
        "yearning": "There's something you want and haven't said. It's sitting in you right now.",
        "ashamed": "Something you did or said is still with you. You wish it wasn't.",
        "suspicious": "Someone or something doesn't add up. You're watching.",
        "irritated": "Things are getting to you today. Small things. It's not really about the small things.",
        "flooded": "Too much is happening inside. It's hard to track which feeling belongs to what.",
        "grieving": "Something is lost or gone and you're carrying that.",
        "afraid": "Something feels threatening. It's in your body even when it's not in your thoughts.",
        "resentful": "You're holding something against someone. You haven't said it yet.",
        "competitive": "Someone has something you want, or you feel like you need to prove something.",
        "emboldened": "Today you feel like you can say the harder thing. Like you have the ground under you.",
        "hopeful": "Something is possible that didn't feel possible yesterday.",
        "warm": "You feel good toward the people here. It's there in how you look at them.",
        "relieved": "Something you were carrying got lighter. You notice it.",
        "energized": "You have more of yourself today than you usually do.",
        "tender": "You're soft today. Not weak — just open in a way you're not always.",
        "watchful": "You're observing more than usual. Taking it in. Not yet reacting.",
        "protective": "Someone or something needs watching over. You feel it before you've thought it.",
        "restless": "You can't settle. The stillness isn't coming today.",
        "heavy": "Something is weighing on you. You carry it with you into every room.",
        "focused": "One thing has your attention today. The rest is noise.",
        "performing": "You're showing a version of yourself right now. Not the whole thing.",
        "cautious": "You're moving carefully. You don't want to make anything worse.",
        "strategic": "You know what you want from this. You're thinking three moves ahead.",
    }

    for tag in tags:
        if tag in tag_phrases:
            parts.append(tag_phrases[tag])

    # Guardedness/loneliness if extreme
    if guardedness > 0.8:
        parts.append("You're not letting anyone in today. The door is closed.")
    if loneliness > 0.8:
        parts.append("The distance between you and everyone else feels wider today.")

    if state.shame_active:
        parts.append("There's something specific you're not proud of. You hope it doesn't come up.")
    if state.hope_active:
        parts.append("Something might change. You're not sure what. But you feel it.")
    if state.obsession_text:
        parts.append(f"One thing keeps coming back to you: {state.obsession_text}")

    if not parts:
        return ""

    return "YOUR EMOTIONAL WEATHER TODAY:\n" + " ".join(parts)
