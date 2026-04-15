"""
memory_writer.py — writes conversation memories and detects naming events.

Naming philosophy:
- Self-naming ONLY — a character claims their own name
- Speaker matched by roster_id directly
- Two detection modes:
  1. Trigger phrases: "call me X", "my name is X", "the name X", etc.
  2. Standalone name: single capitalized word as its own sentence when the
     exchange is clearly about names ("name", "myself", "chose", "call")
- Names already in use by other characters are rejected
- NEVER_NAMES exclusion list prevents common words
- Name-likeness check rejects short or all-caps words
"""
import re
import logging
from sqlalchemy.orm import Session
from database.models import Character, Memory, CharacterRelationship, InceptionEvent
from simulation.ai_caller import call_scoring_model
from simulation.cost_tracker import CostTracker

logger = logging.getLogger("caldwell.memory")

# Words that should NEVER be accepted as names
NEVER_NAMES = {
    "what", "when", "where", "who", "whom", "whose", "which",
    "how", "why", "whether", "whatever", "wherever", "whoever",
    "however", "whenever", "because", "since", "unless", "until",
    "though", "although", "while",
    "the", "a", "an", "you", "me", "we", "i", "it", "he",
    "she", "they", "them", "my", "your", "our", "his", "her",
    "its", "their", "this", "that", "these", "those",
    "and", "but", "or", "so", "yet", "nor", "in", "on", "at",
    "by", "to", "of", "for", "up", "as", "into", "out", "over",
    "down", "back", "off", "through", "with", "from", "about",
    "maybe", "perhaps", "actually", "really", "honestly",
    "clearly", "certainly", "definitely", "obviously", "probably",
    "simply", "therefore", "anyway", "besides", "instead",
    "otherwise", "suddenly", "also", "even", "still", "already",
    "always", "never", "sometimes", "often", "here", "there",
    "now", "then", "just", "only", "very", "too", "quite",
    "rather", "almost", "enough", "around", "again", "away",
    "right", "before", "after", "careful", "good", "bad", "big",
    "small", "old", "new", "first", "last", "same", "different",
    "other", "another", "such", "like", "than", "more", "most",
    "much", "many", "few", "less", "every", "each", "some", "any",
    "no", "not", "yes", "okay", "solid", "wrong", "sure",
    "glad", "happy", "sorry", "ready", "able", "free",
    "avoiding", "running", "hiding", "crying", "trying",
    "wondering", "hoping", "fearing", "leaving", "staying",
    "something", "nothing", "everything", "anything",
    "someone", "nobody", "everybody", "anybody",
    "people", "person", "place", "thing", "time", "day", "night",
    "food", "water", "body", "hand", "eyes", "voice", "mind",
    "life", "world", "way", "side", "point", "part", "kind",
    "name", "word", "thought", "feeling", "question", "answer",
    "listening", "watching", "thinking", "waiting", "feeling",
    "going", "coming", "looking", "saying", "knowing", "doing",
    "being", "having", "making", "taking", "getting", "giving",
    "wanting", "needing", "trying", "asking", "telling", "seeing",
    # Colors, numbers, directions
    "black", "white", "red", "blue", "green", "brown", "gray",
    "north", "south", "east", "west", "light", "dark",
    # Words from recent false positives
    "armor", "weight", "sharp", "clear", "simple", "strong",
    "solid", "hold", "suits", "feels", "like", "sounds",
}

# Trigger phrases — name must immediately follow
SELF_NAMING_TRIGGERS = [
    "call me ",
    "my name is ",
    "i am called ",
    "i call myself ",
    "i want to be called ",
    "name myself ",
    "the name ",          # "thinking about the name Eleanor"
    "my name—",
    "my name: ",
    "name is ",
    "chosen name ",
    "name i chose",
    "name for myself",
]

# Words that indicate a naming conversation is happening
NAME_DISCUSSION_WORDS = {
    "name", "call", "myself", "chose", "chosen", "naming",
    "called", "title", "identity", "known as",
}

# Self-referential words required for standalone detection
# Prevents "Eleanor. That's a strong name." from firing on the listener
SELF_REF_WORDS = {
    "myself", "for me", "i chose", "i want", "i've been",
    "i have been", "i need", "i am", "i'm", "my own",
    "for myself", "i call", "i named",
}


def _is_valid_name(name: str, used_names: set) -> bool:
    """Returns True if this word is a plausible unique name."""
    if not name or not name.isalpha():
        return False
    if len(name) < 3 or len(name) > 15:
        return False
    if name.lower() in NEVER_NAMES:
        return False
    if name.lower() in used_names:
        return False  # Already someone else's name
    # Must be title case (first letter upper, rest lower)
    if not (name[0].isupper() and name[1:].islower()):
        return False
    return True


def _get_used_names(db: Session) -> set:
    """Returns set of all currently assigned names (lowercase)."""
    named = db.query(Character).filter(
        Character.given_name != None,
        Character.alive == True,
    ).all()
    return {c.given_name.lower() for c in named}


def _try_trigger_naming(text: str, text_lower: str, used_names: set) -> str | None:
    """
    Look for a name immediately following a trigger phrase.
    Returns the name if found, None otherwise.
    """
    for trigger in SELF_NAMING_TRIGGERS:
        idx = text_lower.find(trigger)
        if idx == -1:
            continue
        after = text[idx + len(trigger):].strip()
        word_match = re.match(r'([A-Za-z]{3,15})', after)
        if not word_match:
            continue
        name = word_match.group(1).capitalize()
        if _is_valid_name(name, used_names):
            return name
    return None


def _try_standalone_naming(text: str, text_lower: str, used_names: set) -> str | None:
    """
    Detect standalone name: a single capitalized word appearing as its own
    sentence when the exchange is clearly about naming AND contains
    self-referential language.

    Catches:
      "I've been thinking about a name for myself. Eleanor. It feels right."
      "About the one I chose for myself. Eleanor. It feels like armor."

    Rejects:
      "Eleanor. That's a strong name." (no self-reference — listener, not namer)
      "Eleanor. I like the sound of it." (no self-reference)
    """
    # Only bother if this exchange is about names
    if not any(w in text_lower for w in NAME_DISCUSSION_WORDS):
        return None

    # Must also contain self-referential language — otherwise it's a listener
    # reacting to someone else's name, not claiming their own
    if not any(w in text_lower for w in SELF_REF_WORDS):
        return None

    # Split into sentences and look for single-word sentences
    sentences = re.split(r'[.!?]', text)
    for sentence in sentences:
        s = sentence.strip()
        # Exactly one word, title case, plausible length
        if re.match(r'^[A-Z][a-z]{3,14}$', s):
            name = s
            if _is_valid_name(name, used_names):
                return name
    return None


def write_memory(
    character: Character,
    content: str,
    sim_day: int,
    db: Session,
    emotional_weight: float = 0.5,
    memory_type: str = "conversation",
    is_inception: bool = False,
):
    mem = Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type=memory_type,
        content=content,
        emotional_weight=emotional_weight,
        is_inception=is_inception,
    )
    db.add(mem)
    db.commit()


async def extract_and_write_memories(
    char_a: Character,
    char_b: Character,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
):
    """
    After a conversation, call the scoring model to extract a 1-sentence memory
    for each participant and write it to the DB.
    """
    if not exchanges:
        return

    transcript_lines = []
    for ex in exchanges:
        roster = ex.get("roster_id", "?")
        text = ex.get("text", "")
        if text and roster != "OPERATOR":
            transcript_lines.append(f"{roster}: {text}")
    transcript = "\n".join(transcript_lines)
    if not transcript:
        return

    for char, other in [(char_a, char_b), (char_b, char_a)]:
        char_display = char.given_name or char.roster_id
        other_display = other.given_name or other.roster_id

        prompt = (
            f"Conversation transcript:\n{transcript}\n\n"
            f"Write ONE memory sentence from {char_display}\'s point of view. "
            f"First person, past tense, max 25 words. "
            f"Focus on what felt emotionally significant or practically important. "
            f"Refer to the other person as \'{other_display}\'. "
            f"Do not use cultural references or formal language. "
            f"Just the raw feeling or observation."
        )

        try:
            memory_text, in_tok, out_tok = await call_scoring_model(
                system_prompt="You write brief honest memory entries. One sentence, first person past tense.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
            )
            cost_tracker.record("haiku", in_tok, out_tok)
            if memory_text and memory_text.strip() not in ("...", ""):
                write_memory(
                    character=char,
                    content=memory_text.strip().strip('"'),
                    sim_day=sim_day,
                    db=db,
                    emotional_weight=0.6,
                    memory_type="conversation",
                )
        except Exception as e:
            logger.debug(f"Memory extraction failed for {char.roster_id}: {e}")

    logger.info(f"Memories written for {char_a.roster_id} and {char_b.roster_id}")


def update_relationship(
    char_a: Character,
    char_b: Character,
    db: Session,
):
    """Increment familiarity and trust for both directions of a relationship."""
    for src, tgt in [(char_a, char_b), (char_b, char_a)]:
        rel = (
            db.query(CharacterRelationship)
            .filter(
                CharacterRelationship.from_character_id == src.id,
                CharacterRelationship.to_character_id == tgt.id,
            )
            .first()
        )
        if rel is None:
            rel = CharacterRelationship(
                from_character_id=src.id,
                to_character_id=tgt.id,
                trust_level=0.0,
                familiarity=0.0,
                interaction_count=0,
            )
            db.add(rel)
            db.flush()

        rel.familiarity = min(1.0, (rel.familiarity or 0.0) + 0.05)
        rel.trust_level = min(1.0, (rel.trust_level or 0.0) + 0.02)
        rel.interaction_count = (rel.interaction_count or 0) + 1

    db.commit()


def detect_names(
    exchanges: list[dict],
    char_a: Character,
    char_b: Character,
    db: Session,
):
    """
    Detect self-naming events in conversation exchanges.

    Two detection modes:
    1. Trigger phrases — "call me X", "my name is X", "the name X", etc.
    2. Standalone name — single capitalized word as sentence in name discussion

    Speaker matched by roster_id. Duplicates and common words rejected.
    """
    if char_a.given_name and char_b.given_name:
        return

    used_names = _get_used_names(db)

    for ex in exchanges:
        roster_id = ex.get("roster_id", "")
        text = ex.get("text", "")
        if not text or roster_id == "OPERATOR":
            continue

        # Match speaker to character by roster_id
        if roster_id == char_a.roster_id:
            speaker = char_a
        elif roster_id == char_b.roster_id:
            speaker = char_b
        else:
            continue

        if speaker.given_name:
            continue

        text_lower = text.lower()

        # Mode 1: trigger phrase detection
        name = _try_trigger_naming(text, text_lower, used_names)
        if name:
            speaker.given_name = name
            used_names.add(name.lower())
            logger.info(f"NAMING (trigger): {speaker.roster_id} -> {name}")
            continue

        # Mode 2: standalone name in name-discussion context
        name = _try_standalone_naming(text, text_lower, used_names)
        if name:
            speaker.given_name = name
            used_names.add(name.lower())
            logger.info(f"NAMING (standalone): {speaker.roster_id} -> {name}")

    db.commit()


def write_inception_memory(
    character: Character,
    thought_content: str,
    sim_day: int,
    db: Session,
):
    existing = db.query(Memory).filter(
        Memory.character_id == character.id,
        Memory.is_inception == True,
        Memory.content == thought_content,
    ).first()
    if not existing:
        db.add(Memory(
            character_id=character.id,
            sim_day=sim_day,
            memory_type="inception",
            content=thought_content,
            emotional_weight=0.9,
            is_inception=True,
        ))
        db.commit()


def mark_inceptions_delivered(
    character: Character,
    sim_day: int,
    db: Session,
):
    import json
    events = (
        db.query(InceptionEvent)
        .filter(InceptionEvent.injected_at_day == sim_day)
        .all()
    )
    for ev in events:
        targets = json.loads(ev.target_roster_ids_json or "[]")
        if character.roster_id in targets:
            write_inception_memory(character, ev.thought_content, sim_day, db)


def get_recent_memories(
    character: Character,
    db: Session,
    limit: int = 8,
) -> list[str]:
    all_memories = []
    seen_ids = set()

    inceptions = (
        db.query(Memory)
        .filter(Memory.character_id == character.id, Memory.is_inception == True)
        .order_by(Memory.sim_day.desc()).all()
    )
    for m in inceptions:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    significant = (
        db.query(Memory)
        .filter(
            Memory.character_id == character.id,
            Memory.emotional_weight >= 0.65,
        )
        .order_by(Memory.emotional_weight.desc()).limit(12).all()
    )
    for m in significant:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    recent = (
        db.query(Memory)
        .filter(Memory.character_id == character.id)
        .order_by(Memory.sim_day.desc()).limit(10).all()
    )
    for m in recent:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    all_memories.sort(key=lambda m: m.sim_day)
    return [
        f"[Day {m.sim_day}] {m.content}"
        + (" *(a thought that came to you)*" if m.is_inception else "")
        for m in all_memories
    ]
