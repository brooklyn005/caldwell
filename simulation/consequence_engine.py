"""
consequence_engine.py — generates and records persistent consequences from scenes.

Every scene that runs should leave marks on the world. This module:
1. Extracts consequences from scene dialogue (using AI) 
2. Records them in the consequence_records table
3. Updates relationships, location memory, threads, norms
4. Makes those consequences feed back into future scene selection

The goal: things that happen actually matter. The world doesn't reset between scenes.
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, ConsequenceRecord, CivilizationThread,
    CharacterRelationship, NormRecord, Scene,
)
from simulation.location_memory import update_location_memory_after_scene

logger = logging.getLogger("caldwell.consequence")


async def generate_consequences_from_scene(
    scene_type: str,
    exchanges: list[dict],
    participants: list[Character],
    location: Location,
    sim_day: int,
    cost_tracker,
    db: Session,
) -> list[ConsequenceRecord]:
    """
    Main entry point. Called after every scene completes.
    Uses lightweight AI extraction + rule-based inference.
    """
    consequences = []

    if not exchanges or not participants:
        return consequences

    # Rule-based consequences (always run, no API cost)
    rule_consequences = _derive_rule_based_consequences(
        scene_type, exchanges, participants, location, sim_day, db
    )
    for cons in rule_consequences:
        db.add(cons)
        consequences.append(cons)

    # Update location memory
    try:
        participant_roster_ids = [c.roster_id for c in participants]
        significant = scene_type in ("argument", "status_challenge", "quiet_intimacy", "ritual")
        update_location_memory_after_scene(
            scene_type, location, participant_roster_ids,
            sim_day, db, significant=significant,
        )
    except Exception as e:
        logger.debug(f"Location memory update failed: {e}")

    # Update civilization threads
    try:
        _advance_or_create_threads(
            scene_type, exchanges, participants, sim_day, db, consequences
        )
    except Exception as e:
        logger.debug(f"Thread update failed: {e}")

    db.commit()
    logger.info(
        f"  Consequences: {len(consequences)} from {scene_type} "
        f"({'/'.join(c.given_name or c.roster_id for c in participants[:2])})"
    )
    return consequences


def _derive_rule_based_consequences(
    scene_type: str,
    exchanges: list[dict],
    participants: list[Character],
    location: Location,
    sim_day: int,
    db: Session,
) -> list[ConsequenceRecord]:
    """Fast rule-based consequence extraction. No AI calls."""
    consequences = []
    roster_ids = [c.roster_id for c in participants]

    # ── Relationship shift ─────────────────────────────────────────────────
    # Infer from scene type and dialogue length/quality
    rel_type = None
    rel_severity = 0.3

    if scene_type == "quiet_intimacy":
        rel_type = "relationship_shift"
        rel_severity = 0.6
        desc = f"{' and '.join(roster_ids)} shared a private moment. Something between them is different now."
    elif scene_type in ("argument", "correction"):
        # Check for capitulation vs. resistance in dialogue
        last_turns = exchanges[-3:] if len(exchanges) >= 3 else exchanges
        texts = " ".join(t.get("text", "") for t in last_turns).lower()
        if any(w in texts for w in ["fine", "you're right", "i understand", "i was wrong"]):
            rel_type = "relationship_shift"
            desc = f"A dispute between {' and '.join(roster_ids)} found some resolution."
            rel_severity = 0.4
        else:
            rel_type = "rivalry_deepened"
            desc = f"The tension between {' and '.join(roster_ids)} went unresolved. It will sit between them."
            rel_severity = 0.5

    elif scene_type == "teaching":
        rel_type = "relationship_shift"
        rel_severity = 0.3
        desc = f"Knowledge passed between {' and '.join(roster_ids)}. That creates a kind of bond."

    elif scene_type == "status_challenge":
        # Did someone back down?
        last_exchange = exchanges[-1].get("text", "").lower() if exchanges else ""
        if any(w in last_exchange for w in ["fine", "agree", "yes", "understood"]):
            rel_type = "status_shift"
            desc = f"A status challenge between {' and '.join(roster_ids)} produced a result. Standing shifted."
        else:
            rel_type = "status_shift"
            desc = f"A status challenge between {' and '.join(roster_ids)} was left unresolved. Both still standing."
        rel_severity = 0.6

    elif scene_type == "gossip":
        rel_type = "rumor_created"
        rel_severity = 0.4
        # Find if a third party was mentioned
        non_participants = [t.get("given_name", "") for t in exchanges
                          if t.get("given_name") and
                          t.get("given_name") not in [c.given_name for c in participants]]
        if non_participants:
            target = non_participants[0]
            desc = f"A conversation about {target} will shape how others see them."
        else:
            desc = f"Conversation between {' and '.join(roster_ids)} created social knowledge that will spread."

    elif scene_type == "ritual":
        rel_type = "norm_reinforced"
        rel_severity = 0.5
        desc = f"A repeated behavior at {location.name} hardened slightly toward custom."

    if rel_type:
        consequences.append(ConsequenceRecord(
            sim_day=sim_day,
            source_type="scene",
            consequence_type=rel_type,
            affected_ids_json=json.dumps(roster_ids),
            location_id=location.id,
            description=desc,
            severity=rel_severity,
            persistence=7,
            reader_visible=True,
        ))

    # ── First-Person Rule: Transformative first experiences ────────────────
    # A character's first quiet_intimacy scene is coded as Transformative.
    # This significantly shifts guardedness (down) and marks the experience.
    if scene_type == "quiet_intimacy":
        for char in participants:
            prior_count = _count_prior_intimate_scenes(char, sim_day, db)
            if prior_count == 0:
                char_name = char.given_name or char.roster_id
                consequences.append(ConsequenceRecord(
                    sim_day=sim_day,
                    source_type="scene",
                    consequence_type="transformative_first_experience",
                    affected_ids_json=json.dumps([char.roster_id]),
                    location_id=location.id,
                    description=(
                        f"This was {char_name}'s first time in this kind of closeness. "
                        f"Something shifted — a before and after has been created."
                    ),
                    severity=0.85,
                    persistence=30,
                    reader_visible=True,
                ))
                # Lower guardedness in transient state
                try:
                    from database.models import CharacterTransientState
                    transient = db.query(CharacterTransientState).filter(
                        CharacterTransientState.character_id == char.id,
                        CharacterTransientState.sim_day == sim_day,
                    ).first()
                    if transient:
                        transient.guardedness = max(
                            0.0, (transient.guardedness or 0.3) - 0.2
                        )
                except Exception:
                    pass
                logger.info(
                    f"  TRANSFORMATIVE: {char_name}'s first quiet_intimacy scene — marked"
                )

    # ── Emotional residue ──────────────────────────────────────────────────
    # Long confrontational scenes leave emotional weight
    if scene_type in ("argument", "resentment", "correction") and len(exchanges) >= 10:
        consequences.append(ConsequenceRecord(
            sim_day=sim_day,
            source_type="scene",
            consequence_type="emotional_residue",
            affected_ids_json=json.dumps(roster_ids),
            location_id=location.id,
            description=f"What was said between {' and '.join(roster_ids)} will sit with them. It won't just pass.",
            severity=0.5,
            persistence=5,
            reader_visible=True,
        ))

    # ── Knowledge gained ──────────────────────────────────────────────────
    if scene_type == "teaching" and len(exchanges) >= 6:
        learner = participants[-1]  # Last participant usually the learner
        consequences.append(ConsequenceRecord(
            sim_day=sim_day,
            source_type="scene",
            consequence_type="knowledge_gained",
            affected_ids_json=json.dumps([learner.roster_id]),
            location_id=location.id,
            description=f"{learner.given_name or learner.roster_id} knows something now they didn't know before.",
            severity=0.3,
            persistence=30,
            reader_visible=False,
        ))

    # ── Public exposure ────────────────────────────────────────────────────
    # Status challenge scenes leave public marks
    if scene_type == "status_challenge":
        consequences.append(ConsequenceRecord(
            sim_day=sim_day,
            source_type="scene",
            consequence_type="public_exposure",
            affected_ids_json=json.dumps(roster_ids),
            location_id=location.id,
            description=f"What happened between {' and '.join(roster_ids)} was visible. Others will hear about it.",
            severity=0.6,
            persistence=10,
            reader_visible=True,
        ))

    return consequences


def _count_prior_intimate_scenes(char, sim_day: int, db: Session) -> int:
    """Count how many quiet_intimacy scenes this character participated in before today."""
    try:
        from database.models import Scene as SceneModel
        import json as _json
        scenes = db.query(SceneModel).filter(
            SceneModel.scene_type == "quiet_intimacy",
            SceneModel.sim_day < sim_day,
        ).all()
        return sum(
            1 for s in scenes
            if char.roster_id in _json.loads(s.participant_ids_json or "[]")
        )
    except Exception:
        return 0


def _advance_or_create_threads(
    scene_type: str,
    exchanges: list[dict],
    participants: list[Character],
    sim_day: int,
    db: Session,
    existing_consequences: list,
) -> None:
    """Updates or creates civilization threads based on scene outcomes."""
    roster_ids = [c.roster_id for c in participants]

    # Check if an existing thread involves these participants
    threads = db.query(CivilizationThread).filter(
        CivilizationThread.status.in_(["active", "intensifying"]),
    ).all()

    matched_thread = None
    for thread in threads:
        thread_participants = thread.participant_ids
        overlap = sum(1 for rid in roster_ids if rid in thread_participants)
        if overlap >= 1:
            matched_thread = thread
            break

    if matched_thread:
        # Advance existing thread
        matched_thread.advance_count += 1
        matched_thread.last_advanced_day = sim_day

        # Update heat based on scene type
        if scene_type in ("argument", "status_challenge", "correction"):
            matched_thread.heat = min((matched_thread.heat or 0.5) + 0.1, 1.0)
            matched_thread.status = "intensifying" if matched_thread.heat > 0.7 else "active"
        elif scene_type == "quiet_intimacy":
            matched_thread.heat = min((matched_thread.heat or 0.5) + 0.15, 1.0)
        elif scene_type in ("ritual", "teaching"):
            matched_thread.heat = max((matched_thread.heat or 0.5) - 0.05, 0.1)

        # Add participants not already in thread
        existing_p = matched_thread.participant_ids
        for rid in roster_ids:
            if rid not in existing_p:
                existing_p.append(rid)
        matched_thread.participant_ids_json = json.dumps(existing_p)

    else:
        # Maybe create a new thread
        thread_type = _classify_thread_type(scene_type, exchanges)
        if thread_type and _should_create_thread(scene_type, exchanges, db):
            title = _generate_thread_title(thread_type, participants, sim_day)
            description = _generate_thread_description(thread_type, participants, scene_type)

            new_thread = CivilizationThread(
                thread_type=thread_type,
                title=title,
                description=description,
                participant_ids_json=json.dumps(roster_ids),
                heat=0.5,
                status="active",
                origin_day=sim_day,
                last_advanced_day=sim_day,
                advance_count=1,
            )
            db.add(new_thread)
            logger.info(f"  NEW THREAD: {title}")

    # Mark resolved threads
    for thread in threads:
        if thread.last_advanced_day and sim_day - thread.last_advanced_day > 15:
            thread.status = "dormant"
        if thread.heat is not None and thread.heat < 0.1:
            thread.status = "resolved"
            thread.resolved_day = sim_day


def _classify_thread_type(scene_type: str, exchanges: list[dict]) -> str | None:
    if scene_type == "quiet_intimacy":
        return "romance"
    if scene_type in ("argument", "status_challenge") and len(exchanges) >= 8:
        return "rivalry"
    if scene_type == "teaching":
        return "role_emergence"
    if scene_type == "ritual":
        return "ritual_formation"
    if scene_type == "gossip":
        # Check if the gossip is about authority/leadership
        all_text = " ".join(e.get("text", "") for e in exchanges).lower()
        if any(w in all_text for w in ["who decides", "in charge", "authority", "listen to"]):
            return "authority_shift"
        return None
    return None


def _should_create_thread(scene_type: str, exchanges: list[dict], db: Session) -> bool:
    """Only create threads for sufficiently notable scenes."""
    # Don't create too many threads
    active_count = db.query(CivilizationThread).filter(
        CivilizationThread.status.in_(["active", "intensifying"])
    ).count()
    if active_count >= 8:
        return False

    # Scene-specific thresholds
    if scene_type == "quiet_intimacy":
        return len(exchanges) >= 8
    if scene_type in ("argument", "status_challenge"):
        return len(exchanges) >= 10
    if scene_type == "ritual":
        return True

    return False


def _generate_thread_title(thread_type: str, participants: list[Character], sim_day: int) -> str:
    names = [c.given_name or c.roster_id for c in participants[:2]]
    templates = {
        "romance": f"{names[0]} and {names[1] if len(names) > 1 else '?'} drawing closer",
        "rivalry": f"Something building between {names[0]} and {names[1] if len(names) > 1 else '?'}",
        "role_emergence": f"{names[0]} becoming something",
        "ritual_formation": "A practice hardening into custom",
        "authority_shift": f"Who {names[0]} answers to — and why",
        "mystery": f"What {names[0]} doesn't know yet",
    }
    return templates.get(thread_type, f"Thread from day {sim_day}")


def _generate_thread_description(
    thread_type: str, participants: list[Character], scene_type: str
) -> str:
    names = [c.given_name or c.roster_id for c in participants[:2]]
    templates = {
        "romance": f"{names[0]} and {names[1] if len(names) > 1 else 'another'} keep finding each other.",
        "rivalry": f"There's something unresolved between {names[0]} and {names[1] if len(names) > 1 else 'another'}. It keeps surfacing.",
        "role_emergence": f"{names[0]} is becoming the person others go to for something. A role is forming.",
        "ritual_formation": "A repeated behavior is acquiring weight. It's becoming how things are done.",
        "authority_shift": "Questions of authority are alive. Who decides things here — and how — is in motion.",
        "mystery": f"Something {names[0]} witnessed or heard is sitting unresolved. It will surface again.",
    }
    return templates.get(thread_type, "A thread is developing.")


def get_recent_consequences_for_reader(sim_day: int, db: Session, days: int = 3) -> list[dict]:
    """Returns recent visible consequences for reader panel."""
    cons = db.query(ConsequenceRecord).filter(
        ConsequenceRecord.sim_day >= sim_day - days,
        ConsequenceRecord.reader_visible == True,
    ).order_by(ConsequenceRecord.sim_day.desc()).limit(10).all()

    return [
        {
            "day": c.sim_day,
            "type": c.consequence_type,
            "description": c.description,
            "severity": c.severity,
        }
        for c in cons
    ]


def get_active_threads(db: Session) -> list[dict]:
    """Returns active civilization threads for reader panel."""
    threads = db.query(CivilizationThread).filter(
        CivilizationThread.status.in_(["active", "intensifying"]),
    ).order_by(CivilizationThread.heat.desc()).limit(6).all()

    return [
        {
            "title": t.title,
            "description": t.description,
            "heat": t.heat,
            "type": t.thread_type,
            "days_active": None,  # computed elsewhere
        }
        for t in threads
    ]
