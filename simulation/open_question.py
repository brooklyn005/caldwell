"""
open_question.py — persistent unresolved questions that drive character behavior.

DESIGN:
- Questions drive WHO characters talk to next, not just what they say
- Characters follow a social investigation path: witness → intermediary → source
- Attempts only count when the question is actually surfaced in conversation
- No hard attempt limit — questions resolve naturally through directed pairing
- After intermediary conversation, intensity INCREASES and routes toward the source
"""
import logging
from sqlalchemy.orm import Session
from database.models import Character, OpenQuestion, CharacterRelationship
from simulation.ai_caller import call_scoring_model
from simulation.cost_tracker import CostTracker

logger = logging.getLogger("caldwell.questions")

MAX_ACTIVE_QUESTIONS = 5
MIN_INTENSITY = 0.0  # no floor — questions decay fully to zero and are auto-dropped
INTENSITY_DECAY_UNSURFACED = 0.05  # per tick when question NOT surfaced
INTENSITY_BOOST_FIRST_PARTIAL = 0.15  # only first intermediary boosts — after that, decay toward source


# ── Partner selection ─────────────────────────────────────────────────────────

def get_question_relevant_partner(
    character: Character,
    exclude_ids: set,
    sim_day: int,
    db: Session,
) -> Character | None:
    """
    For a character with pressing open questions, find the most relevant
    conversation partner to help them make progress.

    Priority:
    1. The subject of the question (person the question is about), if trust > 0
    2. Someone with high familiarity with the subject (intermediary path)
    3. Someone with Knowledge/Curiosity drive who might have insight
    """
    active = (
        db.query(OpenQuestion)
        .filter(
            OpenQuestion.character_id == character.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
            OpenQuestion.intensity >= 0.5,
        )
        .order_by(OpenQuestion.intensity.desc())
        .first()
    )
    if not active:
        return None

    q_text = active.question_text.lower()
    all_alive = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
        Character.id != character.id,
        Character.id.notin_(exclude_ids),
    ).all()

    if not all_alive:
        return None

    # Build relationship map for this character
    rels = db.query(CharacterRelationship).filter(
        CharacterRelationship.from_character_id == character.id
    ).all()
    rel_by_id = {r.to_character_id: r for r in rels}

    # Score each potential partner
    scored = []
    for candidate in all_alive:
        score = 0.0
        rel = rel_by_id.get(candidate.id)

        # Is this person named or described in the question?
        candidate_name = (candidate.given_name or "").lower()
        if candidate_name and len(candidate_name) > 2 and candidate_name in q_text:
            # This is the SOURCE — highest priority if trust is positive
            trust = rel.trust_level if rel else 0.0
            if trust >= -0.1:  # not actively hostile
                score += 5.0
            else:
                score += 2.0  # still relevant even if tense

        # High familiarity with the character — good intermediary
        if rel and rel.familiarity >= 0.3:
            score += rel.familiarity * 2.0

        # Knowledge/Curiosity drive — likely to have insight
        if candidate.core_drive in ("Knowledge", "Curiosity", "Connection"):
            score += 0.8

        # Recently interacted with — fresh context
        if rel and rel.last_interacted_day and sim_day - rel.last_interacted_day <= 3:
            score += 0.5

        # Avoid re-pairing with someone they just talked to today
        if rel and rel.last_interacted_day == sim_day:
            score -= 3.0

        scored.append((score, candidate))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    # Only override normal pairing if there's a genuinely relevant partner
    if best_score >= 1.5:
        return best
    return None


# ── Extraction ────────────────────────────────────────────────────────────────

async def extract_open_questions(
    character: Character,
    other: Character,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
):
    """
    After a conversation, check if this character is left with an unresolved
    social question about someone's behavior, motive, or a witnessed event.
    """
    if not exchanges:
        return

    active_count = (
        db.query(OpenQuestion)
        .filter(
            OpenQuestion.character_id == character.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        )
        .count()
    )
    if active_count >= MAX_ACTIVE_QUESTIONS:
        return

    transcript = _build_transcript(exchanges)
    char_name = character.given_name or character.roster_id

    system = (
        "You identify whether a person is left with a significant unresolved SOCIAL question "
        "after a conversation. This must be about a specific person's hidden motive, "
        "unexplained behavior, a witnessed act that doesn't make sense, "
        "or a social dynamic pointing to something hidden. "
        "NOT about facts, vocabulary, definitions, survival mechanics, or abstract philosophy. "
        "Write in first person present tense. If nothing qualifies: none"
    )
    prompt = (
        f"Character: {char_name} (drive: {character.core_drive})\n"
        f"Natural tendency: {character.natural_tendency}\n\n"
        f"Conversation:\n{transcript}\n\n"
        f"Is {char_name} left with an unresolved question about WHY a specific person "
        f"did something, WHAT their behavior reveals, or WHAT someone is hiding or not saying?\n\n"
        f"QUALIFIES: unexplained behavior, witnessed unfamiliar act with social significance, "
        f"someone hedged in a revealing way, a detail pointing to something hidden\n"
        f"DOES NOT QUALIFY: vocabulary learning, abstract philosophy, fully explained things\n\n"
        f"If yes: one sentence, first person, present tense, name the specific person.\n"
        f"Good: 'What came out of Kofi's body — and why does he need to be alone when it happens?'\n"
        f"Good: 'Why did Tano grip that basket so tightly — what does he know?'\n"
        f"If no: exactly: none"
    )

    try:
        result, in_tok, out_tok = await call_scoring_model(
            system_prompt=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=70,
        )
        cost_tracker.record("haiku", in_tok, out_tok)
        result = result.strip().strip('"')

        if result.lower() == "none" or not result or len(result) < 10:
            return

        # Don't duplicate existing questions
        existing = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == character.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).all()
        for eq in existing:
            if _similar(eq.question_text, result):
                eq.intensity = min(1.0, eq.intensity + 0.15)
                db.commit()
                logger.info(f"  Reinforced: [{char_name}] {eq.question_text[:60]}")
                return

        q = OpenQuestion(
            character_id=character.id,
            question_text=result,
            source_type="conversation",
            source_day=sim_day,
            emerged_day=sim_day,
            intensity=0.75,
            last_surfaced_day=sim_day,
            attempts=0,
            dropped=False,
        )
        db.add(q)
        db.commit()
        logger.info(f"  NEW question: [{char_name}] {result[:80]}")

    except Exception as e:
        logger.debug(f"Question extraction failed for {character.roster_id}: {e}")


async def extract_question_from_memory(
    character: Character,
    memory_text: str,
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
    source_type: str = "observation",
):
    """Generate a question from a standalone memory — for backfill and discoveries."""
    if not memory_text:
        return

    active_count = db.query(OpenQuestion).filter(
        OpenQuestion.character_id == character.id,
        OpenQuestion.resolved == False,
        OpenQuestion.dropped == False,
    ).count()
    if active_count >= MAX_ACTIVE_QUESTIONS:
        return

    char_name = character.given_name or character.roster_id
    system = (
        "You identify whether an experience leaves a person with a significant unresolved social question "
        "about a person's behavior or motive — not facts or definitions. "
        "First person present tense. If nothing qualifies: none"
    )
    prompt = (
        f"Character: {char_name} (drive: {character.core_drive})\n\n"
        f"Experience: {memory_text}\n\n"
        f"Does this leave {char_name} with an unresolved question about WHY a specific person "
        f"did something or what their behavior means?\n"
        f"If yes: one sentence, first person, present tense, name the person.\n"
        f"If no: exactly: none"
    )

    try:
        result, in_tok, out_tok = await call_scoring_model(
            system_prompt=system,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=70,
        )
        cost_tracker.record("haiku", in_tok, out_tok)
        result = result.strip().strip('"')

        if result.lower() == "none" or not result or len(result) < 10:
            return

        existing = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == character.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).all()
        for eq in existing:
            if _similar(eq.question_text, result):
                eq.intensity = min(1.0, eq.intensity + 0.1)
                db.commit()
                return

        q = OpenQuestion(
            character_id=character.id,
            question_text=result,
            source_type=source_type,
            source_day=sim_day,
            emerged_day=sim_day,
            intensity=0.65,
            last_surfaced_day=sim_day,
            attempts=0,
            dropped=False,
        )
        db.add(q)
        db.commit()
        logger.info(f"  Question from memory: [{char_name}] {result[:80]}")

    except Exception as e:
        logger.debug(f"Memory question extraction failed: {e}")


# ── Resolution ────────────────────────────────────────────────────────────────

async def check_resolution(
    character: Character,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
):
    """
    Smart resolution check with three fixes:
    1. Uses current_understanding as context so the model knows what was already known
    2. Character-decides: uses in-character reflection to detect resolution
    3. Breaks the intermediary loop: after first partial, subsequent partials decay
    """
    active = db.query(OpenQuestion).filter(
        OpenQuestion.character_id == character.id,
        OpenQuestion.resolved == False,
        OpenQuestion.dropped == False,
    ).all()
    if not active or not exchanges:
        return

    transcript = _build_transcript(exchanges)
    char_name = character.given_name or character.roster_id

    for question in active:
        surfaced = _question_surfaced_in_transcript(question.question_text, transcript)
        if not surfaced:
            days_since = sim_day - (question.last_surfaced_day or question.emerged_day)
            if days_since >= 2:
                question.intensity = max(0.0, question.intensity - INTENSITY_DECAY_UNSURFACED)
                if question.intensity <= 0:
                    question.dropped = True
                    question.resolved = True
                    question.resolved_day = sim_day
                    question.resolution_text = "Faded — never surfaced"
            # If stuck in intermediary loop, push toward source by decaying
            if question.source_type == "intermediary_partial":
                question.intensity = max(0.0, question.intensity - 0.1)
            continue

        question.last_surfaced_day = sim_day
        question.times_surfaced = (question.times_surfaced or 0) + 1
        question.attempts = (question.attempts or 0) + 1

        prior_understanding = question.current_understanding or "Nothing yet — this question just emerged."

        # ── Step 1: Character-decides reflection ─────────────────────────────
        # Ask in-character what they still don't understand — more reliable than
        # external scoring because the character has the full context
        reflection_system = (
            "You are writing a single sentence from a character's internal perspective "
            "immediately after a conversation. Be honest and specific. "
            "If the question was answered, say so plainly. "
            "If something was learned but questions remain, name what's still unclear. "
            "If nothing was resolved, say nothing happened."
        )
        reflection_prompt = (
            f"Character: {char_name}\n"
            f"Question they were carrying: \"{question.question_text}\"\n"
            f"What they knew before: {prior_understanding}\n\n"
            f"Conversation:\n{transcript}\n\n"
            f"In one sentence from {char_name}'s perspective: "
            f"what do they now understand about their question that they didn't before, "
            f"or if nothing changed, say exactly: nothing changed."
        )

        try:
            reflection, in_tok, out_tok = await call_scoring_model(
                system_prompt=reflection_system,
                messages=[{"role": "user", "content": reflection_prompt}],
                max_tokens=80,
            )
            cost_tracker.record("haiku", in_tok, out_tok)
            reflection = reflection.strip().strip('"')
        except Exception:
            reflection = "nothing changed"

        nothing_changed = any(phrase in reflection.lower() for phrase in [
            "nothing changed", "nothing new", "no change", "still don't know",
            "still unclear", "didn't come up", "wasn't addressed",
        ])

        # ── Step 2: Assess the delta using what they knew before ─────────────
        if nothing_changed:
            # Genuinely unchanged — decay if stuck in intermediary loop
            if question.source_type == "intermediary_partial":
                question.intensity = max(0.0, question.intensity - 0.12)
                logger.info(f"  LOOP DECAY: [{char_name}] {question.question_text[:50]}")
            continue

        # Something was learned — update understanding
        question.current_understanding = reflection

        # ── Step 3: Determine if fully resolved ──────────────────────────────
        resolved_phrases = [
            "now understand", "makes sense now", "i understand", "fully explained",
            "answered my question", "i know now", "that explains", "it was",
            "he was", "she was", "they were", "because", "turned out",
        ]
        partial_phrases = [
            "still don't", "still unclear", "but i don't", "more questions",
            "doesn't explain", "need to ask", "want to know", "what about",
            "but why", "but how",
        ]

        reflection_lower = reflection.lower()
        resolved_score = sum(1 for p in resolved_phrases if p in reflection_lower)
        partial_score = sum(1 for p in partial_phrases if p in reflection_lower)

        if resolved_score >= 2 and partial_score == 0:
            question.resolved = True
            question.resolved_day = sim_day
            question.resolution_text = reflection
            question.intensity = 0.0
            logger.info(f"  RESOLVED: [{char_name}] {question.question_text[:60]}")

        elif resolved_score >= 1 or (resolved_score == 0 and partial_score == 0):
            # Partial — but break the loop after first partial
            is_first_partial = (question.intermediary_count or 0) == 0
            if is_first_partial:
                question.intensity = min(1.0, question.intensity + INTENSITY_BOOST_FIRST_PARTIAL)
                question.source_type = "intermediary_partial"
                question.intermediary_count = 1
                logger.info(f"  FIRST PARTIAL (boosted): [{char_name}] {question.question_text[:55]}")
            else:
                # Already had intermediary help — decay to push toward source
                question.intermediary_count = (question.intermediary_count or 0) + 1
                question.intensity = max(0.0, question.intensity - 0.08)
                logger.info(f"  REPEAT PARTIAL (decaying, count={question.intermediary_count}): [{char_name}]")

        else:
            # Mostly unresolved partial
            question.intensity = max(0.0, question.intensity - 0.05)

    db.commit()


def _question_surfaced_in_transcript(question_text: str, transcript: str) -> bool:
    """
    Quick keyword check — did this question's subject appear in the conversation?
    Extracts key nouns from the question and checks for overlap.
    """
    # Extract meaningful words from the question (skip common words)
    SKIP = {
        "why", "what", "how", "did", "does", "is", "are", "was", "were",
        "the", "a", "an", "to", "of", "in", "at", "for", "and", "or",
        "that", "this", "his", "her", "their", "my", "i", "he", "she",
        "they", "it", "so", "but", "when", "if", "do", "not", "with",
        "just", "him", "them", "who", "me", "we", "our", "be", "can",
        "has", "had", "have", "know", "said", "still", "about",
    }
    words = [
        w.strip("?.,!\"'").lower()
        for w in question_text.split()
        if w.strip("?.,!\"'").lower() not in SKIP and len(w) > 3
    ]
    if not words:
        return False

    transcript_lower = transcript.lower()
    # Need at least 2 key words from the question to appear in the transcript
    matches = sum(1 for w in words if w in transcript_lower)
    return matches >= 2


# ── Decay for idle characters ─────────────────────────────────────────────────

def decay_questions_for_idle(character_id: int, sim_day: int, db: Session):
    """Called for characters not in any scene this tick."""
    active = db.query(OpenQuestion).filter(
        OpenQuestion.character_id == character_id,
        OpenQuestion.resolved == False,
        OpenQuestion.dropped == False,
    ).all()
    for q in active:
        days_since = sim_day - (q.last_surfaced_day or q.emerged_day)
        if days_since >= 3:
            q.intensity = max(0.0, q.intensity - INTENSITY_DECAY_UNSURFACED)
        if q.intensity <= 0:
            q.dropped = True
            q.resolved = True
            q.resolved_day = sim_day
            q.resolution_text = "Faded — never revisited"
    db.commit()


# ── Prompt injection ──────────────────────────────────────────────────────────

def get_questions_prompt_block(
    character: Character,
    db: Session,
    sim_day: int = 0,
) -> str:
    """Tiered prompt injection based on intensity and age."""
    active = db.query(OpenQuestion).filter(
        OpenQuestion.character_id == character.id,
        OpenQuestion.resolved == False,
        OpenQuestion.dropped == False,
    ).order_by(OpenQuestion.intensity.desc()).limit(3).all()

    if not active:
        return ""

    driving = []
    working = []
    background = []

    for q in active:
        age = sim_day - q.emerged_day if sim_day and q.emerged_day else 0
        if q.intensity >= 0.65 and age >= 2:
            driving.append(q)
        elif q.intensity >= 0.35:
            working.append(q)
        else:
            background.append(q)

    lines = []

    if driving:
        lines.append(
            "PRESSING ON YOU RIGHT NOW — these questions are actively driving you. "
            "You are watching for openings. You notice details that connect to them. "
            "When the moment is right, you move toward an answer:"
        )
        for q in driving:
            partial_note = " (you have partial information — you need to go deeper)" \
                if q.source_type == "intermediary_partial" else ""
            lines.append(f"- {q.question_text}{partial_note}")
        lines.append(
            "You may not ask directly. But the question is alive in you "
            "and shapes what you pay attention to."
        )
        lines.append("")

    if working:
        lines.append("STILL WORKING ON:")
        for q in working:
            lines.append(f"- {q.question_text}")
        lines.append("")

    if background and not driving and not working:
        lines.append("IN THE BACK OF YOUR MIND:")
        for q in background:
            lines.append(f"- {q.question_text}")
        lines.append("")

    return "\n".join(lines)


# ── Pressure detector ──────────────────────────────────────────────────────────

def get_question_driven_pressure(sim_day: int, db: Session) -> dict | None:
    """
    Find a character with an urgent unresolved question.
    Returns a pressure that routes them toward a relevant partner.
    """
    from database.models import Character as Char

    candidates = db.query(OpenQuestion).filter(
        OpenQuestion.resolved == False,
        OpenQuestion.dropped == False,
        OpenQuestion.intensity >= 0.5,
        OpenQuestion.emerged_day <= sim_day - 1,
    ).order_by(OpenQuestion.intensity.desc()).limit(8).all()

    for q in candidates:
        char = db.query(Char).filter(
            Char.id == q.character_id,
            Char.alive == True,
            Char.is_infant == False,
        ).first()
        if not char:
            continue

        # Find the best partner for THIS question
        partner = get_question_relevant_partner(char, set(), sim_day, db)
        others = [partner] if partner else []

        # Fallback to most familiar person
        if not others:
            rel = db.query(CharacterRelationship).filter(
                CharacterRelationship.from_character_id == char.id,
                CharacterRelationship.familiarity >= 0.1,
            ).order_by(CharacterRelationship.familiarity.desc()).first()
            if rel:
                other = db.query(Char).filter(
                    Char.id == rel.to_character_id, Char.alive == True
                ).first()
                if other:
                    others = [other]

        q_lower = q.question_text.lower()
        if any(w in q_lower for w in ["why did", "what does", "what did"]):
            scene_hint = "argument"
        elif any(w in q_lower for w in ["i saw", "i watched", "what came out", "what was"]):
            scene_hint = "teaching"
        else:
            scene_hint = "gossip"

        # If they have partial info from intermediary, go to source directly
        if q.source_type == "intermediary_partial":
            scene_hint = "argument"

        return {
            "type": "open_question",
            "intensity": q.intensity,
            "characters": [char] + others,
            "subject_id": char.id,
            "description": (
                f"{char.given_name or char.roster_id} needs to understand: "
                f"\"{q.question_text}\""
            ),
            "question_id": q.id,
            "question_text": q.question_text,
            "scene_hint": scene_hint,
            "is_intermediary": q.source_type != "intermediary_partial",
        }

    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_transcript(exchanges: list[dict]) -> str:
    lines = []
    for ex in exchanges:
        name = ex.get("given_name") or ex.get("roster_id", "?")
        text = ex.get("text", "")
        if text and ex.get("roster_id") != "OPERATOR":
            lines.append(f"{name}: {text[:200]}")
    return "\n".join(lines[:20])


def _similar(a: str, b: str) -> bool:
    a_words = a.lower().split()
    b_words = set(b.lower().split())
    for i in range(len(a_words) - 3):
        chunk = a_words[i:i+4]
        if all(w in b_words for w in chunk):
            return True
    return False
