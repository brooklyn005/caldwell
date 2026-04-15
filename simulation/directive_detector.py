"""
directive_detector.py — detects when one character directs another to perform
a specific action, and creates a pending action for that character next tick.

When A says "you should cook tomorrow" or "go check the perimeter" or
"I need you to hunt with us", that directive should produce actual behavior
from the target character — not just evaporate into the conversation void.

Directives are stored as high-weight action memories with memory_type="directive"
so the action generator picks them up and executes them with priority.
"""
import re
import logging
from sqlalchemy.orm import Session
from database.models import Character, Memory

logger = logging.getLogger("caldwell.directive")

# Patterns that indicate A is directing B to do something
# Captured group = the action being directed
DIRECTIVE_PATTERNS = [
    # Direct commands
    r"you should ([a-z]+(?:\s+\w+){0,4})",
    r"you need to ([a-z]+(?:\s+\w+){0,4})",
    r"you have to ([a-z]+(?:\s+\w+){0,4})",
    r"you must ([a-z]+(?:\s+\w+){0,4})",
    r"i want you to ([a-z]+(?:\s+\w+){0,4})",
    r"i need you to ([a-z]+(?:\s+\w+){0,4})",
    r"i'm asking you to ([a-z]+(?:\s+\w+){0,4})",
    r"can you ([a-z]+(?:\s+\w+){0,4})",
    r"will you ([a-z]+(?:\s+\w+){0,4})",
    r"could you ([a-z]+(?:\s+\w+){0,4})",
    # Imperatives
    r"^go ([a-z]+(?:\s+\w+){0,3})",
    r"make sure you ([a-z]+(?:\s+\w+){0,4})",
    r"promise (?:me )?you(?:'ll| will) ([a-z]+(?:\s+\w+){0,4})",
    r"your job is to ([a-z]+(?:\s+\w+){0,4})",
    r"you're going to ([a-z]+(?:\s+\w+){0,4})",
    r"i'm counting on you to ([a-z]+(?:\s+\w+){0,4})",
]

DIRECTIVE_RE = re.compile(
    "|".join(f"(?:{p})" for p in DIRECTIVE_PATTERNS),
    re.IGNORECASE | re.MULTILINE,
)

# Actions that should be filtered — too generic or not physical
SKIP_ACTIONS = {
    "be", "know", "think", "feel", "understand", "remember", "agree",
    "trust", "believe", "worry", "care", "stop", "stay", "just",
    "try", "keep", "come", "tell", "say", "talk", "listen",
    "not", "never", "always", "do", "get", "have", "make",
    "see", "look", "want", "need", "like", "love",
}

# Minimum length for an extracted action to be meaningful
MIN_ACTION_LEN = 4


def _extract_action(match_text: str) -> str | None:
    """Clean up a regex match into a usable action description."""
    action = match_text.strip().lower()
    # Remove trailing filler
    action = re.sub(r'\s+(for me|for us|today|tomorrow|now|please|okay|ok)$', '', action)
    action = action.strip()

    if len(action) < MIN_ACTION_LEN:
        return None

    first_word = action.split()[0]
    if first_word in SKIP_ACTIONS:
        return None

    return action


def detect_directives(
    exchanges: list[dict],
    char_a: Character,
    char_b: Character,
    sim_day: int,
    db: Session,
):
    """
    Scan conversation exchanges for directed commands from one character to another.

    When A directs B to perform an action, writes a high-weight directive memory
    for B so the action generator executes it next tick.

    Only fires on adult characters. Minors can be directed but don't issue directives.
    """
    for i, ex in enumerate(exchanges):
        text = ex.get("text", "")
        roster_id = ex.get("roster_id", "")
        if not text or roster_id == "OPERATOR":
            continue

        # Identify speaker and target
        if roster_id == char_a.roster_id:
            speaker = char_a
            target = char_b
        elif roster_id == char_b.roster_id:
            speaker = char_b
            target = char_a
        else:
            continue

        # Skip minors issuing directives
        if speaker.age < 16:
            continue

        # Skip if target already has a directive for today
        existing = db.query(Memory).filter(
            Memory.character_id == target.id,
            Memory.memory_type == "directive",
            Memory.sim_day == sim_day + 1,
        ).first()
        if existing:
            continue

        # Find directive pattern
        matches = DIRECTIVE_RE.finditer(text)
        for match in matches:
            # Get whichever capture group matched
            action_raw = next((g for g in match.groups() if g), None)
            if not action_raw:
                continue

            action = _extract_action(action_raw)
            if not action:
                continue

            speaker_name = speaker.given_name or speaker.physical_description[:30]
            target_name = target.given_name or target.physical_description[:30]

            # Write as a directive memory for the target — due next tick
            directive_content = (
                f"{speaker_name} told me to {action}. "
                f"It's sitting with me as something I'm supposed to do."
            )

            db.add(Memory(
                character_id=target.id,
                sim_day=sim_day + 1,  # Due next tick
                memory_type="directive",
                content=directive_content,
                emotional_weight=0.75,
                is_inception=False,
            ))

            logger.info(
                f"DIRECTIVE: {speaker.roster_id} → {target.roster_id}: "
                f"'{action}' (day {sim_day + 1})"
            )

            # Only capture the first meaningful directive per exchange
            break

    db.commit()
