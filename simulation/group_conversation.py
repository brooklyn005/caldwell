"""
group_conversation.py — runs a conversation between 3-4 characters.

Group dynamics are fundamentally different from pairs:
- A dominant personality can emerge in real time
- Side conversations form and break off
- Quieter characters get drowned out or find unexpected moments
- Alliances and tensions become visible to everyone present

Structure: characters take turns in a round-robin with occasional
"interruptions" where a character jumps in out of order.
"""
import json
import logging
import random
from typing import Callable

from sqlalchemy.orm import Session

from database.models import Character, Location, Dialogue
from simulation.ai_caller import call_ai
from simulation.cost_tracker import CostTracker
from simulation.prompt_builder import (
    build_system_prompt,
    get_pending_inception,
)
from simulation.memory_writer import (
    extract_and_write_memories,
    update_relationship,
    detect_names,
    mark_inceptions_delivered,
)
from simulation.satisfaction_scorer import score_and_record
from simulation.disposition_tracker import update_disposition_record

logger = logging.getLogger("caldwell.group")

GROUP_EXCHANGES_BASE = 12   # trio gets 12 turns total
GROUP_EXCHANGES_QUAD = 10  # quad gets 10 turns total


async def run_group_conversation(
    characters: list[Character],  # 3 or 4 characters
    location: Location,
    sim_day: int,
    sim_tick: int,
    cost_tracker: CostTracker,
    db: Session,
    broadcast_fn: Callable | None = None,
    scene_context: str | None = None,
    dramatic_purpose: str | None = None,
) -> list[dict]:
    if len(characters) < 3:
        return []

    n_exchanges = GROUP_EXCHANGES_QUAD if len(characters) >= 4 else GROUP_EXCHANGES_BASE

    exchanges = []

    # Build a system prompt for each character that mentions ALL others
    def describe_others(char: Character) -> str:
        others = [c for c in characters if c.id != char.id]
        parts = []
        for o in others:
            name = o.given_name or o.roster_id
            desc = o.physical_description[:60]
            parts.append(f"{name} ({desc})")
        return "; ".join(parts)

    system_prompts = {}
    for char in characters:
        inception = get_pending_inception(char, sim_day, db)
        others = [c for c in characters if c.id != char.id]
        base = build_system_prompt(
            char, others[0], location, db, sim_day, inception,
            nearby_characters=others,
            scene_context=scene_context,
            dramatic_purpose=dramatic_purpose,
        )
        group_note = (
            f"\nYou are in a group. Others here: {describe_others(char)}. "
            f"Speak to whoever feels right. React to what you're hearing. "
            f"You don't have to respond to the last speaker — you can address anyone."
        )
        system_prompts[char.id] = base + group_note

    if broadcast_fn:
        await broadcast_fn({
            "type": "conversation_start",
            "data": {
                "group": [c.roster_id for c in characters],
                "group_names": [c.given_name or c.roster_id for c in characters],
                "location": location.name,
                "sim_day": sim_day,
                "is_group": True,
                "scene": bool(scene_context),
            },
        })

    # Build a shared running transcript
    shared_history: list[dict] = []

    # Round-robin with occasional random reorder (simulates interruption)
    speaker_order = list(characters)
    random.shuffle(speaker_order)

    for turn in range(n_exchanges):
        # Occasionally shuffle to simulate someone jumping in
        if turn > 0 and random.random() < 0.25:
            random.shuffle(speaker_order)

        speaker = speaker_order[turn % len(speaker_order)]
        sys_prompt = system_prompts[speaker.id]
        # Build message list from shared history
        if not shared_history:
            # First speaker opens
            messages = [{
                "role": "user",
                "content": (
                    f"You are at {location.name} with {describe_others(speaker)}. "
                    f"The moment feels like something should be said. What do you say or do?"
                ),
            }]
        else:
            # Build context from last 4 exchanges
            recent = shared_history[-4:]
            context_lines = "\n".join(
                f"{ex['given_name'] or ex['roster_id']}: {ex['text']}"
                for ex in recent
            )
            messages = [{
                "role": "user",
                "content": (
                    f"The conversation so far:\n{context_lines}\n\n"
                    f"Speak as yourself — first person only, present tense. "
                    f"React to what was just said. Do NOT describe what others are doing or feeling. "
                    f"Do NOT narrate the scene. Speak your own words directly. 3-5 sentences."
                ),
            }]

        text, in_tok, out_tok = await call_ai(speaker.ai_model, sys_prompt, messages, max_tokens=700)
        cost_tracker.record(speaker.ai_model, in_tok, out_tok)

        entry = {
            "roster_id": speaker.roster_id,
            "given_name": speaker.given_name,
            "text": text,
            "model": speaker.ai_model,
        }
        exchanges.append(entry)
        shared_history.append(entry)

        if broadcast_fn:
            await broadcast_fn({
                "type": "dialogue_line",
                "data": {
                    "roster_id": speaker.roster_id,
                    "given_name": speaker.given_name,
                    "text": text,
                    "model": speaker.ai_model,
                    "location": location.name,
                    "sim_day": sim_day,
                    "is_group": True,
                },
            })

    # Post-conversation processing for all participants
    detect_names(exchanges, characters[0], characters[1], db)

    # Write memories for each character
    for i, char in enumerate(characters):
        other = characters[(i + 1) % len(characters)]
        await extract_and_write_memories(char, other, exchanges, sim_day, db, cost_tracker)
        mark_inceptions_delivered(char, sim_day, db)

    # Open questions — group scenes are especially rich for generating them
    try:
        from simulation.open_question import extract_open_questions, check_resolution
        for i, char in enumerate(characters):
            other = characters[(i + 1) % len(characters)]
            await extract_open_questions(char, other, exchanges, sim_day, db, cost_tracker)
            await check_resolution(char, exchanges, sim_day, db, cost_tracker)
    except Exception as e:
        pass  # non-fatal

    # Update relationships (all pairs in the group)
    for i, char in enumerate(characters):
        for other in characters[i+1:]:
            update_relationship(char, other, db)

    # Score satisfaction for each participant
    for i in range(0, len(characters) - 1, 2):
        await score_and_record(
            characters[i], characters[i+1], exchanges, sim_day, db, cost_tracker
        )
    for char in characters:
        update_disposition_record(char, sim_day, db)

    # Save dialogue
    db.add(Dialogue(
        sim_day=sim_day,
        sim_tick=sim_tick,
        location_id=location.id,
        participant_ids_json=json.dumps([c.id for c in characters]),
        dialogue_json=json.dumps(exchanges),
        topic="group",
    ))
    db.commit()

    if broadcast_fn:
        await broadcast_fn({
            "type": "conversation_end",
            "data": {
                "group": [c.roster_id for c in characters],
                "location": location.name,
                "sim_day": sim_day,
                "exchange_count": len(exchanges),
            },
        })

    logger.info(
        f"Day {sim_day}: Group [{', '.join(c.roster_id for c in characters)}] "
        f"@ {location.name} — {len(exchanges)} turns"
    )
    return exchanges
