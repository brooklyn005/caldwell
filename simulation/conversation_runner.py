"""
conversation_runner.py — runs a full conversation between two characters.

Key changes:
- 9 exchanges per conversation (was 4-5); 16 for scene-injected conversations
- Scene context woven into system prompt, not just prepended to topic seed
- Scene conversations override normal pair slot and run as the primary event
- Topic seeds give characters something real to talk about
- Social learning records approaches and outcomes
- Longer responses (max_tokens 450)
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
from simulation.norm_detector import detect_norms_from_conversation

logger = logging.getLogger("caldwell.conversation")

EXCHANGES_PER_CONVERSATION = 9
EXCHANGES_PER_SCENE = 16
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
    scene_type: str | None = None,
    dramatic_purpose: str | None = None,
) -> list[dict]:

    exchanges = []

    # Inception check
    inception_a = get_pending_inception(char_a, sim_day, db)
    inception_b = get_pending_inception(char_b, sim_day, db)

    # If no scene was injected externally, try to build one from today's action memories
    if not scene_context:
        discovered_scene, activity_topic = build_scene_from_memories(char_a, char_b, sim_day, db)
        if discovered_scene:
            scene_context = discovered_scene

    # Build system prompts — scene_context is woven into the prompt itself via scene_block
    nearby = [char_b]
    sys_a = build_system_prompt(
        char_a, char_b, location, db, sim_day,
        inception_a, nearby, scene_context,
        dramatic_purpose=dramatic_purpose,
        scene_type=scene_type,
    )
    sys_b = build_system_prompt(
        char_b, char_a, location, db, sim_day,
        inception_b, [char_a], scene_context,
        dramatic_purpose=dramatic_purpose,
        scene_type=scene_type,
    )

    # Scene conversations are the event — run longer and open from inside the physical moment
    n_exchanges = EXCHANGES_PER_SCENE if scene_context else EXCHANGES_PER_CONVERSATION

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
                "scene": bool(scene_context),
            },
        })

    # ── Opening message ────────────────────────────────────────────────────────
    if scene_context:
        if scene_type == "quiet_intimacy":
            opening_content = (
                f"You are at {location.name} with "
                f"{char_b.given_name or 'the other person'}. "
                f"The two of you have ended up here without crisis driving it. "
                f"Begin from inside this moment — what you notice, what you feel, "
                f"what comes out of you naturally. At least 8 sentences."
            )
        else:
            opening_content = (
                f"You are at {location.name} with "
                f"{char_b.given_name or 'the other person'}. "
                f"The situation described is happening right now. "
                f"Begin from inside it — what you are doing, what you need to say, "
                f"what the moment demands of you. At least 8 sentences."
            )
        init_msgs = [{"role": "user", "content": opening_content}]
    else:
        topic_a = generate_topic_seed(char_a, db)
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
    for i in range(n_exchanges - 1):
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
                exchange_num,
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
                exchange_num,
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
    await extract_and_write_memories(char_a, char_b, exchanges, sim_day, db, cost_tracker)
    mark_inceptions_delivered(char_a, sim_day, db)
    mark_inceptions_delivered(char_b, sim_day, db)

    # Open questions — extract new ones, check resolution of existing ones
    try:
        from simulation.open_question import extract_open_questions, check_resolution
        await extract_open_questions(char_a, char_b, exchanges, sim_day, db, cost_tracker)
        await extract_open_questions(char_b, char_a, exchanges, sim_day, db, cost_tracker)
        await check_resolution(char_a, exchanges, sim_day, db, cost_tracker)
        await check_resolution(char_b, exchanges, sim_day, db, cost_tracker)
    except Exception as e:
        logger.debug(f"Open question processing failed: {e}")

    await score_and_record(char_a, char_b, exchanges, sim_day, db, cost_tracker)

    update_disposition_record(char_a, sim_day, db)
    update_disposition_record(char_b, sim_day, db)

    update_relationship(char_a, char_b, db)

    # Social learning — classify approaches and record evidence
    try:
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
            location.name, sim_day, db,
        )
    except Exception as e:
        logger.error(f"Social learning record failed: {e}")

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
                "scene": bool(scene_context),
            },
        })

    logger.info(
        f"Day {sim_day}: {char_a.roster_id} ↔ {char_b.roster_id} "
        f"@ {location.name} — {len(exchanges)} exchanges"
        + (" [SCENE]" if scene_context else "")
    )
    return exchanges
