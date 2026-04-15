"""
event_detector.py — automatically detects and logs significant moments
in Caldwell's social history.

Runs after each tick. Uses rule-based detection (no API calls) for speed.
Detected events appear in the Event Journal on the dashboard.
"""
import json
import logging
import re
from sqlalchemy.orm import Session
from database.models import (
    Character, CharacterRelationship, Dialogue,
    SignificantEvent, Memory, ActionEvent
)

logger = logging.getLogger("caldwell.events")

# Keywords that suggest the food/water mystery is being discussed
MYSTERY_KEYWORDS = [
    "where does", "where do", "who left", "who put", "why is there",
    "where did", "appears", "where comes from", "who provides",
    "how does", "who makes", "who brings", "where from",
    "no one planted", "no one built", "cannot explain",
    "strange", "impossible", "shouldn't be",
]

# Keywords suggesting governance or rule-making
GOVERNANCE_KEYWORDS = [
    "we should all", "everyone must", "we agreed", "decided together",
    "rule", "law", "agree", "vote", "gather", "meeting", "decide for all",
    "speak for", "leader", "council", "together we",
]

# Conflict indicators
CONFLICT_KEYWORDS = [
    "won't listen", "not fair", "mine", "you can't", "stay away",
    "don't trust", "lied", "wrong", "angry", "refuse", "never again",
    "get away", "leave me", "stop telling",
]

# Alliance / bonding indicators  
BONDING_KEYWORDS = [
    "trust you", "glad you're here", "together", "help each other",
    "always", "count on", "care about", "feel safe", "friend",
    "with you", "protect", "won't leave",
]


def _already_logged(db, event_type: str, char_ids: list) -> bool:
    """Check if this event type has already been logged for this pair."""
    import json
    pair = set(char_ids)
    # Only scan recent events for performance — strong_bond etc are permanent once logged
    existing = db.query(SignificantEvent).filter(
        SignificantEvent.event_type == event_type
    ).all()
    for ev in existing:
        existing_ids = set(json.loads(ev.character_ids_json or "[]"))
        if existing_ids == pair:
            return True
    return False


def _already_logged_fast(db, event_type: str, char_id: int) -> bool:
    """Fast single-character check — no JSON parsing needed."""
    return db.query(SignificantEvent).filter(
        SignificantEvent.event_type == event_type,
        SignificantEvent.character_ids_json.contains(str(char_id)),
    ).first() is not None


def _log_event(
    db: Session,
    sim_day: int,
    event_type: str,
    description: str,
    character_ids: list,
    location: str = None,
    emotional_weight: float = 0.5,
):
    # Avoid duplicate events on the same day of the same type for the same chars
    ids_json = json.dumps(sorted(character_ids))
    existing = (
        db.query(SignificantEvent)
        .filter(
            SignificantEvent.sim_day == sim_day,
            SignificantEvent.event_type == event_type,
            SignificantEvent.character_ids_json == ids_json,
        )
        .first()
    )
    if existing:
        return
    ev = SignificantEvent(
        sim_day=sim_day,
        event_type=event_type,
        description=description,
        character_ids_json=ids_json,
        location=location,
        emotional_weight=emotional_weight,
    )
    db.add(ev)
    logger.info(f"  EVENT [{event_type}] Day {sim_day}: {description[:80]}")


def scan_dialogues(sim_day: int, db: Session):
    """
    Scan all dialogues from today for significant events.
    Call this at the end of each tick.
    """
    dialogues = (
        db.query(Dialogue)
        .filter(Dialogue.sim_day == sim_day)
        .all()
    )

    for dialogue in dialogues:
        exchanges = dialogue.dialogue
        participant_ids = dialogue.participants
        chars = [
            db.query(Character).filter(Character.id == cid).first()
            for cid in participant_ids
        ]
        chars = [c for c in chars if c]
        if not chars:
            continue

        # Build full text of the conversation
        full_text = " ".join(ex.get("text", "") for ex in exchanges).lower()

        # ── Name giving ───────────────────────────────────────────────────
        name_patterns = [
            r"call me ([a-z][a-z]{1,12})",
            r"my name is ([a-z][a-z]{1,12})",
            r"i am called ([a-z][a-z]{1,12})",
            r"you can call me ([a-z][a-z]{1,12})",
        ]
        for pat in name_patterns:
            matches = re.findall(pat, full_text)
            for match in matches:
                name = match.strip().capitalize()
                if name.lower() not in {"the", "a", "an", "me", "you", "here", "there"}:
                    _log_event(
                        db, sim_day, "first_name",
                        f"A name emerges: '{name}' — a word that will outlast whoever coined it.",
                        [c.id for c in chars],
                        location=dialogue.topic,
                        emotional_weight=0.9,
                    )

        # ── Mystery question ──────────────────────────────────────────────
        mystery_hits = sum(1 for kw in MYSTERY_KEYWORDS if kw in full_text)
        if mystery_hits >= 2:
            speaker_names = [c.given_name or c.roster_id for c in chars]
            _log_event(
                db, sim_day, "mystery_question",
                f"{' and '.join(speaker_names)} question the nature of the provision — "
                f"where does the food come from?",
                [c.id for c in chars],
                location=dialogue.topic,
                emotional_weight=0.85,
            )

        # ── Governance emergence ──────────────────────────────────────────
        gov_hits = sum(1 for kw in GOVERNANCE_KEYWORDS if kw in full_text)
        if gov_hits >= 2:
            speaker_names = [c.given_name or c.roster_id for c in chars]
            _log_event(
                db, sim_day, "governance",
                f"{' and '.join(speaker_names)} begin to negotiate collective rules.",
                [c.id for c in chars],
                location=dialogue.topic,
                emotional_weight=0.8,
            )

        # ── Conflict ─────────────────────────────────────────────────────
        conflict_hits = sum(1 for kw in CONFLICT_KEYWORDS if kw in full_text)
        if conflict_hits >= 2:
            speaker_names = [c.given_name or c.roster_id for c in chars]
            _log_event(
                db, sim_day, "conflict",
                f"Tension surfaces between {' and '.join(speaker_names)}.",
                [c.id for c in chars],
                location=dialogue.topic,
                emotional_weight=0.75,
            )

        # ── Bonding ───────────────────────────────────────────────────────
        bonding_hits = sum(1 for kw in BONDING_KEYWORDS if kw in full_text)
        if bonding_hits >= 2:
            speaker_names = [c.given_name or c.roster_id for c in chars]
            _log_event(
                db, sim_day, "alliance",
                f"{' and '.join(speaker_names)} express genuine trust in each other.",
                [c.id for c in chars],
                location=dialogue.topic,
                emotional_weight=0.7,
            )

    # ── Relationship milestones ───────────────────────────────────────────────
    rels = db.query(CharacterRelationship).all()
    for rel in rels:
        char_a = db.query(Character).filter(Character.id == rel.from_character_id).first()
        char_b = db.query(Character).filter(Character.id == rel.to_character_id).first()
        if not char_a or not char_b:
            continue

        name_a = char_a.given_name or char_a.roster_id
        name_b = char_b.given_name or char_b.roster_id

        # Strong bond milestone
        if rel.familiarity >= 0.82 and not _already_logged(db, "strong_bond", [char_a.id, char_b.id]):
            _log_event(
                db, sim_day, "strong_bond",
                f"{name_a} and {name_b} have become deeply familiar — "
                f"one of Caldwell's closest relationships.",
                [char_a.id, char_b.id],
                emotional_weight=0.85,
            )

        # Trust milestone — someone is trusted highly
        if rel.trust_level >= 0.7 and not _already_logged(db, "trust_milestone", [char_a.id, char_b.id]):
            _log_event(
                db, sim_day, "alliance",
                f"{name_a} deeply trusts {name_b} — "
                f"a bond that may shape who leads.",
                [char_a.id, char_b.id],
                emotional_weight=0.8,
            )

        # First meeting milestone — only once ever per pair, only early days
        if rel.interaction_count == 1 and sim_day <= 5:
            already = db.query(SignificantEvent).filter(
                SignificantEvent.event_type == "first_meeting",
                SignificantEvent.character_ids_json == json.dumps(
                    sorted([char_a.id, char_b.id])
                ),
            ).first()
            if not already:
                _log_event(
                    db, sim_day, "first_meeting",
                    f"{name_a} and {name_b} meet for the first time.",
                    [char_a.id, char_b.id],
                    emotional_weight=0.4,
                )

    # ── Inception effects — fire when thought surfaces in actual dialogue ────
    inception_memories = (
        db.query(Memory)
        .filter(
            Memory.is_inception == True,
        )
        .all()
    )
    for mem in inception_memories:
        char = db.query(Character).filter(Character.id == mem.character_id).first()
        if not char:
            continue

        # Already logged this inception effect?
        if _already_logged(db, "inception_effect", [char.id]):
            continue

        # Check if any dialogue from today contains words from the inception
        # — evidence the thought actually surfaced in conversation
        key_words = [
            w.lower() for w in mem.content.split()
            if len(w) > 5 and w.isalpha()
        ][:6]

        recent_dialogues = (
            db.query(Dialogue)
            .filter(Dialogue.sim_day >= mem.sim_day)
            .filter(Dialogue.participant_ids_json.contains(str(char.id)))
            .all()
        ) if key_words else []

        surfaced = False
        for d in recent_dialogues:
            for exchange in d.dialogue:
                if exchange.get("roster_id") == char.roster_id:
                    text = exchange.get("text", "").lower()
                    if sum(1 for kw in key_words if kw in text) >= 2:
                        surfaced = True
                        break
            if surfaced:
                break

        if surfaced:
            name = char.given_name or char.roster_id
            _log_event(
                db, sim_day, "inception_effect",
                f"A planted thought surfaces in {name}\'s words: \"{mem.content[:80]}\"",
                [char.id],
                emotional_weight=0.9,
            )

    # ── Operator action events ────────────────────────────────────────────────
    action_events = (
        db.query(ActionEvent)
        .filter(
            ActionEvent.processed_day == sim_day,
            ActionEvent.processed == True,
        )
        .all()
    )
    for ev in action_events:
        all_ids = ev.participant_ids + ev.witness_ids
        chars = [db.query(Character).filter(Character.roster_id == rid).first()
                 for rid in all_ids]
        chars = [c for c in chars if c]
        char_ids_json = json.dumps(sorted([c.id for c in chars]))
        existing = db.query(SignificantEvent).filter(
            SignificantEvent.event_type == "action_inject",
            SignificantEvent.character_ids_json == char_ids_json,
            SignificantEvent.sim_day == sim_day,
        ).first()
        if not existing:
            names = [c.given_name or c.roster_id for c in chars]
            _log_event(
                db, sim_day, "action_inject",
                f"[Operator scene] {ev.scene_description[:400]}",
                [c.id for c in chars],
                emotional_weight=0.95,
            )

    db.commit()


def detect_population_milestones(sim_day: int, db: Session):
    """Check for population-level milestones."""
    alive = db.query(Character).filter(Character.alive == True).count()
    total_events = db.query(SignificantEvent).count()

    if sim_day == 7:
        _log_event(db, sim_day, "milestone",
                   f"One week in Caldwell. {alive} souls remain.", [],
                   emotional_weight=0.6)
    elif sim_day == 30:
        _log_event(db, sim_day, "milestone",
                   f"One month. The society of Caldwell is {total_events} events old.", [],
                   emotional_weight=0.7)
    elif sim_day == 60:
        _log_event(db, sim_day, "milestone",
                   f"Two months. What has Caldwell become?", [],
                   emotional_weight=0.8)

    db.commit()
