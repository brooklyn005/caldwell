"""
social_learning.py — implements individual and social learning for characters.

Individual learning: each character records what conversational approaches
produced good or bad outcomes for them personally, and distills those into
behavioral tendencies that modify their future prompts.

Social learning: characters at the same location as a conversation absorb
weak signals from what they witnessed, weighted by drive compatibility.

Neither requires model weight updates — learning is expressed through
accumulated prompt modifiers that update every 7 sim days.
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, BehavioralEvidence, BehavioralTendency,
    CharacterRelationship, Location
)
from simulation.ai_caller import call_scoring_model

logger = logging.getLogger("caldwell.learning")

# ── Drive compatibility matrix ────────────────────────────────────────────────
# How much does character A learn from observing character B's approach?
# 1.0 = learns fully, 0.0 = learns nothing
DRIVE_COMPATIBILITY = {
    ("Connection",  "Connection"):  0.90,
    ("Connection",  "Comfort"):     0.70,
    ("Connection",  "Curiosity"):   0.50,
    ("Connection",  "Knowledge"):   0.40,
    ("Connection",  "Order"):       0.30,
    ("Connection",  "Power"):       0.15,
    ("Connection",  "Survival"):    0.30,
    ("Power",       "Power"):       0.90,
    ("Power",       "Order"):       0.60,
    ("Power",       "Curiosity"):   0.40,
    ("Power",       "Knowledge"):   0.35,
    ("Power",       "Connection"):  0.20,
    ("Power",       "Comfort"):     0.10,
    ("Power",       "Survival"):    0.50,
    ("Knowledge",   "Knowledge"):   0.90,
    ("Knowledge",   "Curiosity"):   0.80,
    ("Knowledge",   "Analytical"):  0.70,
    ("Knowledge",   "Order"):       0.50,
    ("Knowledge",   "Power"):       0.30,
    ("Knowledge",   "Connection"):  0.30,
    ("Knowledge",   "Comfort"):     0.20,
    ("Knowledge",   "Survival"):    0.25,
    ("Order",       "Order"):       0.90,
    ("Order",       "Knowledge"):   0.60,
    ("Order",       "Power"):       0.50,
    ("Order",       "Connection"):  0.30,
    ("Order",       "Curiosity"):   0.35,
    ("Order",       "Comfort"):     0.30,
    ("Order",       "Survival"):    0.40,
    ("Curiosity",   "Curiosity"):   0.90,
    ("Curiosity",   "Knowledge"):   0.80,
    ("Curiosity",   "Connection"):  0.50,
    ("Curiosity",   "Power"):       0.30,
    ("Curiosity",   "Order"):       0.35,
    ("Curiosity",   "Comfort"):     0.25,
    ("Curiosity",   "Survival"):    0.30,
    ("Comfort",     "Comfort"):     0.90,
    ("Comfort",     "Connection"):  0.70,
    ("Comfort",     "Order"):       0.50,
    ("Comfort",     "Knowledge"):   0.30,
    ("Comfort",     "Curiosity"):   0.25,
    ("Comfort",     "Power"):       0.10,
    ("Comfort",     "Survival"):    0.40,
    ("Survival",    "Survival"):    0.90,
    ("Survival",    "Power"):       0.60,
    ("Survival",    "Order"):       0.50,
    ("Survival",    "Comfort"):     0.40,
    ("Survival",    "Connection"):  0.25,
    ("Survival",    "Knowledge"):   0.20,
    ("Survival",    "Curiosity"):   0.20,
}

def _get_compatibility(drive_a: str, drive_b: str) -> float:
    return DRIVE_COMPATIBILITY.get((drive_a, drive_b), 0.25)


# ── Approach classification ───────────────────────────────────────────────────

VALID_APPROACHES = {
    "assertive", "collaborative", "vulnerable", "philosophical",
    "nurturing", "challenging", "analytical", "withdrawn",
    "playful", "protective", "curious", "practical",
}

async def classify_approach(
    exchanges: list[dict],
    character: Character,
) -> str:
    """
    Classify the conversational approach used by a character in one word.
    Cheap local call — just needs one word output.
    """
    char_lines = [
        ex["text"] for ex in exchanges
        if ex.get("roster_id") == character.roster_id
    ]
    if not char_lines:
        return "withdrawn"

    transcript = " ".join(char_lines[:4])  # first 4 of their turns
    prompt = (
        f"Classify this person's conversational approach in ONE word only.\n"
        f"Text: \"{transcript[:300]}\"\n\n"
        f"Choose exactly one: assertive, collaborative, vulnerable, philosophical, "
        f"nurturing, challenging, analytical, withdrawn, playful, protective, "
        f"curious, practical\n\n"
        f"One word. Nothing else."
    )
    text, _, _ = await call_scoring_model(
        system_prompt="Classify conversational approaches. One word only from the provided list.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=5,
    )
    word = text.strip().lower().split()[0] if text.strip() else "withdrawn"
    # Strip punctuation
    word = "".join(c for c in word if c.isalpha())
    return word if word in VALID_APPROACHES else "collaborative"


# ── Evidence recording ────────────────────────────────────────────────────────

def record_evidence(
    character: Character,
    approach: str,
    outcome_score: float,
    partner: Character | None,
    sim_day: int,
    location: str,
    db: Session,
    is_social: bool = False,
    observed_char: Character | None = None,
    compatibility_weight: float = 1.0,
):
    ev = BehavioralEvidence(
        character_id=character.id,
        sim_day=sim_day,
        approach=approach,
        outcome_score=outcome_score,
        trust_delta=0.0,
        partner_id=partner.id if partner else None,
        location=location,
        is_social_observation=is_social,
        observed_character_id=observed_char.id if observed_char else None,
        compatibility_weight=compatibility_weight,
    )
    db.add(ev)


async def record_conversation_learning(
    char_a: Character,
    char_b: Character,
    exchanges: list[dict],
    outcome_a: float,
    outcome_b: float,
    location: str,
    sim_day: int,
    db: Session,
):
    """
    Classify and record learning for both participants in a conversation.
    """
    # Classify approach for each participant
    approach_a = await classify_approach(exchanges, char_a)
    approach_b = await classify_approach(exchanges, char_b)

    record_evidence(char_a, approach_a, outcome_a, char_b, sim_day, location, db)
    record_evidence(char_b, approach_b, outcome_b, char_a, sim_day, location, db)
    db.commit()

    logger.debug(
        f"  Learning: {char_a.roster_id} used '{approach_a}' → {outcome_a:+.2f} | "
        f"{char_b.roster_id} used '{approach_b}' → {outcome_b:+.2f}"
    )


def record_social_observations(
    conversation_chars: list[Character],
    successful_approaches: dict,  # {roster_id: (approach, score)}
    location_name: str,
    sim_day: int,
    db: Session,
):
    """
    Characters who were at the same location implicitly witness conversations.
    Add weak evidence signals for social observers.
    """
    # Find all living characters at this location
    loc = db.query(Location).filter(Location.name == location_name).first()
    if not loc:
        return

    conv_ids = {c.id for c in conversation_chars}
    observers = [
        c for c in loc.occupants
        if c.id not in conv_ids and c.alive
    ]

    for observer in observers:
        for speaker in conversation_chars:
            if speaker.roster_id not in successful_approaches:
                continue
            approach, score = successful_approaches[speaker.roster_id]
            if abs(score) < 0.2:
                continue  # neutral outcomes don't teach much

            compat = _get_compatibility(observer.core_drive, speaker.core_drive)
            weight = compat * 0.3  # social observation always weaker than direct

            record_evidence(
                character=observer,
                approach=approach,
                outcome_score=score * compat,
                partner=None,
                sim_day=sim_day,
                location=location_name,
                db=db,
                is_social=True,
                observed_char=speaker,
                compatibility_weight=weight,
            )

    if observers:
        db.commit()
        logger.debug(
            f"  Social obs: {len(observers)} observers at {location_name}"
        )


# ── Tendency distillation ─────────────────────────────────────────────────────

# Natural language tendency templates
TENDENCY_TEMPLATES = {
    "assertive": {
        "positive": (
            "You have found, without consciously realizing it, that stating things "
            "directly and holding your ground tends to produce better outcomes for you. "
            "Hesitation has not served you well here."
        ),
        "negative": (
            "You have noticed that pushing hard rarely gets you what you want in this place. "
            "Something in you is starting to reach for other ways."
        ),
    },
    "collaborative": {
        "positive": (
            "Working alongside others rather than against them has consistently felt right. "
            "You find yourself naturally looking for the shared path."
        ),
        "negative": (
            "Trying to find common ground hasn't always worked the way you hoped. "
            "You're starting to wonder if you give too much ground."
        ),
    },
    "vulnerable": {
        "positive": (
            "Showing what you actually feel — even the uncomfortable parts — "
            "has brought you closer to people here than you expected. "
            "Honesty about your inner state seems to open things."
        ),
        "negative": (
            "Opening up has not always been received the way you needed. "
            "You are learning to be more careful about who gets to see inside."
        ),
    },
    "philosophical": {
        "positive": (
            "Asking the deeper question — what this all means, why things are as they are — "
            "has led to the most interesting conversations you've had here. "
            "People seem hungry for that kind of thinking."
        ),
        "negative": (
            "Going deep hasn't always landed. Some people want practical, not profound. "
            "You're learning to read the room before you go there."
        ),
    },
    "nurturing": {
        "positive": (
            "Attending to what others need — before they ask — has built something real. "
            "People seek you out. You feel that."
        ),
        "negative": (
            "Giving has not always been received with gratitude. "
            "You are beginning to notice who takes and who reciprocates."
        ),
    },
    "challenging": {
        "positive": (
            "Pushing back, asking harder questions, refusing to let things slide — "
            "this has earned you a certain respect, even when it creates friction."
        ),
        "negative": (
            "Challenging people has sometimes closed doors rather than opened them. "
            "You are learning that timing matters as much as truth."
        ),
    },
    "analytical": {
        "positive": (
            "Slowing down to actually think things through — to look for patterns "
            "before reacting — has served you well. Others seem to trust your assessments."
        ),
        "negative": (
            "Analyzing before acting has sometimes meant missing the moment. "
            "You are starting to trust your faster instincts more."
        ),
    },
    "withdrawn": {
        "positive": (
            "Saying less has often meant the words you do say carry more weight. "
            "You are learning the power of your own silence."
        ),
        "negative": (
            "Holding back has sometimes meant being passed over. "
            "You feel the cost of invisibility and it is starting to bother you."
        ),
    },
    "protective": {
        "positive": (
            "Putting yourself between threat and others has built bonds you didn't expect. "
            "People remember who stood with them."
        ),
        "negative": (
            "Not everyone wants to be protected. You are learning the difference "
            "between helping and overriding."
        ),
    },
    "curious": {
        "positive": (
            "Leading with genuine questions — not to make a point but because you actually "
            "want to know — has opened more conversations than any other approach."
        ),
        "negative": (
            "Your curiosity has sometimes felt intrusive to others. "
            "You are becoming more careful about when to push and when to let things rest."
        ),
    },
    "practical": {
        "positive": (
            "Focusing on what can actually be done — cutting through the talking to the doing — "
            "has made you useful in ways that matter."
        ),
        "negative": (
            "Moving to solutions before people feel heard has sometimes backfired. "
            "You are learning that the feeling matters as much as the fix."
        ),
    },
    "playful": {
        "positive": (
            "Finding the lightness in hard moments has made you someone people want around. "
            "Laughter has built more trust here than seriousness."
        ),
        "negative": (
            "Not every moment wants levity. You have learned the hard way "
            "that humor at the wrong time closes people off."
        ),
    },
}


def distill_tendency(character: Character, sim_day: int, db: Session):
    """
    Read a character's behavioral evidence and distill it into a tendency modifier.
    Runs every 7 sim days. Only updates if there's enough evidence (min 5 records).
    """
    evidence = (
        db.query(BehavioralEvidence)
        .filter(BehavioralEvidence.character_id == character.id)
        .all()
    )
    if len(evidence) < 5:
        return

    # Aggregate weighted scores per approach
    approach_scores: dict[str, list[float]] = {}
    for ev in evidence:
        weighted_score = ev.outcome_score * ev.compatibility_weight
        if ev.approach not in approach_scores:
            approach_scores[ev.approach] = []
        approach_scores[ev.approach].append(weighted_score)

    # Calculate average per approach
    approach_avgs = {
        approach: sum(scores) / len(scores)
        for approach, scores in approach_scores.items()
        if len(scores) >= 2  # need at least 2 data points
    }

    if not approach_avgs:
        return

    # Find dominant approach (most evidence) and best/worst outcomes
    dominant = max(approach_scores, key=lambda a: len(approach_scores[a]))
    best_approach = max(approach_avgs, key=approach_avgs.get)
    worst_approach = min(approach_avgs, key=approach_avgs.get)

    # Build tendency text
    parts = []

    # What's working
    best_score = approach_avgs[best_approach]
    if best_score > 0.2 and best_approach in TENDENCY_TEMPLATES:
        parts.append(TENDENCY_TEMPLATES[best_approach]["positive"])

    # What's not working (if clearly negative)
    worst_score = approach_avgs[worst_approach]
    if worst_score < -0.15 and worst_approach != best_approach and worst_approach in TENDENCY_TEMPLATES:
        parts.append(TENDENCY_TEMPLATES[worst_approach]["negative"])

    # Social learning note if significant
    social_evidence = [ev for ev in evidence if ev.is_social_observation]
    if len(social_evidence) >= 5:
        observed_ids = [ev.observed_character_id for ev in social_evidence if ev.observed_character_id]
        from database.models import Character as Char
        if observed_ids:
            most_observed_id = max(set(observed_ids), key=observed_ids.count)
            most_observed = db.query(Char).filter(Char.id == most_observed_id).first()
            if most_observed:
                obs_name = most_observed.given_name or most_observed.physical_description[:35]
                obs_approach = max(
                    [ev.approach for ev in social_evidence if ev.observed_character_id == most_observed_id],
                    key=lambda a: [ev.approach for ev in social_evidence
                                  if ev.observed_character_id == most_observed_id].count(a)
                )
                parts.append(
                    f"You have been watching {obs_name} closely and something about "
                    f"how they handle situations has gotten under your skin — "
                    f"the way they tend toward {obs_approach}. "
                    f"You don't know if you're drawn to it or wary of it."
                )

    tendency_text = "\n".join(parts) if parts else None

    # Save or update
    existing = (
        db.query(BehavioralTendency)
        .filter(BehavioralTendency.character_id == character.id)
        .first()
    )
    if existing:
        existing.tendency_text = tendency_text
        existing.dominant_approach = dominant
        existing.approaches_json = json.dumps({k: round(v, 3) for k, v in approach_avgs.items()})
        existing.evidence_count = len(evidence)
        existing.last_updated_day = sim_day
    else:
        db.add(BehavioralTendency(
            character_id=character.id,
            tendency_text=tendency_text,
            dominant_approach=dominant,
            approaches_json=json.dumps({k: round(v, 3) for k, v in approach_avgs.items()}),
            evidence_count=len(evidence),
            last_updated_day=sim_day,
        ))
    db.commit()

    if tendency_text:
        logger.info(
            f"  Tendency updated: {character.roster_id} "
            f"dominant={dominant} best={best_approach}({best_score:+.2f})"
        )


def get_tendency_modifier(character: Character, db: Session) -> str | None:
    """Return the current behavioral tendency text for prompt injection."""
    tendency = (
        db.query(BehavioralTendency)
        .filter(BehavioralTendency.character_id == character.id)
        .first()
    )
    if tendency and tendency.tendency_text:
        return tendency.tendency_text
    return None


def maybe_distill_all(sim_day: int, db: Session):
    """
    Run tendency distillation for all characters every 7 sim days.
    """
    if sim_day % 7 != 0:
        return
    chars = db.query(Character).filter(Character.alive == True).all()
    for char in chars:
        distill_tendency(char, sim_day, db)
    logger.info(f"Day {sim_day}: Behavioral tendencies distilled for {len(chars)} characters.")
