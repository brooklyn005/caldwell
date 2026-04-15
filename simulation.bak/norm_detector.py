"""
norm_detector.py — detects emerging community norms from conversation text
and writes them to the NormRecord table.

Norms emerge when characters express shared understanding, agreement,
or describe behaviors as expected/established. This is NOT imposed —
it surfaces what characters are actually building through talk.
"""
import re
import logging
import random
from sqlalchemy.orm import Session
from database.models import NormRecord, Character

logger = logging.getLogger("caldwell.norms")

# Phrases that indicate agreement or shared understanding is forming
AGREEMENT_PATTERNS = [
    r"we (could|should|might|can) agree",
    r"everyone (should|needs to|has to|ought to)",
    r"that('s| is) (the rule|what we do|how it works|understood|settled|decided)",
    r"we('ve| have) decided",
    r"we('ve| have) agreed",
    r"let('s| us) say that",
    r"that('s| is) fair",
    r"that makes sense( for all| to everyone| here)",
    r"no one (should|can|gets to|is allowed to)",
    r"(always|never) (happens|gets|goes|takes|leaves|comes)",
    r"people here (don't|do|always|never|tend to|seem to)",
    r"(that's|it's) (just )?how (it works|things work|we do it) here",
    r"we (all|both) know that",
    r"(understood|agreed|settled|clear)",
]

AGREEMENT_RE = re.compile("|".join(AGREEMENT_PATTERNS), re.IGNORECASE)

# Patterns that indicate a norm is ACTIONABLE — implies regular physical activity
# Keyed by action_verb → list of patterns that indicate that activity
ACTIONABLE_PATTERNS = {
    "hunt": [
        r"we (should|will|can|need to) hunt",
        r"someone (should|needs to|has to) hunt",
        r"hunting (rule|agreement|rotation|schedule|duty|party)",
        r"take turns hunting",
        r"hunting group",
        r"go out (and )?hunt",
    ],
    "fish": [
        r"we (should|will|can) fish",
        r"someone (should|needs to) fish",
        r"fishing (rule|rotation|duty|schedule)",
        r"go (out )?fishing",
        r"take turns fishing",
    ],
    "cook": [
        r"someone (should|needs to|has to|will) cook",
        r"we (should|will|take turns) cook",
        r"cooking (duty|rotation|rule|schedule|responsibility)",
        r"take turns (with )?cook",
        r"whoever cooks",
        r"cook for (everyone|the group|people|us all)",
    ],
    "forage": [
        r"we (should|will|can) forage",
        r"someone (should|needs to) forage",
        r"foraging (party|group|rotation|duty|schedule)",
        r"go (out )?forag",
    ],
    "gather": [
        r"we (should|will) gather",
        r"gathering (food|supplies|resources)",
        r"someone (should|needs to) gather",
        r"take turns gather",
        r"gather(ing)? (and )?distribut",
    ],
    "build": [
        r"we (should|will|can|need to) build",
        r"someone (should|needs to) build",
        r"building (rule|schedule|rotation|duty|project|work)",
        r"work on (building|construction|structure)",
        r"build (shelter|space|structure|something)",
    ],
    "repair": [
        r"we (should|will) repair",
        r"someone (should|needs to) fix",
        r"repair(ing)? (things|what|the)",
        r"keep (things|it|the) (working|repaired|maintained)",
        r"maintenance (rule|schedule|duty)",
    ],
    "patrol": [
        r"we (should|will) patrol",
        r"someone (should|needs to) patrol",
        r"(walk|check) the (perimeter|boundary|edges|outskirts)",
        r"patrol(ing)? (duty|rotation|schedule|rule)",
        r"keep watch",
    ],
    "teach": [
        r"we (should|will) teach",
        r"someone (should|needs to) teach",
        r"pass(ing)? (on|down) (knowledge|skills|what)",
        r"teach(ing)? (the|each|others|younger|what)",
        r"share (knowledge|skills|what we know)",
    ],
    "tend": [
        r"someone (should|needs to) tend",
        r"tending (to )?(the|things|what|sick|injured)",
        r"we (should|will) tend",
        r"take care of (the|things|what|people)",
        r"maintenance (work|duty)",
    ],
}

import re as _re
ACTIONABLE_RES = {
    verb: _re.compile("|".join(patterns), _re.IGNORECASE)
    for verb, patterns in ACTIONABLE_PATTERNS.items()
}


def _detect_action_verb(text: str) -> str | None:
    """Returns the action verb if text describes an actionable norm, else None."""
    for verb, pattern in ACTIONABLE_RES.items():
        if pattern.search(text):
            return verb
    return None


# Topic categories for norm classification
NORM_TOPICS = {
    "food": ["food", "eat", "hungry", "market", "share", "hoard", "take", "leave"],
    "space": ["space", "place", "room", "sleep", "territory", "move", "stay"],
    "body": ["touch", "body", "sex", "naked", "bare", "close", "distance"],
    "conflict": ["fight", "argue", "hurt", "harm", "attack", "threaten", "anger"],
    "resource": ["water", "supply", "tool", "use", "keep", "borrow", "belong"],
    "care": ["sick", "hurt", "help", "care", "tend", "support", "leave alone"],
    "decision": ["decide", "choose", "vote", "agree", "discuss", "everyone", "together"],
}


def _classify_norm(text: str) -> str:
    text_lower = text.lower()
    scores = {topic: 0 for topic in NORM_TOPICS}
    for topic, keywords in NORM_TOPICS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[topic] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "general"


def _extract_norm_description(text: str, max_len: int = 200) -> str:
    """Extract a clean description of the norm being formed."""
    # Take the sentence containing the agreement pattern
    sentences = re.split(r'[.!?]', text)
    for s in sentences:
        if AGREEMENT_RE.search(s):
            desc = s.strip()
            if len(desc) > 20:
                return desc[:max_len]
    return text[:max_len]


def detect_norms_from_conversation(
    exchanges: list[dict],
    char_a: Character,
    char_b: Character,
    sim_day: int,
    db: Session,
):
    """
    Scan conversation exchanges for emerging norm language.
    When found, write or reinforce a NormRecord.

    Only fires on exchanges with clear agreement/shared-understanding language.
    Does not require both parties to agree — one party articulating a norm
    that goes unchallenged is enough (soft norm).
    """
    for ex in exchanges:
        text = ex.get("text", "")
        if not text or ex.get("roster_id") == "OPERATOR":
            continue

        if not AGREEMENT_RE.search(text):
            continue

        # Found agreement language — extract and classify
        description = _extract_norm_description(text)
        norm_type = _classify_norm(text)

        if len(description) < 20:
            continue

        # Check if a similar norm already exists
        existing = (
            db.query(NormRecord)
            .filter(
                NormRecord.norm_type == norm_type,
                NormRecord.is_active == True,
            )
            .all()
        )

        # Simple dedup — don't create duplicate norms for same topic
        # within a 10-day window
        recent_similar = [
            n for n in existing
            if abs(n.emerged_day - sim_day) < 10
        ]

        # Check if this norm is actionable (implies recurring physical activity)
        action_verb = _detect_action_verb(text)
        is_actionable = action_verb is not None

        if recent_similar:
            # Reinforce the most recent similar norm
            norm = max(recent_similar, key=lambda n: n.emerged_day)
            norm.reinforced_count += 1
            norm.strength = min(1.0, norm.strength + 0.05)
            # Upgrade to actionable if newly detected
            if is_actionable and not getattr(norm, 'is_actionable', False):
                try:
                    db.execute(
                        __import__('sqlalchemy').text(
                            "UPDATE norm_records SET is_actionable=1, action_verb=:verb "
                            "WHERE id=:id"
                        ),
                        {"verb": action_verb, "id": norm.id}
                    )
                except Exception:
                    pass
            logger.debug(f"NORM reinforced (day {sim_day}): [{norm_type}] {description[:60]}")
        else:
            # Create new norm
            norm = NormRecord(
                norm_type=norm_type,
                description=description,
                emerged_day=sim_day,
                strength=0.1,
                violated_count=0,
                reinforced_count=1,
                is_active=True,
            )
            db.add(norm)
            db.flush()
            # Set actionable fields if applicable
            if is_actionable:
                try:
                    db.execute(
                        __import__('sqlalchemy').text(
                            "UPDATE norm_records SET is_actionable=1, action_verb=:verb, "
                            "action_frequency_days=2 WHERE id=:id"
                        ),
                        {"verb": action_verb, "id": norm.id}
                    )
                    logger.info(
                        f"ACTIONABLE NORM emerged (day {sim_day}): "
                        f"[{norm_type}] verb={action_verb} {description[:60]}"
                    )
                except Exception as e:
                    logger.debug(f"Could not set actionable: {e}")
            else:
                logger.info(f"NORM emerged (day {sim_day}): [{norm_type}] {description[:80]}")

    db.commit()


def get_active_norms_for_prompt(db: Session, limit: int = 3) -> str:
    """
    Returns a formatted string of active norms for inclusion in character prompts.
    Only returns norms with meaningful strength.
    """
    norms = (
        db.query(NormRecord)
        .filter(
            NormRecord.is_active == True,
            NormRecord.strength >= 0.15,
        )
        .order_by(NormRecord.strength.desc())
        .limit(limit)
        .all()
    )

    if not norms:
        return ""

    lines = ["THINGS THAT SEEM UNDERSTOOD HERE (not rules — just what people seem to do):"]
    for n in norms:
        lines.append(f"- {n.description}")

    return "\n".join(lines)
