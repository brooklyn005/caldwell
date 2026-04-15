"""
satisfaction_scorer.py — scores conversation outcomes per character drive.
Uses call_scoring_model which routes to local Ollama in local/mixed mode.
"""
import logging
from sqlalchemy.orm import Session
from database.models import Character, SatisfactionLog
from simulation.ai_caller import call_scoring_model
from simulation.cost_tracker import CostTracker

logger = logging.getLogger("caldwell.scorer")

# Drive scoring criteria now loaded from drives.py
from simulation.drives import get_drive_satisfaction_criteria, get_all_drive_names

def _get_scoring_criteria(drive: str) -> str:
    return get_drive_satisfaction_criteria(drive)


async def score_conversation(
    character: Character,
    transcript: str,
    cost_tracker: CostTracker,
) -> float:
    criteria = _get_scoring_criteria(character.core_drive)
    char_id = character.given_name or character.roster_id

    prompt = (
        f"Transcript:\n{transcript}\n\n"
        f"Score how well this served {char_id}'s need: {criteria}\n"
        f"Reply with ONLY a decimal -1.0 to 1.0. Nothing else."
    )
    text, in_tok, out_tok = await call_scoring_model(
        system_prompt="Score conversations. Reply with only a decimal number between -1.0 and 1.0.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=8,
    )
    cost_tracker.record("haiku", in_tok, out_tok)
    try:
        score = float(text.strip().split()[0].replace(",", "."))
        return round(max(-1.0, min(1.0, score)), 2)
    except (ValueError, TypeError, IndexError):
        return 0.0


async def score_and_record(
    char_a: Character,
    char_b: Character,
    exchanges: list[dict],
    sim_day: int,
    db: Session,
    cost_tracker: CostTracker,
):
    if not exchanges:
        return
    transcript = "\n".join(
        f"{ex.get('given_name') or ex.get('roster_id','?')}: {ex['text']}"
        for ex in exchanges
    )
    for char in [char_a, char_b]:
        score = await score_conversation(char, transcript, cost_tracker)
        db.add(SatisfactionLog(
            character_id=char.id, sim_day=sim_day,
            score=score, drive=char.core_drive,
        ))
        logger.info(f"  Satisfaction {char.roster_id} ({char.core_drive}): {score:+.2f}")
    db.commit()
