"""
conversation_runner.py — runs a full conversation between two characters.

Key changes:
- 9 exchanges per conversation (was 4-5)
- Topic seeds give characters something real to talk about
- Social learning records approaches and outcomes
- Longer responses (max_tokens 250)
- Depth encouragement builds through the conversation
"""
import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from database.models import Character, Location, Dialogue
from simulation.ai_caller import call_ai
from simulation.cost_tracker import CostTracker
from simulation.prompt_builder import build_system_prompt, response_message
from simulation.topic_seeds import generate_topic_seed, build_opening_message
from simulation.memory_writer import (
    extract_and_write_memories, update_relationship,
    detect_names, mark_inceptions_delivered,
)
from simulation.satisfaction_scorer import score_and_record
from simulation.disposition_tracker import update_disposition_record
from simulation.social_learning import (
    record_conversation_learning, record_social_observations,
)
from simulation.prompt_builder import get_pending_inception
from simulation.scene_builder import build_scene_from_memories

logger = logging.getLogger("caldwell.conversation")

EXCHANGES_PER_CONVERSATION = 9  # was 4-5
MAX_TOKENS_PER_TURN = 450


async def run_conversation(
    char_a: Character,
    char_b: Character,
    location: Location,
    sim_day: int,
    sim_tick: int,
    cost_tracker: CostTracker,
    db: Session,
    broadcast_fn: Callable | None = None,
    scene_context: str | None = None,
) -> list[dict]:

    exchanges = []

    # Inception check
    inception_a = get_pending_inception(char_a, sim_day, db)
    inception_b = get_pending_inception(char_b, sim_day, db)

    # Build system prompts with biology nearby context
    nearby = [char_b]
    sys_a = build_system_prompt(char_a, char_b, location, db, sim_day, inception_a, nearby)
    sys_b = build_system_prompt(char_b, char_a, location, db, sim_day, inception_b, [char_a])

    # Topic seeds — give each character something real on their mind
    topic_a = generate_topic_seed(char_a, db)

    # Scene context — if provided, override topic with activity-grounded seed
    # Also try to build one from today's action memories
    if not scene_context:
        scene_context, activity_topic = build_scene_from_memories(char_a, char_b, sim_day, db)
        if activity_topic:
            topic_a = activity_topic

    if broadcast_fn:
        await broadcast_fn({
            "type": "conversation_start",
            "data": {
                "char_a": char_a.roster_id,
                "char_b": char_b.roster_id,
                "char_a_name": char_a.given_name,
                "char_b_name": char_b.given_name,
                "location": location.name,
                "sim_day": sim_day,
            },
        })

    history_a: list[dict] = []
    history_b: list[dict] = []

    # ── A opens with topic seed ────────────────────────────────────────────────
    # If we have a scene context, prepend it to the topic seed
    if scene_context:
        topic_a = (
            f"WHAT IS HAPPENING RIGHT NOW: {scene_context}\n\n"
            f"{topic_a}"
        )
    init_msgs = build_opening_message(char_a, char_b, location.name, topic_a, db)
    a_text, in_a, out_a = await call_ai(
        char_a.ai_model, sys_a, init_msgs, max_tokens=MAX_TOKENS_PER_TURN
    )
    cost_tracker.record(char_a.ai_model, in_a, out_a)

    exchanges.append({
        "roster_id": char_a.roster_id,
        "given_name": char_a.given_name,
        "text": a_text,
        "model": char_a.ai_model,
    })

    history_a = init_msgs + [{"role": "assistant", "content": a_text}]
    history_b = response_message([], a_text, char_a.given_name or char_a.roster_id, 1)

    if broadcast_fn:
        await broadcast_fn({
            "type": "dialogue_line",
            "data": {
                "roster_id": char_a.roster_id,
                "given_name": char_a.given_name,
                "text": a_text,
                "model": char_a.ai_model,
                "location": location.name,
                "sim_day": sim_day,
            },
        })

    # ── Alternating exchanges ──────────────────────────────────────────────────
    for i in range(EXCHANGES_PER_CONVERSATION - 1):
        exchange_num = i + 2

        if i % 2 == 0:
            # B responds
            b_text, in_b, out_b = await call_ai(
                char_b.ai_model, sys_b, history_b, max_tokens=MAX_TOKENS_PER_TURN
            )
            cost_tracker.record(char_b.ai_model, in_b, out_b)
            exchanges.append({
                "roster_id": char_b.roster_id,
                "given_name": char_b.given_name,
                "text": b_text,
                "model": char_b.ai_model,
            })
            history_b.append({"role": "assistant", "content": b_text})
            history_a = response_message(
                history_a, b_text,
                char_b.given_name or char_b.roster_id,
                exchange_num
            )
            if broadcast_fn:
                await broadcast_fn({
                    "type": "dialogue_line",
                    "data": {
                        "roster_id": char_b.roster_id,
                        "given_name": char_b.given_name,
                        "text": b_text,
                        "model": char_b.ai_model,
                        "location": location.name,
                        "sim_day": sim_day,
                    },
                })
        else:
            # A responds
            a_text2, in_a2, out_a2 = await call_ai(
                char_a.ai_model, sys_a, history_a, max_tokens=MAX_TOKENS_PER_TURN
            )
            cost_tracker.record(char_a.ai_model, in_a2, out_a2)
            exchanges.append({
                "roster_id": char_a.roster_id,
                "given_name": char_a.given_name,
                "text": a_text2,
                "model": char_a.ai_model,
            })
            history_a.append({"role": "assistant", "content": a_text2})
            history_b = response_message(
                history_b, a_text2,
                char_a.given_name or char_a.roster_id,
                exchange_num
            )
            if broadcast_fn:
                await broadcast_fn({
                    "type": "dialogue_line",
                    "data": {
                        "roster_id": char_a.roster_id,
                        "given_name": char_a.given_name,
                        "text": a_text2,
                        "model": char_a.ai_model,
                        "location": location.name,
                        "sim_day": sim_day,
                    },
                })

    # ── Post-conversation ──────────────────────────────────────────────────────

    detect_names(exchanges, char_a, char_b, db)
    detect_norms_from_conversation(exchanges, char_a, char_b, sim_day, db)
    detect_directives(exchanges, char_a, char_b, sim_day, db)
    await extract_and_write_memories(char_a, char_b, exchanges, sim_day, db, cost_tracker)
    mark_inceptions_delivered(char_a, sim_day, db)
    mark_inceptions_delivered(char_b, sim_day, db)

    # Score satisfaction
    await score_and_record(char_a, char_b, exchanges, sim_day, db, cost_tracker)

    # Update dispositions
    update_disposition_record(char_a, sim_day, db)
    update_disposition_record(char_b, sim_day, db)

    # Update relationships
    update_relationship(char_a, char_b, db)

    # Social learning — classify approaches and record evidence
    try:
        # Get the satisfaction scores just recorded
        from database.models import SatisfactionLog
        score_a_row = (
            db.query(SatisfactionLog)
            .filter(
                SatisfactionLog.character_id == char_a.id,
                SatisfactionLog.sim_day == sim_day,
            )
            .order_by(SatisfactionLog.id.desc()).first()
        )
        score_b_row = (
            db.query(SatisfactionLog)
            .filter(
                SatisfactionLog.character_id == char_b.id,
                SatisfactionLog.sim_day == sim_day,
            )
            .order_by(SatisfactionLog.id.desc()).first()
        )
        score_a = score_a_row.score if score_a_row else 0.0
        score_b = score_b_row.score if score_b_row else 0.0

        await record_conversation_learning(
            char_a, char_b, exchanges, score_a, score_b,
            location.name, sim_day, db
        )
    except Exception as e:
        logger.error(f"Social learning record failed: {e}")

    # Save dialogue
    db.add(Dialogue(
        sim_day=sim_day,
        sim_tick=sim_tick,
        location_id=location.id,
        participant_ids_json=json.dumps([char_a.id, char_b.id]),
        dialogue_json=json.dumps(exchanges),
    ))
    db.commit()

    if broadcast_fn:
        await broadcast_fn({
            "type": "conversation_end",
            "data": {
                "char_a": char_a.roster_id,
                "char_b": char_b.roster_id,
                "location": location.name,
                "sim_day": sim_day,
                "exchange_count": len(exchanges),
            },
        })

    logger.info(
        f"Day {sim_day}: {char_a.roster_id} ↔ {char_b.roster_id} "
        f"@ {location.name} — {len(exchanges)} exchanges"
    )
    return exchanges
