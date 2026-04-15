import json
import logging
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import (
    Character, Location, TickLog, InceptionEvent,
    WorldEvent, Memory, CharacterRelationship
)
from simulation.engine import SimulationEngine
from simulation.clock import SimulationClock
from simulation.cost_tracker import CostTracker
from api.websocket_manager import manager

router = APIRouter()
logger = logging.getLogger("caldwell.api")


# ── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive pings
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ── Simulation control ───────────────────────────────────────────────────────

@router.get("/api/status")
def get_status(db: Session = Depends(get_db)):
    engine = SimulationEngine(db)
    return engine.status()


@router.post("/api/sim/start")
def sim_start(db: Session = Depends(get_db)):
    clock = SimulationClock(db)
    clock.start()
    return {"ok": True, "running": True}


@router.post("/api/sim/stop")
def sim_stop(db: Session = Depends(get_db)):
    clock = SimulationClock(db)
    clock.stop()
    return {"ok": True, "running": False}


@router.post("/api/sim/tick")
async def manual_tick(db: Session = Depends(get_db)):
    """Run a single tick manually (useful for testing)."""
    engine = SimulationEngine(db)

    async def broadcast(payload):
        await manager.broadcast(payload)

    result = await engine.run_tick(broadcast_fn=broadcast)
    return result


# ── Characters ───────────────────────────────────────────────────────────────

@router.get("/api/characters")
def list_characters(db: Session = Depends(get_db)):
    chars = (
        db.query(Character)
        .order_by(Character.roster_id)
        .all()
    )
    return [_char_summary(c, db) for c in chars]


@router.get("/api/characters/{roster_id}")
def get_character(roster_id: str, db: Session = Depends(get_db)):
    c = db.query(Character).filter(Character.roster_id == roster_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Character not found")
    return _char_detail(c, db)


def _char_summary(c: Character, db: Session) -> dict:
    from database.models import SocialRole, CharacterTransientState
    loc = db.query(Location).filter(Location.id == c.current_location_id).first()

    role = db.query(SocialRole).filter(SocialRole.character_id == c.id).first()
    transient = (
        db.query(CharacterTransientState)
        .filter(CharacterTransientState.character_id == c.id)
        .order_by(CharacterTransientState.sim_day.desc())
        .first()
    )

    return {
        "roster_id": c.roster_id,
        "gender": c.gender,
        "age": c.age,
        "is_minor": c.is_minor,
        "ai_model": c.ai_model,
        "core_drive": c.core_drive,
        "alive": c.alive,
        "given_name": c.given_name,
        "display_name": c.display_name(),
        "physical_description": c.physical_description,
        "natural_tendency": c.natural_tendency,
        "current_location": loc.name if loc else None,
        "social_role": {
            "primary_role": role.primary_role,
            "secondary_role": role.secondary_role,
            "role_confidence": round(role.role_confidence, 2),
            "public_visibility": round(role.public_visibility, 2),
            "public_reputation": role.public_reputation,
            "emerged_day": role.emerged_day,
        } if role else None,
        "transient_state": {
            "sim_day": transient.sim_day,
            "emotional_tags": transient.emotional_tags,
            "hunger_level": round(transient.hunger_level, 1),
            "fatigue_level": round(transient.fatigue_level, 1),
            "shame_active": transient.shame_active,
            "hope_active": transient.hope_active,
            "obsession_text": transient.obsession_text,
            "guardedness": round(transient.guardedness, 2),
            "loneliness": round(transient.loneliness, 2),
        } if transient else None,
    }


def _char_detail(c: Character, db: Session) -> dict:
    base = _char_summary(c, db)
    memories = (
        db.query(Memory)
        .filter(Memory.character_id == c.id)
        .order_by(Memory.sim_day.desc())
        .limit(20)
        .all()
    )
    relationships = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.from_character_id == c.id)
        .all()
    )
    base.update({
        "personality_traits": c.personality_traits,
        "private_belief": c.private_belief,
        "fear": c.fear,
        "memories": [
            {
                "day": m.sim_day,
                "type": m.memory_type,
                "content": m.content,
                "is_inception": m.is_inception,
            }
            for m in memories
        ],
        "relationships": [
            {
                "with": r.to_character.roster_id,
                "with_name": r.to_character.display_name(),
                "trust": round(r.trust_level, 2),
                "familiarity": round(r.familiarity, 2),
                "bond_type": r.bond_type,
                "interactions": r.interaction_count,
            }
            for r in relationships
        ],
    })
    return base


# ── Locations ────────────────────────────────────────────────────────────────

@router.get("/api/locations")
def list_locations(db: Session = Depends(get_db)):
    from database.models import LocationMemory
    locs = db.query(Location).all()
    result = []
    for loc in locs:
        occupants = (
            db.query(Character)
            .filter(
                Character.current_location_id == loc.id,
                Character.alive == True,
            )
            .all()
        )
        mem = db.query(LocationMemory).filter(LocationMemory.location_id == loc.id).first()
        result.append({
            "id": loc.id,
            "name": loc.name,
            "description": loc.description,
            "location_type": loc.location_type,
            "capacity": loc.capacity,
            "has_desirable_units": loc.has_desirable_units,
            "desirable_unit_count": loc.desirable_unit_count,
            "occupants": [
                {"roster_id": c.roster_id, "given_name": c.given_name}
                for c in occupants
            ],
            "location_memory": {
                "dominant_mood": mem.dominant_mood,
                "privacy_score": round(mem.privacy_score, 2) if mem.privacy_score is not None else None,
                "charge_level": round(mem.charge_level, 2) if mem.charge_level is not None else None,
                "identity_tags": json.loads(mem.identity_tags_json or "[]"),
                "scene_counts": json.loads(mem.scene_counts_json or "{}"),
                "last_notable_event": mem.last_notable_event,
                "last_notable_day": mem.last_notable_day,
                "who_controls": mem.who_controls,
                "who_avoids": json.loads(mem.who_avoids or "[]"),
                "first_recorded_day": mem.first_recorded_day,
            } if mem else None,
        })
    return result


# ── Log ──────────────────────────────────────────────────────────────────────

@router.get("/api/log")
def get_log(limit: int = 50, db: Session = Depends(get_db)):
    logs = (
        db.query(TickLog)
        .order_by(TickLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "tick": t.tick_number,
            "sim_day": t.sim_day,
            "summary": t.summary,
            "events": json.loads(t.events_json or "[]"),
            "cost": t.cost_this_tick,
            "at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in logs
    ]


# ── Inception ────────────────────────────────────────────────────────────────

class InceptionPayload(BaseModel):
    roster_ids: list[str]          # one or more target roster IDs
    thought: str                   # the idea to plant
    inject_on_day: int | None = None  # None = next tick
    operator_note: str | None = None


@router.post("/api/inception")
async def inject_inception(payload: InceptionPayload, db: Session = Depends(get_db)):
    clock = SimulationClock(db)
    date = clock.current_date_dict()
    inject_day = payload.inject_on_day or (date["total_days"] + 1)

    targets = []
    for rid in payload.roster_ids:
        char = db.query(Character).filter(Character.roster_id == rid).first()
        if char:
            targets.append(char)

    if not targets:
        raise HTTPException(status_code=404, detail="No valid characters found")

    event = InceptionEvent(
        target_roster_ids_json=json.dumps(payload.roster_ids),
        thought_content=payload.thought,
        injected_at_day=inject_day,
        operator_note=payload.operator_note,
    )
    db.add(event)
    db.commit()

    await manager.broadcast({
        "type": "inception",
        "data": {
            "targets": payload.roster_ids,
            "thought_preview": payload.thought[:80] + ("..." if len(payload.thought) > 80 else ""),
            "inject_on_day": inject_day,
        },
    })

    return {
        "ok": True,
        "targets": [c.roster_id for c in targets],
        "inject_on_day": inject_day,
    }


# ── Cost ─────────────────────────────────────────────────────────────────────

@router.get("/api/cost")
def get_cost(db: Session = Depends(get_db)):
    tracker = CostTracker(db)
    return tracker.status_dict()


# ── Satisfaction & disposition ────────────────────────────────────────────────

@router.get("/api/satisfaction/{roster_id}")
def get_satisfaction(roster_id: str, days: int = 14, db: Session = Depends(get_db)):
    from database.models import SatisfactionLog, CharacterDisposition
    char = db.query(Character).filter(Character.roster_id == roster_id).first()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    logs = (
        db.query(SatisfactionLog)
        .filter(SatisfactionLog.character_id == char.id)
        .order_by(SatisfactionLog.sim_day.desc())
        .limit(days)
        .all()
    )
    disposition = (
        db.query(CharacterDisposition)
        .filter(CharacterDisposition.character_id == char.id)
        .first()
    )
    return {
        "roster_id": roster_id,
        "drive": char.core_drive,
        "disposition": {
            "state": disposition.state if disposition else "neutral",
            "average": disposition.rolling_average if disposition else 0.0,
            "last_updated_day": disposition.last_updated_day if disposition else 0,
        },
        "history": [
            {"day": l.sim_day, "score": l.score}
            for l in reversed(logs)
        ],
    }


@router.get("/api/dispositions")
def all_dispositions(db: Session = Depends(get_db)):
    from database.models import CharacterDisposition
    chars = db.query(Character).filter(Character.alive == True).all()
    result = []
    for c in chars:
        d = (
            db.query(CharacterDisposition)
            .filter(CharacterDisposition.character_id == c.id)
            .first()
        )
        result.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "drive": c.core_drive,
            "state": d.state if d else "neutral",
            "average": round(d.rolling_average, 3) if d else 0.0,
        })
    # Sort by average ascending so most frustrated are at top
    result.sort(key=lambda x: x["average"])
    return result


# ── Visual map positions ──────────────────────────────────────────────────────

@router.get("/api/positions")
def get_positions(db: Session = Depends(get_db)):
    """
    Returns all living characters with their current location
    for the Phaser map to render on load.
    """
    from database.models import CharacterDisposition
    chars = db.query(Character).filter(Character.alive == True).all()
    result = []
    for c in chars:
        loc = db.query(Location).filter(Location.id == c.current_location_id).first()
        disp = (
            db.query(CharacterDisposition)
            .filter(CharacterDisposition.character_id == c.id)
            .first()
        )
        result.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "age": c.age,
            "is_minor": c.is_minor,
            "ai_model": c.ai_model,
            "core_drive": c.core_drive,
            "natural_tendency": c.natural_tendency,
            "physical_description": c.physical_description,
            "alive": c.alive,
            "location": loc.name if loc else "Central Square",
            "disposition": disp.state if disp else "neutral",
            "disposition_avg": round(disp.rolling_average, 3) if disp else 0.0,
        })
    return result


@router.get("/api/recent_dialogues")
def get_recent_dialogues(limit: int = 20, db: Session = Depends(get_db)):
    """Return recent dialogues for the map replay panel."""
    from database.models import Dialogue
    dialogues = (
        db.query(Dialogue)
        .order_by(Dialogue.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for d in dialogues:
        loc = db.query(Location).filter(Location.id == d.location_id).first()
        result.append({
            "id": d.id,
            "sim_day": d.sim_day,
            "location": loc.name if loc else "Unknown",
            "participants": d.participants,
            "dialogue": d.dialogue,
            "topic": d.topic,
        })
    return result


# ── Analytics endpoints ───────────────────────────────────────────────────────

@router.get("/api/relationships")
def get_relationship_web(db: Session = Depends(get_db)):
    """Returns all relationships for the relationship web overlay."""
    rels = db.query(CharacterRelationship).all()
    result = []
    for r in rels:
        char_a = db.query(Character).filter(Character.id == r.from_character_id).first()
        char_b = db.query(Character).filter(Character.id == r.to_character_id).first()
        if not char_a or not char_b:
            continue
        if r.familiarity < 0.1:
            continue  # skip near-strangers
        result.append({
            "from": char_a.roster_id,
            "to": char_b.roster_id,
            "from_name": char_a.given_name or char_a.roster_id,
            "to_name": char_b.given_name or char_b.roster_id,
            "trust": round(r.trust_level or 0, 3),
            "familiarity": round(r.familiarity or 0, 3),
            "interactions": r.interaction_count or 0,
            "bond_type": r.bond_type,
        })
    return result


@router.get("/api/heatmap")
def get_heatmap(days: int = 7, db: Session = Depends(get_db)):
    """Returns activity counts per location over the last N days."""
    from database.models import Dialogue, TickLog
    clock_row = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
    current_day = clock_row.sim_day if clock_row else 1
    since_day = max(1, current_day - days)

    dialogues = (
        db.query(Dialogue)
        .filter(Dialogue.sim_day >= since_day)
        .all()
    )
    counts: dict[str, int] = {}
    for d in dialogues:
        loc = db.query(Location).filter(Location.id == d.location_id).first()
        name = loc.name if loc else "Unknown"
        counts[name] = counts.get(name, 0) + 1

    max_count = max(counts.values(), default=1)
    return [
        {"location": name, "count": count, "intensity": round(count / max_count, 3)}
        for name, count in counts.items()
    ]


@router.get("/api/events/significant")
def get_significant_events(limit: int = 100, db: Session = Depends(get_db)):
    """Returns auto-detected significant events for the event journal."""
    from database.models import SignificantEvent
    events = (
        db.query(SignificantEvent)
        .order_by(SignificantEvent.sim_day.desc(), SignificantEvent.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for ev in events:
        char_ids = ev.character_ids
        chars = [
            db.query(Character).filter(Character.id == cid).first()
            for cid in char_ids
        ]
        names = [
            (c.given_name or c.roster_id) for c in chars if c
        ]
        result.append({
            "id": ev.id,
            "sim_day": ev.sim_day,
            "event_type": ev.event_type,
            "description": ev.description,
            "characters": names,
            "location": ev.location,
            "emotional_weight": ev.emotional_weight,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })
    return result


@router.get("/api/character_arcs")
def get_character_arcs(db: Session = Depends(get_db)):
    """Returns satisfaction history per character for arc charts."""
    from database.models import SatisfactionLog, CharacterDisposition
    chars = db.query(Character).filter(Character.alive == True).all()
    result = []
    for c in chars:
        logs = (
            db.query(SatisfactionLog)
            .filter(SatisfactionLog.character_id == c.id)
            .order_by(SatisfactionLog.sim_day.asc())
            .all()
        )
        disp = (
            db.query(CharacterDisposition)
            .filter(CharacterDisposition.character_id == c.id)
            .first()
        )
        result.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "core_drive": c.core_drive,
            "disposition": disp.state if disp else "neutral",
            "history": [
                {"day": l.sim_day, "score": l.score}
                for l in logs
            ],
        })
    return result


@router.get("/api/sim/days")
def get_sim_days(db: Session = Depends(get_db)):
    """Returns the range of simulated days available for the scrubber."""
    from database.models import Dialogue, TickLog
    min_day_row = db.query(Dialogue).order_by(Dialogue.sim_day.asc()).first()
    max_day_row = db.query(Dialogue).order_by(Dialogue.sim_day.desc()).first()
    clock = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
    return {
        "min_day": min_day_row.sim_day if min_day_row else 1,
        "max_day": max_day_row.sim_day if max_day_row else 1,
        "current_day": clock.sim_day if clock else 1,
    }


# ── Batch run ─────────────────────────────────────────────────────────────────

class BatchRunPayload(BaseModel):
    days: int = 30


@router.post("/api/sim/run_batch")
async def run_batch(payload: BatchRunPayload, db: Session = Depends(get_db)):
    """
    Run N simulation days sequentially in the background.
    Broadcasts progress via WebSocket after each day.
    Max 90 days per batch to prevent runaway cost.
    """
    from simulation.engine import SimulationEngine
    from simulation.clock import SimulationClock

    days = min(payload.days, 90)

    async def do_batch():
        engine = SimulationEngine(db)
        results = []

        # Mark as running so scheduler doesn't fire concurrent ticks
        from simulation.clock import SimulationClock
        clock = SimulationClock(db)
        clock.start()

        for i in range(days):
            if engine.cost.is_budget_exhausted():
                await manager.broadcast({
                    "type": "batch_stopped",
                    "data": {"reason": "budget_exhausted", "days_run": i},
                })
                break

            result = await engine.run_tick(broadcast_fn=manager.broadcast)

            await manager.broadcast({
                "type": "batch_progress",
                "data": {
                    "current": i + 1,
                    "total": days,
                    "day": result.get("sim_day"),
                    "date": result.get("date_display"),
                    "cost": result.get("cost_today"),
                },
            })
            results.append(result)

        await manager.broadcast({
            "type": "batch_complete",
            "data": {
                "days_run": len(results),
                "final_day": results[-1].get("sim_day") if results else 0,
                "final_date": results[-1].get("date_display") if results else "",
                "total_cost": results[-1].get("cost_today") if results else 0,
            },
        })

    import asyncio
    asyncio.create_task(do_batch())
    return {"ok": True, "days": days, "status": "running"}


# ── Biology status ────────────────────────────────────────────────────────────

@router.get("/api/biology")
def get_biology_status(db: Session = Depends(get_db)):
    """Returns current biological state for all living characters."""
    from database.models import CharacterBiology, PhysicalAttraction
    chars = db.query(Character).filter(Character.alive == True).all()
    result = []
    for c in chars:
        bio = (
            db.query(CharacterBiology)
            .filter(CharacterBiology.character_id == c.id)
            .first()
        )
        if not bio:
            continue
        result.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "age": c.age,
            "is_minor": c.is_minor,
            "hunger": round(bio.hunger, 1),
            "fatigue": round(bio.fatigue, 1),
            "bathroom_urgency": round(bio.bathroom_urgency, 1),
            "physical_comfort": round(bio.physical_comfort, 1),
            "hormonal_state": bio.hormonal_state if not c.is_minor else "n/a",
        })
    return result


# ── Action inject ─────────────────────────────────────────────────────────────

class ActionPayload(BaseModel):
    participant_roster_ids: list[str]      # characters IN the scene
    scene_description: str                 # what happens, written by operator
    witness_roster_ids: list[str] = []    # characters who observe
    perspective: str = "mutual"           # "mutual" | "observer" | "subject"
    inject_on_day: int | None = None      # None = next tick
    operator_note: str | None = None


@router.post("/api/action_inject")
async def inject_action(payload: ActionPayload, db: Session = Depends(get_db)):
    from database.models import ActionEvent
    from simulation.clock import SimulationClock
    import json

    clock = SimulationClock(db)
    date = clock.current_date_dict()
    inject_day = payload.inject_on_day or (date["total_days"] + 1)

    # Validate all roster IDs exist
    all_ids = payload.participant_roster_ids + payload.witness_roster_ids
    valid = []
    for rid in all_ids:
        char = db.query(Character).filter(Character.roster_id == rid).first()
        if char:
            valid.append(rid)

    if not payload.participant_roster_ids:
        raise HTTPException(status_code=400, detail="At least one participant required")

    event = ActionEvent(
        participant_roster_ids_json=json.dumps(payload.participant_roster_ids),
        witness_roster_ids_json=json.dumps(payload.witness_roster_ids),
        scene_description=payload.scene_description,
        perspective=payload.perspective,
        inject_on_day=inject_day,
        operator_note=payload.operator_note,
    )
    db.add(event)
    db.commit()

    await manager.broadcast({
        "type": "action_queued",
        "data": {
            "participants": payload.participant_roster_ids,
            "witnesses": payload.witness_roster_ids,
            "scene_preview": payload.scene_description[:80] + ("..." if len(payload.scene_description) > 80 else ""),
            "inject_on_day": inject_day,
        },
    })

    return {
        "ok": True,
        "event_id": event.id,
        "participants": payload.participant_roster_ids,
        "witnesses": payload.witness_roster_ids,
        "inject_on_day": inject_day,
    }


@router.get("/api/action_events")
def list_action_events(db: Session = Depends(get_db)):
    from database.models import ActionEvent
    events = db.query(ActionEvent).order_by(ActionEvent.id.desc()).limit(50).all()
    return [
        {
            "id": e.id,
            "participants": e.participant_ids,
            "witnesses": e.witness_ids,
            "scene": e.scene_description,
            "inject_on_day": e.inject_on_day,
            "processed": e.processed,
            "processed_day": e.processed_day,
            "perspective": e.perspective,
        }
        for e in events
    ]


# ── Power / Centrality map ────────────────────────────────────────────────────

@router.get("/api/power_map")
def get_power_map(db: Session = Depends(get_db)):
    """
    Returns centrality scores for each character.
    Centrality = weighted sum of familiarity + trust across all relationships.
    Also returns cluster detection based on mutual high-familiarity pairs.
    """
    chars = db.query(Character).filter(Character.alive == True).all()
    rels = db.query(CharacterRelationship).all()

    # Build adjacency scores
    scores: dict[str, dict] = {}
    for c in chars:
        scores[c.roster_id] = {
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "core_drive": c.core_drive,
            "age": c.age,
            "centrality": 0.0,
            "avg_trust": 0.0,
            "avg_familiarity": 0.0,
            "connection_count": 0,
            "trust_sum": 0.0,
            "fam_sum": 0.0,
            "top_connections": [],
        }

    # Score each relationship
    for rel in rels:
        char_a = db.query(Character).filter(
            Character.id == rel.from_character_id
        ).first()
        char_b = db.query(Character).filter(
            Character.id == rel.to_character_id
        ).first()
        if not char_a or not char_b:
            continue
        if char_a.roster_id not in scores or char_b.roster_id not in scores:
            continue

        fam = rel.familiarity or 0.0
        trust = rel.trust_level or 0.0
        interactions = rel.interaction_count or 0

        if fam < 0.05:
            continue

        # Weight: familiarity counts more than trust for centrality
        weight = fam * 0.6 + max(0, trust) * 0.4

        scores[char_a.roster_id]["centrality"] += weight
        scores[char_a.roster_id]["fam_sum"] += fam
        scores[char_a.roster_id]["trust_sum"] += trust
        scores[char_a.roster_id]["connection_count"] += 1
        scores[char_a.roster_id]["top_connections"].append({
            "roster_id": char_b.roster_id,
            "given_name": char_b.given_name,
            "familiarity": round(fam, 3),
            "trust": round(trust, 3),
            "interactions": interactions,
        })

    # Normalize and compute averages
    max_centrality = max(
        (s["centrality"] for s in scores.values()), default=1.0
    ) or 1.0

    result = []
    for rid, s in scores.items():
        conn = s["connection_count"]
        s["avg_trust"] = round(s["trust_sum"] / conn, 3) if conn else 0.0
        s["avg_familiarity"] = round(s["fam_sum"] / conn, 3) if conn else 0.0
        s["centrality_normalized"] = round(s["centrality"] / max_centrality, 3)
        # Sort top connections by familiarity
        s["top_connections"] = sorted(
            s["top_connections"],
            key=lambda x: x["familiarity"],
            reverse=True
        )[:5]
        # Clean up raw sums
        del s["trust_sum"], s["fam_sum"]
        result.append(s)

    result.sort(key=lambda x: x["centrality_normalized"], reverse=True)
    return result


# ── Ideological drift tracker ─────────────────────────────────────────────────

@router.get("/api/ideology_drift")
def get_ideology_drift(db: Session = Depends(get_db)):
    """
    Scans all dialogue for keyword frequency over time.
    Returns word frequency trends that reveal emerging ideological concepts.
    """
    import re
    from database.models import Dialogue

    # Concepts to track — these are sociologically meaningful word clusters
    CONCEPT_CLUSTERS = {
        "food & provision": [
            "food", "eat", "hungry", "hunger", "meal", "provision",
            "appear", "market", "where does", "who provides"
        ],
        "ownership & territory": [
            "mine", "my space", "belongs", "claim", "territory",
            "keep", "own", "ownership", "take", "theirs"
        ],
        "authority & leadership": [
            "decide", "leader", "authority", "rule", "together we",
            "who speaks", "council", "vote", "agree", "everyone should"
        ],
        "trust & alliance": [
            "trust", "rely", "count on", "together", "protect",
            "safe", "ally", "friend", "bond", "us"
        ],
        "conflict & tension": [
            "angry", "wrong", "unfair", "refuse", "won't",
            "against", "fight", "argue", "disagree", "tension"
        ],
        "mystery & origin": [
            "why", "where does", "who left", "explain", "source",
            "mystery", "strange", "cannot understand", "no one knows"
        ],
        "identity & naming": [
            "call me", "my name", "who am i", "i am", "i call",
            "name", "known as", "identity", "myself"
        ],
        "connection & longing": [
            "lonely", "together", "close", "feel", "care",
            "want you", "miss", "near", "with me", "hold"
        ],
    }

    # Get all dialogues grouped by sim_day
    dialogues = db.query(Dialogue).order_by(Dialogue.sim_day.asc()).all()

    # Group by day buckets (every 5 days)
    day_buckets: dict[int, str] = {}
    for d in dialogues:
        bucket = (d.sim_day // 5) * 5
        text = " ".join(
            ex.get("text", "")
            for ex in d.dialogue
            if isinstance(ex, dict)
        ).lower()
        day_buckets[bucket] = day_buckets.get(bucket, "") + " " + text

    if not day_buckets:
        return {"concepts": list(CONCEPT_CLUSTERS.keys()), "series": []}

    sorted_buckets = sorted(day_buckets.keys())

    series = []
    for concept, keywords in CONCEPT_CLUSTERS.items():
        data_points = []
        for bucket in sorted_buckets:
            text = day_buckets[bucket]
            word_count = max(len(text.split()), 1)
            hits = sum(
                len(re.findall(r'\b' + re.escape(kw) + r'\b', text))
                for kw in keywords
            )
            # Normalize per 1000 words
            freq = round((hits / word_count) * 1000, 2)
            data_points.append({"day": bucket, "freq": freq})
        series.append({"concept": concept, "data": data_points})

    return {
        "concepts": list(CONCEPT_CLUSTERS.keys()),
        "buckets": sorted_buckets,
        "series": series,
    }


# ── Injection impact tracker ──────────────────────────────────────────────────

@router.get("/api/injection_impact")
def get_injection_impact(db: Session = Depends(get_db)):
    """
    Correlates all injections (inception + action) with character
    satisfaction trajectories in the 7 days before and after.
    Measures whether injections are actually changing outcomes.
    """
    from database.models import InceptionEvent, ActionEvent, SatisfactionLog
    import json

    results = []

    # ── Inception events ──────────────────────────────────────────────────────
    inceptions = db.query(InceptionEvent).all()
    for ev in inceptions:
        target_ids = json.loads(ev.target_roster_ids_json or "[]")
        inject_day = ev.injected_at_day

        for roster_id in target_ids:
            char = db.query(Character).filter(
                Character.roster_id == roster_id
            ).first()
            if not char:
                continue

            # Satisfaction 7 days before
            before = (
                db.query(SatisfactionLog)
                .filter(
                    SatisfactionLog.character_id == char.id,
                    SatisfactionLog.sim_day >= inject_day - 7,
                    SatisfactionLog.sim_day < inject_day,
                )
                .all()
            )
            # Satisfaction 7 days after
            after = (
                db.query(SatisfactionLog)
                .filter(
                    SatisfactionLog.character_id == char.id,
                    SatisfactionLog.sim_day > inject_day,
                    SatisfactionLog.sim_day <= inject_day + 7,
                )
                .all()
            )

            avg_before = (
                sum(s.score for s in before) / len(before)
                if before else None
            )
            avg_after = (
                sum(s.score for s in after) / len(after)
                if after else None
            )

            delta = None
            if avg_before is not None and avg_after is not None:
                delta = round(avg_after - avg_before, 3)

            results.append({
                "type": "inception",
                "inject_day": inject_day,
                "character": roster_id,
                "given_name": char.given_name,
                "content_preview": ev.thought_content[:80] if ev.thought_content else "",
                "avg_satisfaction_before": round(avg_before, 3) if avg_before else None,
                "avg_satisfaction_after": round(avg_after, 3) if avg_after else None,
                "delta": delta,
                "impact": (
                    "positive" if delta and delta > 0.1
                    else "negative" if delta and delta < -0.1
                    else "neutral" if delta is not None
                    else "insufficient_data"
                ),
            })

    # ── Action events ─────────────────────────────────────────────────────────
    actions = db.query(ActionEvent).filter(ActionEvent.processed == True).all()
    for ev in actions:
        import json as _json
        all_rids = (
            _json.loads(ev.participant_roster_ids_json or "[]") +
            _json.loads(ev.witness_roster_ids_json or "[]")
        )
        inject_day = ev.processed_day or ev.inject_on_day

        char_impacts = []
        for roster_id in all_rids:
            char = db.query(Character).filter(
                Character.roster_id == roster_id
            ).first()
            if not char:
                continue

            before = (
                db.query(SatisfactionLog)
                .filter(
                    SatisfactionLog.character_id == char.id,
                    SatisfactionLog.sim_day >= inject_day - 7,
                    SatisfactionLog.sim_day < inject_day,
                )
                .all()
            )
            after = (
                db.query(SatisfactionLog)
                .filter(
                    SatisfactionLog.character_id == char.id,
                    SatisfactionLog.sim_day > inject_day,
                    SatisfactionLog.sim_day <= inject_day + 7,
                )
                .all()
            )

            avg_before = (
                sum(s.score for s in before) / len(before)
                if before else None
            )
            avg_after = (
                sum(s.score for s in after) / len(after)
                if after else None
            )
            delta = None
            if avg_before is not None and avg_after is not None:
                delta = round(avg_after - avg_before, 3)

            char_impacts.append({
                "roster_id": roster_id,
                "given_name": char.given_name,
                "delta": delta,
                "avg_before": round(avg_before, 3) if avg_before else None,
                "avg_after": round(avg_after, 3) if avg_after else None,
            })

        results.append({
            "type": "action",
            "inject_day": inject_day,
            "scene_preview": ev.scene_description[:80],
            "characters": char_impacts,
            "perspective": ev.perspective,
            "overall_impact": (
                "positive"
                if char_impacts and
                   sum(c["delta"] or 0 for c in char_impacts) > 0
                else "negative"
                if char_impacts and
                   sum(c["delta"] or 0 for c in char_impacts) < 0
                else "neutral"
            ),
        })

    # ── Discover location events ──────────────────────────────────────────────
    from database.models import EmergentLocation
    discoveries = db.query(EmergentLocation).all()
    for ev in discoveries:
        from database.models import Location as Loc
        loc = db.query(Loc).filter(Loc.id == ev.location_id).first()
        discoverer = db.query(Character).filter(Character.id == ev.discovered_by_id).first()
        if not discoverer:
            continue
        inject_day = ev.discovery_day
        before = (
            db.query(SatisfactionLog)
            .filter(
                SatisfactionLog.character_id == discoverer.id,
                SatisfactionLog.sim_day >= inject_day - 7,
                SatisfactionLog.sim_day < inject_day,
            ).all()
        )
        after = (
            db.query(SatisfactionLog)
            .filter(
                SatisfactionLog.character_id == discoverer.id,
                SatisfactionLog.sim_day > inject_day,
                SatisfactionLog.sim_day <= inject_day + 7,
            ).all()
        )
        avg_before = sum(s.score for s in before) / len(before) if before else None
        avg_after = sum(s.score for s in after) / len(after) if after else None
        delta = round(avg_after - avg_before, 3) if avg_before is not None and avg_after is not None else None
        results.append({
            "type": "discovery",
            "inject_day": inject_day,
            "character": discoverer.roster_id,
            "given_name": discoverer.given_name,
            "content_preview": f"Discovered: {loc.name if loc else 'unknown location'}" + (" [OUTSIDE]" if ev.is_outside else ""),
            "avg_satisfaction_before": round(avg_before, 3) if avg_before else None,
            "avg_satisfaction_after": round(avg_after, 3) if avg_after else None,
            "delta": delta,
            "impact": (
                "positive" if delta and delta > 0.1
                else "negative" if delta and delta < -0.1
                else "neutral" if delta is not None
                else "insufficient_data"
            ),
        })

    results.sort(key=lambda x: x["inject_day"])
    return results


# ── Drive distribution outcomes ───────────────────────────────────────────────

@router.get("/api/drive_outcomes")
def get_drive_outcomes(db: Session = Depends(get_db)):
    """
    Average satisfaction score per drive type over time.
    Shows which drives are thriving in Caldwell's specific conditions.
    """
    from database.models import SatisfactionLog
    chars = db.query(Character).filter(Character.alive == True).all()

    # Map character_id -> drive
    drive_map = {c.id: c.core_drive for c in chars}
    drives = sorted(set(drive_map.values()))

    # Get all satisfaction logs
    logs = db.query(SatisfactionLog).order_by(SatisfactionLog.sim_day.asc()).all()

    # Bucket by 5-day windows per drive
    from collections import defaultdict
    drive_buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    for log in logs:
        drive = drive_map.get(log.character_id)
        if not drive:
            continue
        bucket = (log.sim_day // 5) * 5
        drive_buckets[drive][bucket].append(log.score)

    all_buckets = sorted(set(
        b for drive_data in drive_buckets.values()
        for b in drive_data.keys()
    ))

    series = []
    for drive in drives:
        data = []
        for bucket in all_buckets:
            scores = drive_buckets[drive].get(bucket, [])
            avg = round(sum(scores) / len(scores), 3) if scores else None
            data.append({"day": bucket, "avg": avg, "n": len(scores)})
        # Overall stats
        all_scores = [
            s for bucket_scores in drive_buckets[drive].values()
            for s in bucket_scores
        ]
        series.append({
            "drive": drive,
            "data": data,
            "overall_avg": round(sum(all_scores) / len(all_scores), 3) if all_scores else 0,
            "character_count": sum(1 for c in chars if c.core_drive == drive),
        })

    series.sort(key=lambda x: x["overall_avg"], reverse=True)
    return {"drives": drives, "buckets": all_buckets, "series": series}


# ── Relationship trajectory ───────────────────────────────────────────────────

@router.get("/api/relationship_trajectory")
def get_relationship_trajectory(db: Session = Depends(get_db)):
    """
    Returns the fastest-rising and fastest-falling relationships,
    plus relationships that have been stuck at neutral for a long time.
    """
    rels = db.query(CharacterRelationship).all()
    result = {"rising": [], "falling": [], "stuck": [], "strongest": [], "coldest": []}

    for rel in rels:
        char_a = db.query(Character).filter(Character.id == rel.from_character_id).first()
        char_b = db.query(Character).filter(Character.id == rel.to_character_id).first()
        if not char_a or not char_b:
            continue
        if rel.interaction_count < 3:
            continue

        name_a = char_a.given_name or char_a.roster_id
        name_b = char_b.given_name or char_b.roster_id
        fam = rel.familiarity or 0.0
        trust = rel.trust_level or 0.0
        interactions = rel.interaction_count or 0

        entry = {
            "from": char_a.roster_id,
            "to": char_b.roster_id,
            "from_name": name_a,
            "to_name": name_b,
            "familiarity": round(fam, 3),
            "trust": round(trust, 3),
            "interactions": interactions,
            "bond_type": rel.bond_type,
        }

        # Strongest bonds — high familiarity + trust
        if fam >= 0.4 and trust >= 0.15:
            result["strongest"].append(entry)

        # Coldest — low trust relative to familiarity
        if trust < 0.08 and fam > 0.1:
            result["coldest"].append(entry)

        # Stuck — multiple interactions but familiarity not growing
        if interactions >= 5 and fam < 0.15:
            result["stuck"].append({**entry, "note": "Many interactions, familiarity not growing"})

        # Rising — high trust relative to interaction count (bonding fast)
        if trust > 0.18 and fam > 0.2 and interactions <= 12:
            result["rising"].append(entry)

        # Falling — familiarity high but trust lagging behind
        if fam > 0.3 and trust < 0.1:
            result["falling"].append({**entry, "note": "Familiar but trust hasn't followed"})

    # Sort and limit
    result["strongest"] = sorted(
        result["strongest"], key=lambda x: x["familiarity"] + x["trust"], reverse=True
    )[:8]
    result["coldest"] = sorted(
        result["coldest"], key=lambda x: x["trust"]
    )[:5]
    result["stuck"] = sorted(
        result["stuck"], key=lambda x: x["interactions"], reverse=True
    )[:5]
    result["rising"] = sorted(
        result["rising"], key=lambda x: x["trust"], reverse=True
    )[:5]
    result["falling"] = sorted(
        result["falling"], key=lambda x: x["trust"]
    )[:5]

    return result


# ── Naming sociology ──────────────────────────────────────────────────────────

@router.get("/api/naming_sociology")
def get_naming_sociology(db: Session = Depends(get_db)):
    """
    Returns detailed data about the naming timeline:
    who named themselves, when, what drive they had,
    how many interactions preceded the naming,
    and what the population naming rate is over time.
    """
    from database.models import Memory, Dialogue
    import re

    chars = db.query(Character).filter(Character.alive == True).all()
    named = [c for c in chars if c.given_name]
    unnamed = [c for c in chars if not c.given_name]

    naming_events = []
    for char in named:
        # Find the earliest memory that contains their name
        name_lower = char.given_name.lower()
        name_memory = (
            db.query(Memory)
            .filter(
                Memory.character_id == char.id,
                Memory.content.ilike(f"%{char.given_name}%"),
            )
            .order_by(Memory.sim_day.asc())
            .first()
        )
        naming_day = name_memory.sim_day if name_memory else None

        # Count interactions before naming
        interactions_before = 0
        if naming_day:
            rels = db.query(CharacterRelationship).filter(
                (CharacterRelationship.from_character_id == char.id) |
                (CharacterRelationship.to_character_id == char.id)
            ).all()
            interactions_before = sum(r.interaction_count or 0 for r in rels)

        naming_events.append({
            "roster_id": char.roster_id,
            "given_name": char.given_name,
            "gender": char.gender,
            "age": char.age,
            "core_drive": char.core_drive,
            "natural_tendency": char.natural_tendency,
            "naming_day": naming_day,
            "interactions_before_naming": interactions_before,
        })

    naming_events.sort(key=lambda x: x["naming_day"] or 9999)

    # Drive breakdown of named vs unnamed
    drive_stats = {}
    for c in chars:
        d = c.core_drive
        if d not in drive_stats:
            drive_stats[d] = {"drive": d, "total": 0, "named": 0}
        drive_stats[d]["total"] += 1
        if c.given_name:
            drive_stats[d]["named"] += 1

    for d in drive_stats:
        t = drive_stats[d]["total"]
        drive_stats[d]["naming_rate"] = round(
            drive_stats[d]["named"] / t, 2
        ) if t else 0

    # Naming velocity — cumulative names over time
    naming_timeline = []
    running_total = 0
    for ev in naming_events:
        if ev["naming_day"]:
            running_total += 1
            naming_timeline.append({
                "day": ev["naming_day"],
                "cumulative": running_total,
                "name": ev["given_name"],
            })

    # Unnamed characters — how long they've gone without naming
    from database.models import TickLog
    last_tick = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
    current_day = last_tick.sim_day if last_tick else 0

    unnamed_data = []
    for c in unnamed:
        rels = db.query(CharacterRelationship).filter(
            (CharacterRelationship.from_character_id == c.id) |
            (CharacterRelationship.to_character_id == c.id)
        ).all()
        total_interactions = sum(r.interaction_count or 0 for r in rels)
        unnamed_data.append({
            "roster_id": c.roster_id,
            "gender": c.gender,
            "age": c.age,
            "core_drive": c.core_drive,
            "days_alive": current_day,
            "total_interactions": total_interactions,
            "natural_tendency": c.natural_tendency[:50],
        })

    unnamed_data.sort(key=lambda x: x["total_interactions"], reverse=True)

    return {
        "total_population": len(chars),
        "named_count": len(named),
        "unnamed_count": len(unnamed),
        "naming_rate": round(len(named) / len(chars), 3) if chars else 0,
        "naming_events": naming_events,
        "naming_timeline": naming_timeline,
        "drive_breakdown": sorted(
            drive_stats.values(), key=lambda x: x["naming_rate"], reverse=True
        ),
        "unnamed_characters": unnamed_data[:10],
    }


# ── Biology analytics ─────────────────────────────────────────────────────────

@router.get("/api/biology_analytics")
def get_biology_analytics(db: Session = Depends(get_db)):
    """
    Full biological layer analytics:
    - Biological satisfaction per character
    - Hormonal state distribution across population
    - Bio vs psychological satisfaction correlation
    - Physical attraction network
    """
    from database.models import CharacterBiology, PhysicalAttraction, SatisfactionLog

    chars = db.query(Character).filter(Character.alive == True).all()

    # ── Per-character biological state ────────────────────────────────────────
    char_bio = []
    hormonal_counts = {}
    total_hunger = 0
    total_fatigue = 0
    total_bathroom = 0
    bio_count = 0

    for c in chars:
        bio = (
            db.query(CharacterBiology)
            .filter(CharacterBiology.character_id == c.id)
            .first()
        )
        if not bio:
            continue

        # Biological satisfaction: inverse of average need urgency
        hunger_norm = bio.hunger / 10.0
        fatigue_norm = bio.fatigue / 10.0
        bathroom_norm = bio.bathroom_urgency / 10.0
        bio_sat = round(1.0 - (hunger_norm * 0.4 + fatigue_norm * 0.35 + bathroom_norm * 0.25), 3)

        # Psychological satisfaction (last 7 days average)
        psych_logs = (
            db.query(SatisfactionLog)
            .filter(SatisfactionLog.character_id == c.id)
            .order_by(SatisfactionLog.sim_day.desc())
            .limit(7)
            .all()
        )
        psych_sat = round(
            sum(l.score for l in psych_logs) / len(psych_logs), 3
        ) if psych_logs else None

        hormonal_state = bio.hormonal_state if not c.is_minor else "n/a"
        hormonal_counts[hormonal_state] = hormonal_counts.get(hormonal_state, 0) + 1

        total_hunger += bio.hunger
        total_fatigue += bio.fatigue
        total_bathroom += bio.bathroom_urgency
        bio_count += 1

        char_bio.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "age": c.age,
            "is_minor": c.is_minor,
            "core_drive": c.core_drive,
            "hunger": round(bio.hunger, 1),
            "fatigue": round(bio.fatigue, 1),
            "bathroom_urgency": round(bio.bathroom_urgency, 1),
            "physical_comfort": round(bio.physical_comfort, 1),
            "bio_satisfaction": bio_sat,
            "psych_satisfaction": psych_sat,
            "hormonal_state": hormonal_state,
            "satisfaction_delta": round(psych_sat - bio_sat, 3) if psych_sat is not None else None,
        })

    char_bio.sort(key=lambda x: x["bio_satisfaction"])

    # ── Population averages ───────────────────────────────────────────────────
    population_avg = {
        "avg_hunger": round(total_hunger / bio_count, 2) if bio_count else 0,
        "avg_fatigue": round(total_fatigue / bio_count, 2) if bio_count else 0,
        "avg_bathroom": round(total_bathroom / bio_count, 2) if bio_count else 0,
    }

    # ── Correlation: bio sat vs psych sat ─────────────────────────────────────
    correlation_data = [
        {"roster_id": c["roster_id"], "given_name": c["given_name"],
         "bio": c["bio_satisfaction"], "psych": c["psych_satisfaction"],
         "drive": c["core_drive"], "gender": c["gender"]}
        for c in char_bio
        if c["psych_satisfaction"] is not None
    ]

    # Simple correlation coefficient
    if len(correlation_data) >= 3:
        bio_vals = [d["bio"] for d in correlation_data]
        psych_vals = [d["psych"] for d in correlation_data]
        n = len(bio_vals)
        mean_bio = sum(bio_vals) / n
        mean_psych = sum(psych_vals) / n
        numerator = sum(
            (b - mean_bio) * (p - mean_psych)
            for b, p in zip(bio_vals, psych_vals)
        )
        denom_bio = sum((b - mean_bio)**2 for b in bio_vals) ** 0.5
        denom_psych = sum((p - mean_psych)**2 for p in psych_vals) ** 0.5
        correlation = round(
            numerator / (denom_bio * denom_psych), 3
        ) if denom_bio * denom_psych > 0 else 0
    else:
        correlation = None

    # ── Physical attraction network ───────────────────────────────────────────
    attractions = (
        db.query(PhysicalAttraction)
        .filter(PhysicalAttraction.attraction_level >= 0.3)
        .order_by(PhysicalAttraction.attraction_level.desc())
        .all()
    )
    attraction_network = []
    for attr in attractions:
        char_from = db.query(Character).filter(
            Character.id == attr.from_character_id
        ).first()
        char_to = db.query(Character).filter(
            Character.id == attr.to_character_id
        ).first()
        if not char_from or not char_to:
            continue

        # Check if mutual
        mutual = db.query(PhysicalAttraction).filter(
            PhysicalAttraction.from_character_id == attr.to_character_id,
            PhysicalAttraction.to_character_id == attr.from_character_id,
        ).first()

        # Check familiarity between pair
        rel = db.query(CharacterRelationship).filter(
            CharacterRelationship.from_character_id == attr.from_character_id,
            CharacterRelationship.to_character_id == attr.to_character_id,
        ).first()
        familiarity = rel.familiarity if rel else 0.0

        attraction_network.append({
            "from": char_from.roster_id,
            "from_name": char_from.given_name or char_from.roster_id,
            "from_gender": char_from.gender,
            "to": char_to.roster_id,
            "to_name": char_to.given_name or char_to.roster_id,
            "to_gender": char_to.gender,
            "attraction_level": round(attr.attraction_level, 3),
            "is_mutual": mutual is not None,
            "familiarity": round(familiarity, 3),
            "acknowledged": attr.acknowledged,
            "intensity": (
                "high" if attr.attraction_level >= 0.65
                else "medium" if attr.attraction_level >= 0.42
                else "low"
            ),
        })

    return {
        "character_biology": char_bio,
        "population_avg": population_avg,
        "hormonal_distribution": hormonal_counts,
        "correlation": correlation,
        "correlation_interpretation": (
            "strong positive — biological wellbeing drives psychological flourishing"
            if correlation and correlation > 0.5
            else "moderate positive — some link between biological and psychological state"
            if correlation and correlation > 0.2
            else "weak/no correlation — psychological and biological states are decoupled"
            if correlation and correlation >= -0.2
            else "negative — characters flourish psychologically despite biological stress"
            if correlation is not None
            else "insufficient data"
        ),
        "attraction_network": attraction_network,
        "mutual_attraction_count": sum(
            1 for a in attraction_network if a["is_mutual"]
        ),
        "high_intensity_count": sum(
            1 for a in attraction_network if a["intensity"] == "high"
        ),
    }


# ── Character transcript viewer ───────────────────────────────────────────────

@router.get("/api/character_transcripts/{roster_id}")
def get_character_transcripts(
    roster_id: str,
    db: Session = Depends(get_db),
    limit: int = 100,
):
    """
    Returns all conversations a character participated in,
    with full exchange text, ordered by day.
    """
    from database.models import Dialogue

    char = db.query(Character).filter(Character.roster_id == roster_id).first()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")

    # Find all dialogues this character was in
    all_dialogues = db.query(Dialogue).order_by(Dialogue.sim_day.asc()).all()

    char_dialogues = []
    for d in all_dialogues:
        if char.id not in d.participants:
            continue

        # Get other participants
        others = []
        for pid in d.participants:
            if pid == char.id:
                continue
            other = db.query(Character).filter(Character.id == pid).first()
            if other:
                others.append({
                    "roster_id": other.roster_id,
                    "given_name": other.given_name,
                    "gender": other.gender,
                })

        loc = db.query(Location).filter(Location.id == d.location_id).first()

        char_dialogues.append({
            "sim_day": d.sim_day,
            "location": loc.name if loc else "Unknown",
            "topic": d.topic,
            "participants": others,
            "is_group": len(d.participants) > 2,
            "is_action_inject": bool(d.topic and "ACTION INJECT" in (d.topic or "")),
            "exchanges": d.dialogue,
            "exchange_count": len([
                e for e in d.dialogue
                if e.get("roster_id") != "OPERATOR"
            ]),
        })

    return {
        "roster_id": char.roster_id,
        "given_name": char.given_name,
        "gender": char.gender,
        "age": char.age,
        "core_drive": char.core_drive,
        "total_conversations": len(char_dialogues),
        "conversations": char_dialogues[-limit:],
    }


# ── Theme search across all dialogues ────────────────────────────────────────

@router.get("/api/theme_search")
def theme_search(
    query: str,
    roster_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """
    Search all dialogue text for a theme or keyword.
    Optionally filter by character roster_id.
    Returns matching exchanges with context.
    """
    from database.models import Dialogue
    import re

    query_lower = query.lower().strip()
    if not query_lower:
        return []

    all_dialogues = (
        db.query(Dialogue)
        .order_by(Dialogue.sim_day.desc())
        .all()
    )

    results = []
    for d in all_dialogues:
        # Filter by character if requested
        if roster_id:
            char = db.query(Character).filter(
                Character.roster_id == roster_id
            ).first()
            if not char or char.id not in d.participants:
                continue

        loc = db.query(Location).filter(Location.id == d.location_id).first()

        # Search exchanges
        matching_exchanges = []
        for ex in d.dialogue:
            text = ex.get("text", "").lower()
            if query_lower in text:
                # Highlight match
                speaker_char = db.query(Character).filter(
                    Character.roster_id == ex.get("roster_id")
                ).first()
                matching_exchanges.append({
                    "roster_id": ex.get("roster_id"),
                    "given_name": ex.get("given_name") or (
                        speaker_char.given_name if speaker_char else None
                    ),
                    "gender": speaker_char.gender if speaker_char else None,
                    "text": ex.get("text", ""),
                })

        if not matching_exchanges:
            continue

        # Get participant names
        participants = []
        for pid in d.participants:
            pc = db.query(Character).filter(Character.id == pid).first()
            if pc:
                participants.append({
                    "roster_id": pc.roster_id,
                    "given_name": pc.given_name,
                    "gender": pc.gender,
                })

        results.append({
            "sim_day": d.sim_day,
            "location": loc.name if loc else "Unknown",
            "participants": participants,
            "matching_exchanges": matching_exchanges,
            "total_exchanges": len(d.dialogue),
            "is_action_inject": bool(d.topic and "ACTION INJECT" in (d.topic or "")),
        })

        if len(results) >= limit:
            break

    return results


# ── World expansion ───────────────────────────────────────────────────────────

@router.post("/api/discover_location")
async def discover_location_endpoint(
    roster_id: str = "",
    description: str = "",
    is_outside: bool = False,
    custom_name: str | None = None,
    db: Session = Depends(get_db),
):
    from simulation.world_expansion import create_emergent_location
    from simulation.clock import SimulationClock
    char = db.query(Character).filter(Character.roster_id == roster_id).first()
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    clock = SimulationClock(db)
    sim_day = clock.current_date_dict()["total_days"]
    new_loc = create_emergent_location(char, description, is_outside, sim_day, db, custom_name)
    if not new_loc:
        raise HTTPException(status_code=400, detail="Location creation failed")
    await manager.broadcast({
        "type": "location_discovered",
        "data": {"character": char.roster_id, "given_name": char.given_name,
                 "location": new_loc.name, "is_outside": is_outside, "sim_day": sim_day},
    })
    return {"ok": True, "location_id": new_loc.id, "location_name": new_loc.name}


@router.get("/api/world_map")
def get_world_map(db: Session = Depends(get_db)):
    from database.models import EmergentLocation, CharacterDisposition, CharacterBiology
    chars = db.query(Character).filter(Character.alive == True).all()
    locs = db.query(Location).all()
    char_data = []
    for c in chars:
        loc = db.query(Location).filter(Location.id == c.current_location_id).first()
        disp = db.query(CharacterDisposition).filter(CharacterDisposition.character_id == c.id).first()
        bio = db.query(CharacterBiology).filter(CharacterBiology.character_id == c.id).first()
        char_data.append({
            "roster_id": c.roster_id, "given_name": c.given_name, "gender": c.gender,
            "location": loc.name if loc else "Unknown", "location_id": c.current_location_id,
            "disposition": disp.state if disp else "neutral",
            "hormonal_state": bio.hormonal_state if bio and not c.is_minor else "baseline",
        })
    loc_data = []
    for loc in locs:
        emergent = db.query(EmergentLocation).filter(EmergentLocation.location_id == loc.id).first()
        occupants = [c.roster_id for c in chars if c.current_location_id == loc.id]
        loc_data.append({
            "id": loc.id, "name": loc.name, "occupants": occupants,
            "occupant_count": len(occupants),
            "is_emergent": emergent is not None,
            "is_outside": emergent.is_outside if emergent else False,
            "map_x": emergent.map_x if emergent else None,
            "map_y": emergent.map_y if emergent else None,
            "discovery_day": emergent.discovery_day if emergent else None,
        })
    return {"characters": char_data, "locations": loc_data}


# ── Procreation endpoints ─────────────────────────────────────────────────────

@router.get("/api/pregnancies")
def get_pregnancies(db: Session = Depends(get_db)):
    """Returns all pregnancies — active and historical."""
    from database.models import Pregnancy
    pregnancies = db.query(Pregnancy).order_by(Pregnancy.conception_day.desc()).all()
    result = []
    for p in pregnancies:
        mother = db.query(Character).filter(Character.id == p.mother_id).first()
        father = db.query(Character).filter(Character.id == p.father_id).first() if p.father_id else None
        infant = db.query(Character).filter(Character.id == p.born_character_id).first() if p.born_character_id else None
        result.append({
            "id": p.id,
            "status": p.status,
            "conception_day": p.conception_day,
            "expected_birth_day": p.expected_birth_day,
            "actual_birth_day": p.actual_birth_day,
            "mother": mother.roster_id if mother else None,
            "mother_name": mother.given_name if mother else None,
            "father": father.roster_id if father else None,
            "father_name": father.given_name if father else None,
            "infant_roster_id": infant.roster_id if infant else None,
            "infant_gender": infant.gender if infant else None,
        })
    return result


@router.get("/api/infants")
def get_infants(db: Session = Depends(get_db)):
    """Returns all living infants and young children."""
    infants = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == True,
    ).all()
    result = []
    for inf in infants:
        from database.models import CharacterBiology, Pregnancy
        bio = db.query(CharacterBiology).filter(
            CharacterBiology.character_id == inf.id
        ).first()
        preg = db.query(Pregnancy).filter(
            Pregnancy.born_character_id == inf.id
        ).first()
        mother = db.query(Character).filter(
            Character.id == preg.mother_id
        ).first() if preg else None
        father = db.query(Character).filter(
            Character.id == preg.father_id
        ).first() if preg and preg.father_id else None
        loc = db.query(Location).filter(
            Location.id == inf.current_location_id
        ).first()
        result.append({
            "roster_id": inf.roster_id,
            "gender": inf.gender,
            "age_float": bio.age_float if bio else 0,
            "mother": mother.roster_id if mother else None,
            "mother_name": mother.given_name if mother else None,
            "father": father.roster_id if father else None,
            "father_name": father.given_name if father else None,
            "location": loc.name if loc else "Unknown",
            "is_infant": inf.is_infant,
        })
    return result


# ── Resource and environment endpoints ───────────────────────────────────────

@router.get("/api/resources")
def get_resources(db: Session = Depends(get_db)):
    """Current food supply and resource state."""
    from simulation.resource_manager import get_resource_status
    return get_resource_status(db)


@router.get("/api/environment")
def get_environment(db: Session = Depends(get_db)):
    """Active and historical environment events."""
    from database.models import EnvironmentEvent
    from simulation.clock import SimulationClock
    clock = SimulationClock(db)
    sim_day = clock.current_date_dict()["total_days"]
    events = db.query(EnvironmentEvent).order_by(
        EnvironmentEvent.start_day.desc()
    ).limit(20).all()
    return [{
        "type": ev.event_type,
        "description": ev.description,
        "start_day": ev.start_day,
        "end_day": ev.end_day,
        "severity": ev.severity,
        "resolved": ev.resolved,
        "is_active": (
            ev.start_day <= sim_day and
            not ev.resolved and
            (ev.end_day is None or ev.end_day >= sim_day)
        ),
    } for ev in events]


@router.get("/api/status_scores")
def get_status_scores(db: Session = Depends(get_db)):
    """Social status scores for all characters."""
    from database.models import StatusScore
    scores = db.query(StatusScore).all()
    result = []
    for s in scores:
        char = db.query(Character).filter(Character.id == s.character_id).first()
        if char and char.alive:
            result.append({
                "roster_id": char.roster_id,
                "given_name": char.given_name,
                "score": round(s.score, 1),
                "times_shared": s.times_shared_food,
                "times_hoarded": s.times_hoarded,
                "times_helped": s.times_helped,
            })
    return sorted(result, key=lambda x: x["score"], reverse=True)


@router.get("/api/sexual_encounters")
def get_sexual_encounters(db: Session = Depends(get_db)):
    """History of sexual encounters in Caldwell."""
    from database.models import SexualEncounter
    import json
    encounters = db.query(SexualEncounter).order_by(
        SexualEncounter.sim_day.desc()
    ).all()
    result = []
    for enc in encounters:
        char_a = db.query(Character).filter(Character.id == enc.character_a_id).first()
        char_b = db.query(Character).filter(Character.id == enc.character_b_id).first()
        loc = db.query(Location).filter(Location.id == enc.location_id).first()
        result.append({
            "sim_day": enc.sim_day,
            "character_a": char_a.roster_id if char_a else None,
            "name_a": char_a.given_name if char_a else None,
            "character_b": char_b.roster_id if char_b else None,
            "name_b": char_b.given_name if char_b else None,
            "location": loc.name if loc else None,
            "intensity": enc.intensity,
            "witness_count": len(json.loads(enc.witness_ids_json or "[]")),
        })
    return result


@router.get("/api/norms")
def get_norms(db: Session = Depends(get_db)):
    """Emerging community norms."""
    from database.models import NormRecord
    norms = db.query(NormRecord).filter(NormRecord.is_active == True).all()
    return [{
        "type": n.norm_type,
        "description": n.description,
        "emerged_day": n.emerged_day,
        "strength": round(n.strength, 2),
        "violations": n.violated_count,
        "reinforcements": n.reinforced_count,
    } for n in norms]


@router.get("/api/recent_scenes")
def get_recent_scenes(limit: int = 30, db: Session = Depends(get_db)):
    """Recent scenes with type, pressure, participants, and summary."""
    try:
        from database.models import Scene
        scenes = (
            db.query(Scene)
            .order_by(Scene.sim_day.desc(), Scene.id.desc())
            .limit(limit)
            .all()
        )
    except Exception:
        return []

    result = []
    for s in scenes:
        try:
            loc = db.query(Location).filter(Location.id == s.location_id).first()
            p_ids = json.loads(s.participant_ids_json or "[]")
            chars = [db.query(Character).filter(Character.id == cid).first() for cid in p_ids]
            chars = [c for c in chars if c]
            exchanges = json.loads(s.dialogue_json or "[]")
            result.append({
                "id": s.id,
                "sim_day": s.sim_day,
                "scene_type": s.scene_type,
                "pressure_type": s.pressure_type,
                "location": loc.name if loc else "Unknown",
                "participants": [
                    {"roster_id": c.roster_id, "given_name": c.given_name, "gender": c.gender}
                    for c in chars
                ],
                "exchange_count": len(exchanges),
                "is_group": len(chars) >= 3,
                "first_line": exchanges[0]["text"][:120] if exchanges else "",
            })
        except Exception:
            continue
    return result


@router.get("/api/cast_status")
def get_cast_status(db: Session = Depends(get_db)):
    """All characters with active/departed status and scene participation."""
    all_chars = db.query(Character).order_by(Character.roster_id).all()

    # Count scene appearances — try Scene table first, fall back to Dialogue
    scene_counts = {}
    try:
        from database.models import Scene
        scenes = db.query(Scene).all()
        for s in scenes:
            try:
                p_ids = json.loads(s.participant_ids_json or "[]")
                for cid in p_ids:
                    scene_counts[cid] = scene_counts.get(cid, 0) + 1
            except Exception:
                pass
    except Exception:
        # Scene table doesn't exist yet — count from Dialogue instead
        try:
            from database.models import Dialogue
            dialogues = db.query(Dialogue).all()
            for d in dialogues:
                try:
                    p_ids = json.loads(d.participant_ids_json or "[]")
                    for cid in p_ids:
                        scene_counts[cid] = scene_counts.get(cid, 0) + 1
                except Exception:
                    pass
        except Exception:
            pass

    result = []
    for c in all_chars:
        try:
            rels = (
                db.query(CharacterRelationship)
                .filter(CharacterRelationship.from_character_id == c.id)
                .all()
            )
            rel_count = len(rels)
            max_fam = max((r.familiarity for r in rels), default=0.0)
        except Exception:
            rel_count = 0
            max_fam = 0.0

        result.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "age": c.age,
            "core_drive": c.core_drive,
            "alive": c.alive,
            "is_minor": c.is_minor,
            "scenes": scene_counts.get(c.id, 0),
            "relationships": rel_count,
            "max_familiarity": round(max_fam, 2),
        })
    return result


@router.get("/api/replay/{sim_day}")
def get_day_replay_v2(sim_day: int, db: Session = Depends(get_db)):
    """Returns all scenes AND dialogues for a given simulated day."""
    from database.models import Scene, Dialogue

    result = []

    # Try scenes table first (new architecture)
    try:
        scenes = (
            db.query(Scene)
            .filter(Scene.sim_day == sim_day)
            .order_by(Scene.id.asc())
            .all()
        )
        for s in scenes:
            loc = db.query(Location).filter(Location.id == s.location_id).first()
            p_ids = json.loads(s.participant_ids_json or "[]")
            chars = [db.query(Character).filter(Character.id == cid).first() for cid in p_ids]
            exchanges = json.loads(s.dialogue_json or "[]")
            result.append({
                "source": "scene",
                "scene_type": s.scene_type,
                "pressure_type": s.pressure_type,
                "location": loc.name if loc else "Unknown",
                "participants": [
                    {"roster_id": c.roster_id, "given_name": c.given_name, "gender": c.gender}
                    for c in chars if c
                ],
                "exchanges": exchanges,
            })
    except Exception:
        pass

    # Fall back to dialogues table (old architecture / action events)
    if not result:
        dialogues = (
            db.query(Dialogue)
            .filter(Dialogue.sim_day == sim_day)
            .order_by(Dialogue.id.asc())
            .all()
        )
        for d in dialogues:
            loc = db.query(Location).filter(Location.id == d.location_id).first()
            p_ids = d.participants
            chars = [db.query(Character).filter(Character.id == cid).first() for cid in p_ids]
            result.append({
                "source": "dialogue",
                "scene_type": None,
                "pressure_type": None,
                "location": loc.name if loc else "Unknown",
                "topic": d.topic,
                "participants": [
                    {"roster_id": c.roster_id, "given_name": c.given_name, "gender": c.gender}
                    for c in chars if c
                ],
                "exchanges": d.dialogue,
            })

    return result


@router.get("/api/scene_categories")
def get_scene_categories(db: Session = Depends(get_db)):
    """Scene content category counts and breakdown over time."""
    from simulation.scene_categorizer import CATEGORY_LABELS, CATEGORY_COLORS, ALL_CATEGORIES

    try:
        from database.models import Scene
        scenes = db.query(Scene).order_by(Scene.sim_day.asc()).all()
    except Exception:
        return {"totals": {}, "by_day": [], "labels": CATEGORY_LABELS, "colors": CATEGORY_COLORS}

    # Total counts per category
    totals: dict[str, int] = {c: 0 for c in ALL_CATEGORIES}
    by_day: dict[int, dict[str, int]] = {}

    for s in scenes:
        cat = s.content_category or "community"
        totals[cat] = totals.get(cat, 0) + 1

        day = s.sim_day
        if day not in by_day:
            by_day[day] = {c: 0 for c in ALL_CATEGORIES}
        by_day[day][cat] = by_day[day].get(cat, 0) + 1

    # Sort by_day into a list
    by_day_list = [
        {"day": day, **counts}
        for day, counts in sorted(by_day.items())
    ]

    # Per-character category breakdown
    char_breakdown = []
    for s in scenes:
        cat = s.content_category or "community"
        p_ids = json.loads(s.participant_ids_json or "[]")
        for cid in p_ids:
            char_breakdown.append({"character_id": cid, "category": cat})

    char_category_counts: dict[int, dict[str, int]] = {}
    for row in char_breakdown:
        cid = row["character_id"]
        cat = row["category"]
        if cid not in char_category_counts:
            char_category_counts[cid] = {}
        char_category_counts[cid][cat] = char_category_counts[cid].get(cat, 0) + 1

    char_summaries = []
    for c in db.query(Character).filter(Character.alive == True).all():
        counts = char_category_counts.get(c.id, {})
        top_category = max(counts, key=counts.get) if counts else None
        char_summaries.append({
            "roster_id": c.roster_id,
            "given_name": c.given_name,
            "gender": c.gender,
            "core_drive": c.core_drive,
            "category_counts": counts,
            "top_category": top_category,
            "total_scenes": sum(counts.values()),
        })
    char_summaries.sort(key=lambda x: x["total_scenes"], reverse=True)

    return {
        "totals": totals,
        "by_day": by_day_list,
        "labels": CATEGORY_LABELS,
        "colors": CATEGORY_COLORS,
        "character_breakdown": char_summaries,
    }


@router.get("/api/open_questions")
def get_open_questions(db: Session = Depends(get_db)):
    """All active open questions across the community."""
    try:
        from database.models import OpenQuestion
        from datetime import datetime as dt

        # Clean up any zero-intensity questions that weren't dropped by decay
        zero = db.query(OpenQuestion).filter(
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
            OpenQuestion.intensity <= 0.0,
        ).all()
        for q in zero:
            q.dropped = True
            q.resolved = True
            q.resolution_text = "Faded — intensity reached zero"
        if zero:
            db.commit()

        questions = (
            db.query(OpenQuestion)
            .filter(
                OpenQuestion.resolved == False,
                OpenQuestion.dropped == False,
                OpenQuestion.intensity > 0.0,
            )
            .order_by(OpenQuestion.intensity.desc())
            .all()
        )
        result = []
        for q in questions:
            char = db.query(Character).filter(Character.id == q.character_id).first()
            result.append({
                "id": q.id,
                "character": char.given_name or char.roster_id if char else "?",
                "roster_id": char.roster_id if char else "?",
                "gender": char.gender if char else "?",
                "core_drive": char.core_drive if char else "?",
                "question": q.question_text,
                "intensity": round(q.intensity, 2),
                "emerged_day": q.emerged_day,
                "times_surfaced": q.times_surfaced,
                "source_type": q.source_type,
                "attempts": getattr(q, "attempts", 0),
                "understanding": q.current_understanding or "",
            })
        return result
    except Exception as e:
        return []


# ── Reader summaries ──────────────────────────────────────────────────────────

def _format_summary(s) -> dict:
    return {
        "sim_day": s.sim_day,
        "daybook": s.daybook,
        "active_threads": json.loads(s.active_threads_json or "[]"),
        "shifting_roles": json.loads(s.shifting_roles_json or "[]"),
        "consequences": json.loads(s.consequences_json or "[]"),
        "place_updates": json.loads(s.place_updates_json or "[]"),
        "character_arcs": json.loads(s.character_arcs_json or "[]"),
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/api/summary/today")
def get_summary_today(db: Session = Depends(get_db)):
    """Return today's ReaderSummary."""
    from database.models import ReaderSummary
    summary = (
        db.query(ReaderSummary)
        .order_by(ReaderSummary.sim_day.desc())
        .first()
    )
    if not summary:
        raise HTTPException(status_code=404, detail="No summary available yet")
    return _format_summary(summary)


@router.get("/api/summary/{sim_day}")
def get_summary_by_day(sim_day: int, db: Session = Depends(get_db)):
    """Return the ReaderSummary for a specific sim day."""
    from database.models import ReaderSummary
    summary = (
        db.query(ReaderSummary)
        .filter(ReaderSummary.sim_day == sim_day)
        .first()
    )
    if not summary:
        raise HTTPException(status_code=404, detail=f"No summary for day {sim_day}")
    return _format_summary(summary)
