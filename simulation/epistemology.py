"""
epistemology.py — belief formation from ambiguous private signals.

Characters don't receive facts about private behaviors — they accumulate
signals (sensory fragments, overheard moments, ambiguous observations).
Those signals build into beliefs that are personal, errorful, and contested.

Belief states:
  confusion      — 1 signal, no pattern yet
  tentative      — 2+ signals, a working theory
  labeled        — character has named/tagged it (or adopted someone's term)
  social_concept — 3+ sources, belief is now shared with others

The "No Instant Understanding" rule: signal_count >= 2 OR source_count >= 2
to advance past confusion.
"""
import json
import logging
from sqlalchemy.orm import Session
from database.models import Character, CharacterBelief

logger = logging.getLogger("caldwell.epistemology")

# Signals keyed by subject category — ambiguous sensory/behavioral fragments
_SIGNAL_POOL = {
    "shared_touch": [
        "two people very close, bodies almost touching",
        "hands on each other in a way that wasn't about work",
        "heard a long exhale, like something had just happened",
        "they pulled apart when I came in",
        "the stillness between them was different from ordinary stillness",
    ],
    "private_sounds": [
        "rhythmic sound from behind the closed door",
        "heavy breathing, not from effort — from something else",
        "a low sound I didn't recognize, then silence",
        "someone making a sound I'd never heard before",
        "the kind of noise that stops when they know you're there",
    ],
    "concealed_activity": [
        "they stopped when I walked in — they'd been doing something",
        "locked from the inside, no one answered",
        "moved away fast when the light changed",
        "wouldn't say what they'd been doing there",
        "came out looking different — flushed, rearranging",
    ],
    "body_change_female": [
        "her body is changing in ways I don't understand",
        "she seemed in pain but wouldn't name what was wrong",
        "something is different about her chest — she covers it now",
        "blood, but not from a wound — she didn't seem afraid of it",
        "her belly is getting larger and she won't say why",
    ],
    "body_change_male": [
        "his voice changed — lower now, different quality",
        "body hair where there wasn't before",
        "he's different lately — restless in a way I don't have a word for",
        "something changed in how he holds himself",
    ],
    "pair_bonding": [
        "they keep finding each other in the same place",
        "something passes between them that I can't name",
        "they touch each other more than necessary",
        "one of them looks for the other before they look for anyone else",
    ],
    "unknown_distress": [
        "she was shaking, couldn't say from what",
        "he went quiet in a way I hadn't seen before — not tired quiet",
        "the kind of crying that isn't about sadness exactly",
        "something happened to them that they won't put into words",
    ],
}

# Maps scene types and memory keywords to belief subjects
_SCENE_TO_SUBJECT = {
    "quiet_intimacy": ["shared_touch", "private_sounds", "concealed_activity"],
    "body_change": ["body_change_female", "body_change_male"],
    "grief": ["unknown_distress"],
}

_KEYWORD_TO_SUBJECT = {
    "menstrual": "body_change_female",
    "menstruation": "body_change_female",
    "pregnant": "body_change_female",
    "belly": "body_change_female",
    "cycle": "body_change_female",
    "chest": "body_change_female",
    "voice changed": "body_change_male",
    "locked": "concealed_activity",
    "door": "concealed_activity",
    "alone together": "concealed_activity",
    "breathing": "private_sounds",
    "touching": "shared_touch",
    "skin": "shared_touch",
    "close together": "shared_touch",
    "bonding": "pair_bonding",
    "keep finding": "pair_bonding",
}


def record_private_signal(
    character: Character,
    signal_text: str,
    subject: str,
    source_type: str,
    sim_day: int,
    db: Session,
) -> CharacterBelief | None:
    """
    Record a new signal for a character about a private subject.
    Advances belief state when thresholds are met.
    source_type: 'direct_witness', 'overheard', 'told_by_other', 'own_experience'
    """
    belief = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == character.id,
        CharacterBelief.subject == subject,
    ).first()

    if not belief:
        belief = CharacterBelief(
            character_id=character.id,
            subject=subject,
            belief_state="confusion",
            signals_json=json.dumps([]),
            signal_count=0,
            source_count=0,
            confidence=0.1,
            coherence=0.1,
            first_signal_day=sim_day,
            last_updated_day=sim_day,
        )
        db.add(belief)

    # Add signal
    signals = json.loads(belief.signals_json or "[]")
    signals.append({"text": signal_text, "source": source_type, "day": sim_day})
    belief.signals_json = json.dumps(signals[-10:])  # keep last 10
    belief.signal_count = (belief.signal_count or 0) + 1
    belief.last_updated_day = sim_day

    # Track distinct source types
    existing_sources = {s.get("source") for s in signals}
    belief.source_count = len(existing_sources)

    # Advance belief state — No Instant Understanding rule
    _advance_belief_state(belief)

    db.flush()
    return belief


def _advance_belief_state(belief: CharacterBelief) -> None:
    """Ratchets belief forward. States only advance, never regress."""
    count = belief.signal_count or 0
    sources = belief.source_count or 0

    if belief.belief_state == "confusion":
        if count >= 2 or sources >= 2:
            belief.belief_state = "tentative"
            belief.confidence = 0.3
            belief.coherence = 0.25
    elif belief.belief_state == "tentative":
        if count >= 4 or sources >= 3:
            belief.belief_state = "labeled"
            belief.confidence = 0.55
            belief.coherence = 0.5
    elif belief.belief_state == "labeled":
        if count >= 6 or sources >= 4:
            belief.belief_state = "social_concept"
            belief.confidence = 0.75
            belief.coherence = 0.7


def detect_subject_from_text(text: str) -> str | None:
    """
    Scan a piece of text for keywords that suggest a private subject.
    Returns the most likely subject category or None.
    """
    text_lower = text.lower()
    for keyword, subject in _KEYWORD_TO_SUBJECT.items():
        if keyword in text_lower:
            return subject
    return None


def get_belief_prompt_block(character: Character, db: Session) -> str:
    """
    Returns a natural language block about what this character currently believes
    about private subjects. Injected into system prompt.
    """
    beliefs = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == character.id,
        CharacterBelief.belief_state.in_(["tentative", "labeled", "social_concept"]),
    ).all()

    if not beliefs:
        return ""

    lines = []
    for b in beliefs:
        if b.belief_state == "tentative":
            lines.append(
                f"- You've noticed something about {_subject_label(b.subject)} "
                f"but don't have a clear picture. "
                f"You have a working theory, but it might be wrong."
            )
        elif b.belief_state == "labeled":
            tag = f" You call it \"{b.vocabulary_tag}\"." if b.vocabulary_tag else ""
            lines.append(
                f"- You have a name (or at least a description) for {_subject_label(b.subject)}.{tag} "
                f"You're fairly sure you understand what it is."
            )
        elif b.belief_state == "social_concept":
            tag = f" The word you use is \"{b.vocabulary_tag}\"." if b.vocabulary_tag else ""
            lines.append(
                f"- {_subject_label(b.subject).capitalize()} is something the community is beginning "
                f"to have shared language for.{tag}"
            )

    if not lines:
        return ""

    return "THINGS YOU'VE NOTICED BUT DON'T FULLY UNDERSTAND:\n" + "\n".join(lines)


def _subject_label(subject: str) -> str:
    labels = {
        "shared_touch": "what happens between two people when they are very close",
        "private_sounds": "sounds that come from behind closed doors",
        "concealed_activity": "what people do when they don't want to be seen",
        "body_change_female": "the changes happening in some of the women's bodies",
        "body_change_male": "the changes happening in some of the younger men's bodies",
        "pair_bonding": "the way certain two people keep gravitating toward each other",
        "unknown_distress": "what happens to people who are in a certain kind of pain",
    }
    return labels.get(subject, subject.replace("_", " "))


def get_all_beliefs_for_dashboard(db: Session) -> list[dict]:
    """Returns all non-trivial beliefs for the dashboard knowledge panel."""
    beliefs = db.query(CharacterBelief).filter(
        CharacterBelief.belief_state != "confusion"
    ).all()

    result = []
    for b in beliefs:
        char = db.query(Character).filter(
            Character.id == b.character_id
        ).first()
        if not char:
            continue
        result.append({
            "character": char.given_name or char.roster_id,
            "roster_id": char.roster_id,
            "subject": b.subject,
            "belief_state": b.belief_state,
            "signal_count": b.signal_count,
            "source_count": b.source_count,
            "vocabulary_tag": b.vocabulary_tag,
            "confidence": round(b.confidence or 0, 2),
            "first_signal_day": b.first_signal_day,
            "last_updated_day": b.last_updated_day,
        })

    return sorted(result, key=lambda x: (-x["signal_count"], x["character"]))
