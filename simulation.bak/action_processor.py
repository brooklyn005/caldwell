"""
action_processor.py — processes operator-injected action events.

Action events are forced scenes that actually happen in Caldwell.
Unlike inception (private thought), action events:
1. Are experienced by multiple characters
2. Generate immediate reactions from all participants
3. Force a follow-up conversation between primary participants that same tick
4. Are saved to the Dialogue table so they appear in the timeline scrubber
5. Run BEFORE conversation selection so participants can be paired immediately
"""
import json
import logging
from sqlalchemy.orm import Session
from database.models import (
    Character, ActionEvent, Memory, Location, Dialogue, SignificantEvent
)
from simulation.ai_caller import call_ai
from simulation.cost_tracker import CostTracker

logger = logging.getLogger("caldwell.action")


def _strip_roster_ids(text: str, db: Session) -> str:
    """Replace all roster IDs in text with names or physical descriptions."""
    chars = db.query(Character).all()
    for c in chars:
        display = c.given_name if c.given_name else c.physical_description[:45]
        text = text.replace(c.roster_id, display)
    return text


def _write_memory(
    character: Character,
    content: str,
    sim_day: int,
    db: Session,
    emotional_weight: float = 0.8,
):
    mem = Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type="observation",
        content=content,
        emotional_weight=emotional_weight,
        is_inception=False,
    )
    db.add(mem)
    db.commit()


async def process_action_events(
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
    broadcast_fn=None,
) -> list[dict]:
    """
    Find and process all action events due on this day.
    Returns list of processed results including forced conversation pairs.
    """
    pending = (
        db.query(ActionEvent)
        .filter(
            ActionEvent.inject_on_day == sim_day,
            ActionEvent.processed == False,
        )
        .all()
    )

    if not pending:
        return []

    results = []
    for event in pending:
        result = await _process_single_event(
            event, sim_day, db, cost_tracker, broadcast_fn
        )
        results.append(result)
        event.processed = True
        event.processed_day = sim_day
        db.commit()

    return results


async def _process_single_event(
    event: ActionEvent,
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
    broadcast_fn,
) -> dict:
    """
    Process one action event:
    1. Generate reactions from all participants
    2. Save reactions to Dialogue table (appears in timeline scrubber)
    3. Write memories for all involved characters
    4. Return forced conversation pairs for same-day follow-up
    """
    # Resolve characters
    all_roster_ids = event.participant_ids + event.witness_ids
    characters = {}
    for rid in all_roster_ids:
        c = db.query(Character).filter(Character.roster_id == rid).first()
        if c:
            characters[rid] = c

    if not characters:
        logger.warning(f"Action event {event.id}: no valid characters found")
        return {"event_id": event.id, "status": "no_characters", "forced_pairs": []}

    # Clean scene description — strip all roster IDs
    clean_scene = _strip_roster_ids(event.scene_description, db)

    logger.info(
        f"Day {sim_day}: Processing action event — {clean_scene[:400]}..."
    )

    if broadcast_fn:
        await broadcast_fn({
            "type": "action_event",
            "data": {
                "sim_day": sim_day,
                "scene": clean_scene,
                "participants": event.participant_ids,
                "witnesses": event.witness_ids,
            },
        })

    # ── Generate reactions from each character ────────────────────────────────
    reactions = {}
    exchanges = []

    # Add scene as first "exchange" so scrubber shows it
    exchanges.append({
        "roster_id": "OPERATOR",
        "given_name": "Scene",
        "text": clean_scene,
        "model": "operator",
    })

    for roster_id, char in characters.items():
        is_witness = roster_id in event.witness_ids
        is_subject = (
            roster_id == event.participant_ids[-1]
            if event.perspective == "subject" and len(event.participant_ids) > 1
            else False
        )

        # Build perspective-appropriate framing
        if is_witness:
            framing = f"You just witnessed from nearby: {clean_scene}"
        elif is_subject:
            framing = f"This just happened to you: {clean_scene}"
        elif event.perspective == "observer" and roster_id == event.participant_ids[0]:
            framing = f"You just witnessed this: {clean_scene}"
        else:
            framing = f"This just happened: {clean_scene}"

        identity = (
            char.given_name if char.given_name
            else char.physical_description[:45]
        )

        sys_prompt = (
            f"You are {identity}. Age {char.age}.\n"
            f"Your nature: {char.natural_tendency}.\n"
            f"Your core drive: {char.core_drive}.\n\n"
            f"{framing}\n\n"
            f"Write 2-3 sentences as yourself. First person only, present tense.\n"
            f"What do you feel RIGHT NOW in your body and mind.\n"
            f"NEVER use codes like F-01 or M-07. NEVER third person.\n"
            f"You are IN this moment, not watching it. Raw and direct."
        )

        messages = [{"role": "user", "content": "Speak as yourself right now."}]
        reaction_text, in_tok, out_tok = await call_ai(
            char.ai_model, sys_prompt, messages, max_tokens=120
        )
        cost_tracker.record(char.ai_model, in_tok, out_tok)
        reactions[roster_id] = reaction_text.strip()

        exchanges.append({
            "roster_id": char.roster_id,
            "given_name": char.given_name,
            "gender": char.gender,
            "text": reaction_text.strip(),
            "model": char.ai_model,
            "is_witness": is_witness,
        })

        logger.info(f"  {roster_id} reaction: {reaction_text[:60]}")

        if broadcast_fn:
            await broadcast_fn({
                "type": "action_reaction",
                "data": {
                    "roster_id": char.roster_id,
                    "given_name": char.given_name,
                    "gender": char.gender,
                    "reaction": reaction_text,
                    "sim_day": sim_day,
                },
            })

    # ── Save to Dialogue table so timeline scrubber shows it ─────────────────
    # Find a location for the primary participant
    primary_char = characters.get(event.participant_ids[0]) if event.participant_ids else None
    loc_id = primary_char.current_location_id if primary_char else None
    if not loc_id:
        default_loc = db.query(Location).filter(
            Location.name == "Central Square"
        ).first()
        loc_id = default_loc.id if default_loc else None

    db.add(Dialogue(
        sim_day=sim_day,
        sim_tick=sim_day,
        location_id=loc_id,
        participant_ids_json=json.dumps([c.id for c in characters.values()]),
        dialogue_json=json.dumps(exchanges),
        topic=f"[ACTION INJECT] {clean_scene[:400]}",
    ))
    db.commit()

    # ── Write memories for all involved characters ────────────────────────────
    for roster_id, char in characters.items():
        is_witness = roster_id in event.witness_ids
        reaction = reactions.get(roster_id, "")

        others = [
            c for rid, c in characters.items() if rid != roster_id
        ]
        other_names = ", ".join(
            c.given_name or c.physical_description[:30]
            for c in others
        )

        memory_content = (
            f"[Shared moment] {clean_scene[:400]} "
            f"{'(with ' + other_names + ')' if other_names else ''}. "
            f"{reaction}"
        ).strip()

        _write_memory(
            char, memory_content, sim_day, db,
            emotional_weight=0.9 if not is_witness else 0.7,
        )

    # Cross-memories between primary participants
    participant_chars = [
        characters[rid] for rid in event.participant_ids
        if rid in characters
    ]
    if len(participant_chars) >= 2:
        for i, char_a in enumerate(participant_chars):
            for char_b in participant_chars[i+1:]:
                reaction_b = reactions.get(char_b.roster_id, "")
                name_b = char_b.given_name or char_b.physical_description[:30]
                if reaction_b:
                    _write_memory(
                        char_a,
                        f"[Shared moment] {name_b} — {reaction_b[:80]}",
                        sim_day, db, 0.75
                    )
                reaction_a = reactions.get(char_a.roster_id, "")
                name_a = char_a.given_name or char_a.physical_description[:30]
                if reaction_a:
                    _write_memory(
                        char_b,
                        f"[Shared moment] {name_a} — {reaction_a[:80]}",
                        sim_day, db, 0.75
                    )

    # ── Log to event journal ──────────────────────────────────────────────────
    char_ids = [c.id for c in characters.values()]
    char_ids_json = json.dumps(sorted(char_ids))
    existing = db.query(SignificantEvent).filter(
        SignificantEvent.event_type == "action_inject",
        SignificantEvent.character_ids_json == char_ids_json,
        SignificantEvent.sim_day == sim_day,
    ).first()
    if not existing:
        db.add(SignificantEvent(
            sim_day=sim_day,
            event_type="action_inject",
            description=f"[Operator scene] {clean_scene[:400]}",
            character_ids_json=char_ids_json,
            emotional_weight=0.95,
        ))
        db.commit()

    # ── Return forced conversation pairs ──────────────────────────────────────
    # Primary participants should talk to each other this same tick
    forced_pairs = []
    if len(participant_chars) >= 2:
        for i in range(0, len(participant_chars) - 1, 2):
            forced_pairs.append((
                participant_chars[i].roster_id,
                participant_chars[i+1].roster_id,
            ))

    # Witnesses get paired with a primary participant if possible
    witness_chars = [
        characters[rid] for rid in event.witness_ids
        if rid in characters
    ]
    for witness in witness_chars:
        if participant_chars:
            forced_pairs.append((
                witness.roster_id,
                participant_chars[0].roster_id,
            ))

    return {
        "event_id": event.id,
        "status": "processed",
        "scene": clean_scene[:400],
        "characters": list(characters.keys()),
        "reactions": reactions,
        "forced_pairs": forced_pairs,
    }
