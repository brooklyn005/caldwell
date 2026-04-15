"""
daybook.py — reader-facing daily summary generator.

Every day the reader should be able to see:
1. Daybook: one paragraph labeling the day's character
2. Active threads: what social stories are developing
3. Shifting roles: who is becoming something
4. Consequence panel: what changed today
5. Place memory updates: what locations are becoming
6. Character arc snapshots: per-character brief state

This is what transforms the sim from "a log" into "a living history."
"""
import json
import logging
from sqlalchemy.orm import Session
from database.models import (
    Character, DayComposition, ReaderSummary,
    CivilizationThread, SocialRole, CharacterDisposition,
    ConsequenceRecord, LocationMemory, Location,
    CharacterTransientState,
)
from simulation.consequence_engine import get_recent_consequences_for_reader, get_active_threads
from simulation.social_roles import get_all_roles_for_reader
from simulation.location_memory import get_all_location_memories

logger = logging.getLogger("caldwell.daybook")


def generate_reader_summary(sim_day: int, db: Session) -> ReaderSummary:
    """
    Generates and persists the full reader summary for a given day.
    Called at end of each tick after all scenes have run.
    """
    # Get or create
    existing = db.query(ReaderSummary).filter(
        ReaderSummary.sim_day == sim_day
    ).first()
    if existing:
        return existing

    summary = ReaderSummary(sim_day=sim_day)

    # ── Daybook ────────────────────────────────────────────────────────────
    comp = db.query(DayComposition).filter(
        DayComposition.sim_day == sim_day
    ).first()
    summary.daybook = comp.daybook_text if comp else _fallback_daybook(sim_day, db)

    # ── Active threads ─────────────────────────────────────────────────────
    threads = get_active_threads(db)
    summary.active_threads_json = json.dumps(threads)

    # ── Shifting roles ─────────────────────────────────────────────────────
    roles = get_all_roles_for_reader(db)
    # Only show roles that emerged or strengthened recently
    recent_roles = [r for r in roles if r["confidence"] >= 0.35][:6]
    summary.shifting_roles_json = json.dumps(recent_roles)

    # ── Consequences ───────────────────────────────────────────────────────
    consequences = get_recent_consequences_for_reader(sim_day, db, days=1)
    summary.consequences_json = json.dumps(consequences)

    # ── Place updates ──────────────────────────────────────────────────────
    place_updates = []
    loc_memories = get_all_location_memories(db)
    for lm in loc_memories:
        if lm.get("charge") and lm["charge"] > 0.5:
            place_updates.append({
                "location": lm["location"],
                "update": f"{lm['location']} is charged. {lm.get('last_event', '')}",
            })
        elif lm.get("mood"):
            place_updates.append({
                "location": lm["location"],
                "update": f"{lm['location']} feels {lm['mood']} now.",
            })
    summary.place_updates_json = json.dumps(place_updates[:5])

    # ── Character arc snapshots ────────────────────────────────────────────
    arcs = _build_character_arcs(sim_day, db)
    summary.character_arcs_json = json.dumps(arcs)

    db.add(summary)
    db.commit()

    logger.info(f"  Reader summary generated for day {sim_day}")
    return summary


def _fallback_daybook(sim_day: int, db: Session) -> str:
    return f"Day {sim_day}. The people of Caldwell are living. Things are happening."


def _build_character_arcs(sim_day: int, db: Session) -> list[dict]:
    """Build brief per-character arc snapshots."""
    chars = db.query(Character).filter(
        Character.alive == True, Character.is_infant == False
    ).all()

    arcs = []
    for char in chars:
        # Disposition
        disp = db.query(CharacterDisposition).filter(
            CharacterDisposition.character_id == char.id
        ).first()
        state = disp.state if disp else "neutral"

        # Role
        role_rec = db.query(SocialRole).filter(
            SocialRole.character_id == char.id,
            SocialRole.role_confidence >= 0.35,
        ).first()
        role = role_rec.primary_role if role_rec else None

        # Transient state tags
        trans = db.query(CharacterTransientState).filter(
            CharacterTransientState.character_id == char.id,
            CharacterTransientState.sim_day == sim_day,
        ).first()
        tags = trans.emotional_tags if trans else []

        arc = {
            "name": char.given_name or char.roster_id,
            "age": char.age,
            "disposition": state,
            "role": role,
            "emotional_tags": tags[:2],
        }
        arcs.append(arc)

    return arcs


def format_summary_for_api(summary: ReaderSummary, db: Session) -> dict:
    """
    Returns the reader summary as a clean dict for the API/frontend.
    """
    return {
        "sim_day": summary.sim_day,
        "daybook": summary.daybook or "",
        "active_threads": json.loads(summary.active_threads_json or "[]"),
        "shifting_roles": json.loads(summary.shifting_roles_json or "[]"),
        "consequences": json.loads(summary.consequences_json or "[]"),
        "place_updates": json.loads(summary.place_updates_json or "[]"),
        "character_arcs": json.loads(summary.character_arcs_json or "[]"),
    }
