"""
backfill_open_questions.py — generate open questions from existing simulation data.

Processes:
  1. Existing Dialogue records — extracts questions for both participants
  2. High-weight Memory records — extracts questions from significant observations

Run once after upgrading to the open question system. Safe to run again
(won't create duplicates). Will cost a small amount via the scoring model.

Usage:
  caldwell
  python3 backfill_open_questions.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from database.db import SessionLocal, init_db
from database.models import Character, Dialogue, Memory, OpenQuestion, Scene

init_db()
db = SessionLocal()


class FakeCostTracker:
    """Minimal cost tracker for backfill — just accumulates."""
    def __init__(self):
        self.total = 0.0

    def record(self, model, in_tok, out_tok):
        # Rough estimate
        self.total += (in_tok * 0.00000014) + (out_tok * 0.00000028)


async def backfill():
    from simulation.open_question import (
        extract_open_questions,
        extract_question_from_memory,
    )

    cost = FakeCostTracker()
    processed_dialogues = 0
    processed_memories = 0

    import json

    # ── 1. Process Scene records (primary — new architecture) ────────────────
    print("Processing scenes (new architecture)...")
    try:
        scenes = db.query(Scene).order_by(Scene.sim_day.asc()).all()
    except Exception:
        scenes = []

    for s in scenes:
        p_ids = json.loads(s.participant_ids_json or "[]")
        if len(p_ids) < 2:
            continue
        char_a = db.query(Character).filter(Character.id == p_ids[0]).first()
        char_b = db.query(Character).filter(Character.id == p_ids[1]).first()
        if not char_a or not char_b:
            continue

        exchanges = json.loads(s.dialogue_json or "[]")
        real = [e for e in exchanges if e.get("roster_id") != "OPERATOR"]
        if len(real) < 4:
            continue

        q_a = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == char_a.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).count()
        q_b = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == char_b.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).count()

        if q_a < 5:
            await extract_open_questions(char_a, char_b, exchanges, s.sim_day, db, cost)
        if q_b < 5:
            await extract_open_questions(char_b, char_a, exchanges, s.sim_day, db, cost)

        processed_dialogues += 1
        if processed_dialogues % 10 == 0:
            print(f"  {processed_dialogues}/{len(scenes)} scenes (~${cost.total:.3f})")

    print(f"Scene processing complete: {processed_dialogues} processed")

    # ── 2. Process legacy Dialogue records (old architecture) ────────────────
    print("Processing legacy dialogues...")
    dialogues = db.query(Dialogue).order_by(Dialogue.sim_day.asc()).all()

    for d in dialogues:
        p_ids = d.participants
        if len(p_ids) < 2:
            continue
        char_a = db.query(Character).filter(Character.id == p_ids[0]).first()
        char_b = db.query(Character).filter(Character.id == p_ids[1]).first()
        if not char_a or not char_b:
            continue

        exchanges = d.dialogue
        real = [e for e in exchanges if e.get("roster_id") != "OPERATOR"]
        if len(real) < 4:
            continue

        q_a = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == char_a.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).count()
        q_b = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == char_b.id,
            OpenQuestion.resolved == False,
            OpenQuestion.dropped == False,
        ).count()

        if q_a < 5:
            await extract_open_questions(char_a, char_b, exchanges, d.sim_day, db, cost)
        if q_b < 5:
            await extract_open_questions(char_b, char_a, exchanges, d.sim_day, db, cost)

        processed_dialogues += 1

    # ── 2. Process high-weight standalone memories ────────────────────────────
    print("\nProcessing significant memories...")
    significant = (
        db.query(Memory)
        .filter(
            Memory.emotional_weight >= 0.75,
            Memory.memory_type.in_(["observation", "feeling"]),
            Memory.is_inception == False,
        )
        .order_by(Memory.sim_day.asc())
        .all()
    )

    for mem in significant:
        char = db.query(Character).filter(
            Character.id == mem.character_id,
            Character.alive == True,
        ).first()
        if not char:
            continue

        q_count = db.query(OpenQuestion).filter(
            OpenQuestion.character_id == char.id,
            OpenQuestion.resolved == False,
        ).count()
        if q_count >= 3:
            continue

        await extract_question_from_memory(
            char, mem.content, mem.sim_day, db, cost,
            source_type="observation"
        )
        processed_memories += 1

    print(f"Memory processing complete: {processed_memories} significant memories processed")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_questions = db.query(OpenQuestion).count()
    print(f"\nBackfill complete.")
    print(f"  Open questions created: {total_questions}")
    print(f"  Estimated cost: ~${cost.total:.4f}")

    # Show a sample
    sample = db.query(OpenQuestion).filter(
        OpenQuestion.resolved == False
    ).order_by(OpenQuestion.intensity.desc()).limit(10).all()

    print("\nTop active questions:")
    for q in sample:
        char = db.query(Character).filter(Character.id == q.character_id).first()
        name = char.given_name if char else "?"
        print(f"  [{name}] intensity={q.intensity:.2f} day={q.emerged_day}: {q.question_text[:80]}")

    db.close()


if __name__ == "__main__":
    asyncio.run(backfill())
