"""
lexicon.py — community word-dictionary for things that lack names.

Characters in Caldwell start without vocabulary for intimate/private acts,
body changes, and human development. They must invent terms — or describe
things in awkward literal language — until the community builds a shared lexicon.

The lexicon is injected into system prompts as vocabulary CONSTRAINTS:
  - Characters cannot use modern clinical/adult terminology
  - They must use coined terms or describe what they actually observed
  - As terms spread, more characters gain access to them

Term detection: after each scene, scan dialogue for coining patterns.
"""
import json
import logging
import re
from sqlalchemy.orm import Session
from database.models import Character, LexiconEntry

logger = logging.getLogger("caldwell.lexicon")

# Vocabulary that is FORBIDDEN until the community coins something
# (These are the concepts characters must work around)
_FORBIDDEN_TERMS = {
    # Clinical/adult intimate vocabulary — must be described
    "sex", "sexual", "intercourse", "masturbation", "erection",
    "orgasm", "ejaculation", "vagina", "penis", "genitals",
    "vulva", "clitoris", "testicles", "aroused", "arousal",
    # Medical terms for bodily changes
    "menstruation", "menstrual", "puberty", "ovulation",
    "hormones", "testosterone", "estrogen",
    # Modern social/relationship vocabulary
    "boyfriend", "girlfriend", "romantic", "dating",
    "attraction", "sexuality",
}

# Patterns that suggest a character is coining a new term
_COINING_PATTERNS = [
    r"i(?:'ve been)? call(?:ing)? it [\"\']?([A-Za-z\-]+)[\"\']?",
    r"we call it [\"\']?([A-Za-z\-]+)[\"\']?",
    r"i named it [\"\']?([A-Za-z\-]+)[\"\']?",
    r"the [\"\']([A-Za-z\-]+)[\"\'] — (?:it's|that's|is)",
    r"what i (?:call|name) [\"\']([A-Za-z\-]+)[\"\']",
    r"[\"\']([A-Za-z\-]+)[\"\'] is what (?:i|we) call",
    r"i(?:'ve)? been thinking of it as [\"\']?([A-Za-z\-]+)[\"\']?",
]

# Subjects that should be described, not named with modern terms
_CONSTRAINT_TOPICS = {
    "intimate behavior": (
        "You have no standard word for what happens between two people in private — "
        "the touching, the closeness, the thing that happens when they are alone together. "
        "If you need to refer to it, describe what you actually saw or felt."
    ),
    "body changes (female)": (
        "You have no agreed name yet for the changes happening in women's bodies — "
        "the monthly bleeding, the shifts in shape and mood. "
        "Describe what you notice, or use whatever term your community has coined."
    ),
    "body changes (male)": (
        "You have no agreed name for the changes in younger men's bodies — "
        "the voice, the hair, the restlessness. Describe what you observe."
    ),
    "attraction": (
        "You don't have a clean word for the feeling of being pulled toward someone. "
        "You know the sensation. You may name it, describe it, or stay quiet about it."
    ),
}


def get_vocabulary_constraints(character: Character, db: Session) -> str:
    """
    Returns a vocabulary constraints block for the system prompt.
    Lists what the character cannot say and what they can use instead.
    """
    # Gather community-coined terms
    entries = db.query(LexiconEntry).order_by(
        LexiconEntry.adoption_count.desc()
    ).limit(12).all()

    coined_lines = []
    for entry in entries:
        adopters = json.loads(entry.adopters_json or "[]")
        char_knows = (
            character.roster_id in adopters
            or entry.adoption_count >= 4  # widely known
        )
        if char_knows:
            coined_lines.append(
                f'  "{entry.term}" — {entry.rough_meaning}'
            )

    constraints_block = (
        "VOCABULARY LIMITS — HARD RULES:\n"
        "This community has no inherited language for human development and private behavior. "
        "You cannot use modern clinical or adult terminology for these topics. "
        "Forbidden: sex, sexual, intercourse, puberty, menstruation, hormones, genitals, "
        "arousal, orgasm, or any clinical anatomy term.\n"
        "Instead: describe what you actually saw, heard, or felt using plain words. "
        "If you need to refer to these things, use the community's own coined terms "
        "or invent your own description on the spot."
    )

    if coined_lines:
        constraints_block += (
            "\n\nTERMS THIS COMMUNITY HAS COINED:\n"
            + "\n".join(coined_lines)
        )

    return constraints_block


def detect_new_terms_in_exchanges(
    character: Character,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
) -> list[LexiconEntry]:
    """
    After a scene, scan this character's dialogue for patterns that suggest
    they are coining a new term. Returns any new LexiconEntry rows created.
    """
    new_entries = []

    for ex in exchanges:
        if ex.get("roster_id") != character.roster_id:
            continue
        text = ex.get("text", "")
        if not text:
            continue

        text_lower = text.lower()
        for pattern in _COINING_PATTERNS:
            match = re.search(pattern, text_lower)
            if not match:
                continue
            raw_term = match.group(1).strip().strip("\"'")
            if not raw_term or len(raw_term) < 3:
                continue
            # Skip if it's a normal English word
            if raw_term.lower() in {
                "this", "that", "what", "here", "there", "just", "when",
                "sometimes", "always", "never", "maybe", "something", "anything"
            }:
                continue

            term = raw_term.lower()
            # Check if it already exists
            existing = db.query(LexiconEntry).filter(
                LexiconEntry.term == term
            ).first()
            if existing:
                # Mark this character as an adopter
                _add_adopter(existing, character.roster_id, db)
                continue

            # Infer rough meaning from surrounding context
            rough_meaning = _infer_meaning_from_context(text, term)

            entry = LexiconEntry(
                term=term,
                rough_meaning=rough_meaning,
                coined_by_id=character.id,
                coined_on_day=sim_day,
                category=_infer_category(text),
                adopters_json=json.dumps([character.roster_id]),
                adoption_count=1,
                community_adoption_level=0.0,
            )
            db.add(entry)
            new_entries.append(entry)
            logger.info(
                f"  LEXICON: {character.given_name or character.roster_id} "
                f"coined '{term}' — {rough_meaning}"
            )

    if new_entries:
        db.flush()

    return new_entries


def _add_adopter(entry: LexiconEntry, roster_id: str, db: Session) -> None:
    adopters = json.loads(entry.adopters_json or "[]")
    if roster_id not in adopters:
        adopters.append(roster_id)
        entry.adopters_json = json.dumps(adopters)
        entry.adoption_count = len(adopters)
        total = db.query(Character).filter(Character.alive == True).count()
        entry.community_adoption_level = min(len(adopters) / max(total, 1), 1.0)


def spread_term_adoption(term: str, adopter: Character, db: Session) -> None:
    """Mark a character as having adopted/used a community term."""
    entry = db.query(LexiconEntry).filter(LexiconEntry.term == term).first()
    if entry:
        _add_adopter(entry, adopter.roster_id, db)


def _infer_meaning_from_context(text: str, term: str) -> str:
    """
    Try to extract a short meaning from the surrounding sentence.
    Falls back to a generic placeholder.
    """
    # Find the sentence containing the term
    sentences = re.split(r'[.!?]', text)
    for sentence in sentences:
        if term in sentence.lower():
            cleaned = sentence.strip()
            if len(cleaned) > 10:
                # Return a truncated version as the rough meaning
                return cleaned[:80].strip()
    return "coined by this character — meaning unclear"


def _infer_category(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in ["body", "chest", "belly", "skin", "hair", "blood"]):
        return "body_part"
    if any(w in text_lower for w in ["feel", "emotion", "mood", "sad", "happy", "angry"]):
        return "emotional_state"
    if any(w in text_lower for w in ["together", "close", "touch", "holding", "near"]):
        return "behavior"
    if any(w in text_lower for w in ["place", "room", "space", "where"]):
        return "place"
    return "behavior"


def get_lexicon_for_dashboard(db: Session) -> list[dict]:
    """Returns all lexicon entries for the dashboard."""
    entries = db.query(LexiconEntry).order_by(
        LexiconEntry.adoption_count.desc(),
        LexiconEntry.coined_on_day.asc(),
    ).all()

    result = []
    for e in entries:
        coined_by_name = None
        if e.coined_by_id:
            char = db.query(Character).filter(Character.id == e.coined_by_id).first()
            if char:
                coined_by_name = char.given_name or char.roster_id

        result.append({
            "term": e.term,
            "rough_meaning": e.rough_meaning,
            "coined_by": coined_by_name,
            "coined_on_day": e.coined_on_day,
            "category": e.category,
            "adoption_count": e.adoption_count,
            "community_adoption_level": round(e.community_adoption_level or 0, 2),
        })

    return result
