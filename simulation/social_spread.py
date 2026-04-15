"""
social_spread.py — witness memories and secondhand rumor propagation.

After high-intensity scenes (argument, status_challenge, quiet_intimacy),
characters who were physically present but not in the scene get a distorted
first-person memory of what they observed. The next day, a small number of
uninvolved characters receive a vaguer secondhand rumor.

Distortion is weighted by relationship tension: a witness who distrusts
participant A will frame what they saw through that lens.

No API calls — rule-based only.
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, Memory, CharacterRelationship,
)

logger = logging.getLogger("caldwell.social_spread")

# Only high-intensity scenes generate witness memories
_HIGH_INTENSITY = {"argument", "status_challenge", "quiet_intimacy"}

# emotional weight by scene type
_WITNESS_WEIGHT = {
    "argument": 0.55,
    "status_challenge": 0.60,
    "quiet_intimacy": 0.65,
}

# ── Witness memory templates ───────────────────────────────────────────────────
# Keyed by (scene_type, bias_toward_first_participant)
# bias: "allied" (trust > 0.4), "hostile" (trust < -0.2), "neutral"
# {a} = first participant, {b} = second participant, {loc} = location name

_WITNESS_TEMPLATES = {
    ("argument", "neutral"): [
        "I saw {a} and {b} go at it at {loc}. I kept out of it.",
        "There was a fight between {a} and {b}. Hard to miss.",
        "{a} and {b} were arguing. Something about it didn't feel resolved when it ended.",
        "I heard raised voices — {a} and {b}. The air was bad after.",
    ],
    ("argument", "allied"): [
        "I watched {b} push {a} too hard. {a} held their ground but it cost them something.",
        "{b} wouldn't let up on {a}. I don't know what started it, but I know what I saw.",
        "{a} was trying to say something and {b} kept cutting through it. It wasn't right.",
    ],
    ("argument", "hostile"): [
        "{a} was in {b}'s face about something. I don't know what they wanted.",
        "Looked to me like {a} was the one pushing. {b} was just trying to get through it.",
        "{a} was making it ugly. {b} didn't deserve that, from what I saw.",
    ],
    ("status_challenge", "neutral"): [
        "Something played out between {a} and {b} at {loc}. Someone came out smaller.",
        "I saw {a} and {b} go for something. I couldn't tell who won.",
        "There was a moment between {a} and {b}. The kind where something shifts.",
        "{a} and {b} were testing each other. I watched enough to know it wasn't casual.",
    ],
    ("status_challenge", "allied"): [
        "{a} stood their ground with {b}. It took something to do that.",
        "I saw {a} hold their own against {b}. Whatever {b} was after, they didn't get it clean.",
    ],
    ("status_challenge", "hostile"): [
        "{a} was making a point of something with {b}. Looked like posturing to me.",
        "{a} pushed at {b} like they had something to prove. Maybe they do.",
    ],
    ("quiet_intimacy", "neutral"): [
        "I saw {a} and {b} together at {loc}. More than just talking.",
        "I came across {a} and {b}. Something was happening between them. I left.",
        "There was something between {a} and {b} at {loc}. Not my business, but I saw it.",
        "I don't know what {a} and {b} were doing, but it wasn't nothing.",
    ],
    ("quiet_intimacy", "allied"): [
        "I saw {a} with {b}. Something close. I hope it's good for them.",
        "{a} and {b} were together in a way I hadn't seen before. I noticed.",
    ],
    ("quiet_intimacy", "hostile"): [
        "I saw {a} with {b}. Whatever it is between them, it's more than I thought.",
        "{a} and {b} — together. I don't know what to make of that.",
    ],
}

# ── Secondhand rumor templates ─────────────────────────────────────────────────
_RUMOR_TEMPLATES = {
    "argument": [
        "I heard {a} and {b} had a fight at {loc}. I wasn't there.",
        "Word got to me that {a} and {b} went at each other. Don't know what about.",
        "Someone said {a} and {b} had it out. The details didn't come with the story.",
    ],
    "status_challenge": [
        "I heard something happened between {a} and {b}. Someone came out on top, apparently.",
        "Word is {a} and {b} had some kind of confrontation. People are talking about it.",
        "I wasn't there, but I heard {a} and {b} had a moment. The kind that matters.",
    ],
    "quiet_intimacy": [
        "Someone told me they saw {a} and {b} together. That way.",
        "I heard {a} and {b} were seen at {loc}. Together. More than just talking.",
        "Word is {a} and {b} are closer than people knew. I'm thinking about that.",
    ],
}


def _name(char: Character) -> str:
    return char.given_name if char.given_name else (
        "the woman" if char.gender == "F" else "the man"
    )


def _bias(witness: Character, participant: Character, db: Session) -> str:
    """Returns 'allied', 'hostile', or 'neutral' based on trust level."""
    rel = db.query(CharacterRelationship).filter(
        CharacterRelationship.from_character_id == witness.id,
        CharacterRelationship.to_character_id == participant.id,
    ).first()
    if not rel:
        return "neutral"
    if rel.trust_level > 0.4:
        return "allied"
    if rel.trust_level < -0.2:
        return "hostile"
    return "neutral"


def _pick_template(scene_type: str, bias: str, templates: dict) -> str | None:
    key = (scene_type, bias)
    options = templates.get(key) or templates.get((scene_type, "neutral"))
    return random.choice(options) if options else None


def propagate_scene_aftermath(
    scene_type: str,
    exchanges: list[dict],
    participants: list[Character],
    location: Location,
    sim_day: int,
    db: Session,
) -> None:
    """
    Called after every scene. For high-intensity scene types:
    - Writes a distorted first-person witness memory to each character
      physically present at the location who wasn't a participant.
    - Seeds a vaguer secondhand rumor for 1-2 uninvolved characters
      the following day.
    """
    if scene_type not in _HIGH_INTENSITY:
        return
    if not participants or not location:
        return

    participant_ids = {p.id for p in participants}
    char_a = participants[0]
    char_b = participants[1] if len(participants) > 1 else participants[0]
    a_name = _name(char_a)
    b_name = _name(char_b)
    loc_name = location.name

    # ── Direct witnesses ──────────────────────────────────────────────────────
    witnesses = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.current_location_id == location.id,
        ~Character.id.in_(participant_ids),
    ).all()

    witness_ids = {w.id for w in witnesses}

    for witness in witnesses:
        bias = _bias(witness, char_a, db)
        template = _pick_template(scene_type, bias, _WITNESS_TEMPLATES)
        if not template:
            continue

        text = template.format(a=a_name, b=b_name, loc=loc_name)
        weight = _WITNESS_WEIGHT.get(scene_type, 0.55)

        try:
            db.add(Memory(
                character_id=witness.id,
                sim_day=sim_day,
                memory_type="witness",
                content=text,
                emotional_weight=weight,
                is_inception=False,
            ))
        except Exception as e:
            logger.warning(f"Witness memory write failed for {witness.roster_id}: {e}")

    if witnesses:
        logger.info(
            f"  Social spread [{scene_type}] at {loc_name}: "
            f"{len(witnesses)} witness(es) got memories"
        )

    # ── Secondhand rumors (next day) ──────────────────────────────────────────
    rumor_templates = _RUMOR_TEMPLATES.get(scene_type)
    if not rumor_templates:
        return

    # Only seed rumors for scenes with enough exchanges to be noteworthy
    if len(exchanges) < 6:
        return

    # Pick 1-2 characters who weren't there and aren't participants
    candidates = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        ~Character.id.in_(participant_ids | witness_ids),
    ).all()

    if not candidates:
        return

    n_rumors = min(2, len(candidates))
    recipients = random.sample(candidates, n_rumors)

    for recipient in recipients:
        template = random.choice(rumor_templates)
        text = template.format(a=a_name, b=b_name, loc=loc_name)

        try:
            db.add(Memory(
                character_id=recipient.id,
                sim_day=sim_day + 1,   # arrives the next day
                memory_type="rumor",
                content=text,
                emotional_weight=_WITNESS_WEIGHT.get(scene_type, 0.55) * 0.7,
                is_inception=False,
            ))
        except Exception as e:
            logger.warning(f"Rumor write failed for {recipient.roster_id}: {e}")

    try:
        db.commit()
    except Exception as e:
        logger.warning(f"Social spread commit failed: {e}")
        db.rollback()
