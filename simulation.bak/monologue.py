"""
monologue.py — generates internal thoughts for characters who
didn't have a conversation this tick.

Cheap call (~60 tokens), runs locally in local/mixed mode.
Makes idle characters feel continuous rather than frozen.
"""
import logging
from sqlalchemy.orm import Session
from database.models import Character, Location, Memory
from simulation.ai_caller import call_scoring_model
from simulation.prompt_builder import get_recent_memories
from simulation.disposition_tracker import get_disposition_modifier

logger = logging.getLogger("caldwell.monologue")


async def generate_monologue(
    character: Character,
    sim_day: int,
    db: Session,
) -> str | None:
    """Generate a brief internal thought for an idle character."""
    memories = get_recent_memories(character, db, limit=4)
    memory_block = "\n".join(f"- {m}" for m in memories) if memories else "- Nothing much yet."
    disposition_mod = get_disposition_modifier(character, sim_day, db) or ""

    loc = db.query(Location).filter(Location.id == character.current_location_id).first()
    loc_name = loc.name if loc else "somewhere in the city"

    # Build body/desire context for monologue
    body_context = ""
    if not character.is_minor:
        try:
            from database.models import CharacterBiology, PhysicalAttraction
            bio = db.query(CharacterBiology).filter(
                CharacterBiology.character_id == character.id
            ).first()
            if bio:
                if bio.hormonal_state == "aroused":
                    body_context = (
                        "Your body is wanting something you don't have words for. "
                        "A physical aliveness. A pull toward someone or something. "
                        "This is real and you are alone with it right now."
                    )
                elif bio.hormonal_state == "heightened":
                    body_context = (
                        "Everything is louder than usual — other people's bodies, "
                        "your own skin, what it means to be near someone."
                    )
                # Check if thinking about a specific person
                top_attr = db.query(PhysicalAttraction).filter(
                    PhysicalAttraction.from_character_id == character.id,
                    PhysicalAttraction.attraction_level >= 0.55,
                ).order_by(PhysicalAttraction.attraction_level.desc()).first()
                if top_attr and bio.hormonal_state in ("aroused", "heightened", "restless"):
                    target = db.query(Character).filter(
                        Character.id == top_attr.to_character_id
                    ).first()
                    if target:
                        tname = target.given_name or target.physical_description[:35]
                        body_context = (
                            f"You keep thinking about {tname}. "
                            f"Not just thinking — your body is involved in it. "
                            f"You don't know what to call this or what to do with it."
                        )
        except Exception:
            pass

    prompt = (
        f"You are a person at {loc_name}. Age {character.age}. "
        f"Your nature: {character.natural_tendency}\n"
        f"Recent memories:\n{memory_block}\n"
        f"{disposition_mod}\n"
        f"{body_context}\n\n"
        f"Write ONE sentence — an internal thought you are having right now. "
        f"Private, unspoken. First person. Direct. Under 25 words. "
        f"No cultural references. No institutions. Just a raw feeling, desire, "
        f"observation, or question. It can be about your body, about another person, "
        f"about what you want."
    )

    text, _, _ = await call_scoring_model(
        system_prompt="Write brief internal thoughts for characters. One sentence, first person, under 20 words.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=40,
    )

    thought = text.strip().strip('"').strip("'")
    if thought and thought != "...":
        db.add(Memory(
            character_id=character.id,
            sim_day=sim_day,
            memory_type="feeling",
            content=thought,
            emotional_weight=0.3,
            is_inception=False,
        ))
        db.commit()
        logger.debug(f"  Monologue {character.roster_id}: {thought}")
        return thought
    return None
