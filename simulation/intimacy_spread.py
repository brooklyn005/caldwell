"""
intimacy_spread.py — belief transmission when secrets are shared.

When a character "shares a secret" in a scene, they transmit their belief
(including its errors) to the listener. If two characters hold contradictory
beliefs about the same subject, the tension is logged in CivilizationThread.
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import Character, CharacterBelief, CivilizationThread
from simulation.epistemology import record_private_signal, detect_subject_from_text

logger = logging.getLogger("caldwell.intimacy_spread")

# Phrases that indicate a character is sharing a secret or private knowledge
_SECRET_SHARING_PHRASES = [
    "i shouldn't tell you",
    "don't tell anyone",
    "between us",
    "you can't say anything",
    "what i saw",
    "i saw them",
    "i heard something",
    "what i heard",
    "nobody knows",
    "i've been keeping",
    "i haven't told",
    "promise not to",
    "only told you",
    "in secret",
    "they were alone",
    "i saw what",
    "what was happening",
    "what i noticed",
    "what goes on",
]


def spread_beliefs_after_scene(
    char_a: Character,
    char_b: Character,
    scene_type: str,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
) -> None:
    """
    Called after every scene. Detects secret-sharing language and propagates
    beliefs between participants. Checks for contradictions.
    """
    try:
        sharing_events = _detect_secret_sharing(exchanges)
        if not sharing_events:
            return

        for event in sharing_events:
            speaker_rid = event["speaker"]
            subject_hint = event["subject_hint"]

            # Identify speaker and listener
            if speaker_rid == char_a.roster_id:
                speaker, listener = char_a, char_b
            elif speaker_rid == char_b.roster_id:
                speaker, listener = char_b, char_a
            else:
                continue

            # Find what the speaker believes about the subject
            subject = _resolve_subject(subject_hint, speaker, db)
            if not subject:
                continue

            speaker_belief = db.query(CharacterBelief).filter(
                CharacterBelief.character_id == speaker.id,
                CharacterBelief.subject == subject,
            ).first()

            if not speaker_belief or speaker_belief.belief_state == "confusion":
                # Speaker doesn't know enough to transmit usefully
                continue

            # Construct a degraded signal to transmit to listener
            # (the listener receives the speaker's belief, imperfectly)
            transmitted_signal = _degrade_belief(speaker_belief)

            record_private_signal(
                listener, transmitted_signal, subject,
                "told_by_other", sim_day, db
            )

            # Check for belief contradiction
            _check_belief_contradiction(speaker, listener, subject, sim_day, db)

    except Exception as e:
        logger.debug(f"Belief spread failed: {e}")


def _detect_secret_sharing(exchanges: list[dict]) -> list[dict]:
    """
    Scan exchanges for secret-sharing language.
    Returns list of {speaker, text_snippet, subject_hint} dicts.
    """
    events = []
    for ex in exchanges:
        text = ex.get("text", "").lower()
        roster_id = ex.get("roster_id", "")
        if not text or roster_id in ("narrator", "OPERATOR"):
            continue
        for phrase in _SECRET_SHARING_PHRASES:
            if phrase in text:
                subject_hint = detect_subject_from_text(text)
                events.append({
                    "speaker": roster_id,
                    "text_snippet": text[:100],
                    "subject_hint": subject_hint or "concealed_activity",
                })
                break  # one event per exchange
    return events


def _resolve_subject(subject_hint: str, speaker: Character, db: Session) -> str | None:
    """
    Given a subject hint, find the speaker's most developed belief on that topic.
    Falls back to any non-trivial belief if no direct match.
    """
    # Direct match
    direct = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == speaker.id,
        CharacterBelief.subject == subject_hint,
        CharacterBelief.belief_state != "confusion",
    ).first()
    if direct:
        return direct.subject

    # Any non-trivial belief
    fallback = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == speaker.id,
        CharacterBelief.belief_state.in_(["tentative", "labeled", "social_concept"]),
    ).order_by(CharacterBelief.signal_count.desc()).first()
    if fallback:
        return fallback.subject

    return None


def _degrade_belief(belief: CharacterBelief) -> str:
    """
    Construct a degraded, secondhand version of a belief to transmit.
    The listener gets less coherent information than the speaker has.
    """
    signals = json.loads(belief.signals_json or "[]")
    if signals:
        # Pick a random signal to transmit (imperfect relay)
        raw = random.choice(signals).get("text", "")
        if raw:
            return f"heard from someone: {raw}"

    # Fallback based on subject
    degraded_map = {
        "shared_touch": "something was happening between two people — the person wasn't clear",
        "private_sounds": "sounds coming from behind a door — the person who told me seemed unsure",
        "concealed_activity": "they were doing something they didn't want seen — that's all I know",
        "body_change_female": "something changing in a woman's body — the person couldn't explain it well",
        "body_change_male": "one of the younger men is different — the person noticed but couldn't name it",
        "pair_bonding": "two people drawn to each other — the person who told me seemed certain",
        "unknown_distress": "someone in pain in a way that has no name — the person seemed shaken",
    }
    return degraded_map.get(belief.subject, "something private — the details got lost in the telling")


def _check_belief_contradiction(
    char_a: Character,
    char_b: Character,
    subject: str,
    sim_day: int,
    db: Session,
) -> None:
    """
    If the two characters hold contradictory beliefs about the same subject,
    log a CivilizationThread for the contradiction.
    """
    belief_a = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == char_a.id,
        CharacterBelief.subject == subject,
    ).first()
    belief_b = db.query(CharacterBelief).filter(
        CharacterBelief.character_id == char_b.id,
        CharacterBelief.subject == subject,
    ).first()

    if not belief_a or not belief_b:
        return

    # Contradiction: they have labeled beliefs that diverge (different vocabulary tags)
    if (
        belief_a.belief_state in ("labeled", "social_concept")
        and belief_b.belief_state in ("labeled", "social_concept")
        and belief_a.vocabulary_tag
        and belief_b.vocabulary_tag
        and belief_a.vocabulary_tag.lower() != belief_b.vocabulary_tag.lower()
    ):
        a_name = char_a.given_name or char_a.roster_id
        b_name = char_b.given_name or char_b.roster_id
        subject_label = subject.replace("_", " ")

        # Check if a thread about this already exists
        existing = db.query(CivilizationThread).filter(
            CivilizationThread.status.in_(["active", "intensifying"]),
            CivilizationThread.thread_type == "contested_knowledge",
        ).first()

        if not existing:
            thread = CivilizationThread(
                thread_type="contested_knowledge",
                title=f"Different words for the same thing",
                description=(
                    f"{a_name} calls it \"{belief_a.vocabulary_tag}\". "
                    f"{b_name} calls it \"{belief_b.vocabulary_tag}\". "
                    f"They are both talking about {subject_label}. "
                    f"The disagreement is not yet visible — but it will surface."
                ),
                participant_ids_json=json.dumps([
                    char_a.roster_id, char_b.roster_id
                ]),
                heat=0.4,
                status="active",
                origin_day=sim_day,
                last_advanced_day=sim_day,
                advance_count=1,
            )
            db.add(thread)
            logger.info(
                f"  CONTRADICTION: {a_name} vs {b_name} on '{subject_label}' — thread created"
            )
