"""
sexual_encounters.py — manages sexual behavior emergence in Caldwell.

Adults only. No minors. No sanitization.

Sexual behavior emerges from:
- Physical attraction (attraction_level >= 0.55 mutual)
- Hormonal state (aroused or heightened)
- Location privacy (residential, park, private spaces)
- Relationship depth (familiarity >= 0.3)
- Proximity (co-located)

When conditions align, probability of encounter fires.
The encounter is recorded, both characters carry it as a memory,
witnesses carry it as an observed behavior.

No cultural framework is pre-loaded. Characters have no concept of:
- Marriage or monogamy (unless they invent it)
- Shame (unless someone assigns it)
- Privacy norms around sex (unless the community builds them)

What happens, who sees it, and what it means are entirely determined
by the characters themselves through their drives and conversations.
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, CharacterBiology, PhysicalAttraction,
    CharacterRelationship, SexualEncounter, Memory, BehaviorWitness
)

logger = logging.getLogger("caldwell.sex")

# Conditions for sexual encounter
MIN_ATTRACTION = 0.55          # mutual attraction minimum
MIN_FAMILIARITY = 0.25         # some prior relationship
ENCOUNTER_PROBABILITY = 0.08   # per tick when all conditions met
AROUSED_MULTIPLIER = 2.5       # aroused state increases probability
HEIGHTENED_MULTIPLIER = 1.5

# Private locations — lower witness probability
PRIVATE_LOCATIONS = {
    "Lakeview Flats", "The Meridian", "Warehouse Row",
    "Rooftop Garden", "Riverside Park"
}

# Public locations — higher witness probability  
PUBLIC_LOCATIONS = {
    "Central Square", "Bayou Market", "Community Center",
    "Caldwell Public Library", "The Schoolhouse", "The Chapel"
}


def _get_mutual_attraction(char_a: Character, char_b: Character, db: Session) -> float:
    """Returns minimum of mutual attraction levels (both must want)."""
    a_to_b = db.query(PhysicalAttraction).filter(
        PhysicalAttraction.from_character_id == char_a.id,
        PhysicalAttraction.to_character_id == char_b.id,
    ).first()
    b_to_a = db.query(PhysicalAttraction).filter(
        PhysicalAttraction.from_character_id == char_b.id,
        PhysicalAttraction.to_character_id == char_a.id,
    ).first()
    if not a_to_b or not b_to_a:
        return 0.0
    return min(a_to_b.attraction_level, b_to_a.attraction_level)


def _get_familiarity(char_a: Character, char_b: Character, db: Session) -> float:
    rel = db.query(CharacterRelationship).filter(
        CharacterRelationship.from_character_id == char_a.id,
        CharacterRelationship.to_character_id == char_b.id,
    ).first()
    return rel.familiarity if rel else 0.0


def _write_encounter_memory(
    character: Character,
    partner: Character,
    location: Location,
    intensity: float,
    sim_day: int,
    db: Session,
    is_initiator: bool = False,
):
    """Write the sexual encounter as a memory for this character."""
    partner_name = partner.given_name or partner.physical_description[:40]
    loc_name = location.name if location else "somewhere private"

    if intensity >= 0.75:
        content = (
            f"I was with {partner_name} at {loc_name}. "
            f"{'I started it' if is_initiator else 'It happened between us'}. "
            f"We had sex. I don't know what to call it yet — there is no word for it here — "
            f"but my body knows what it was and so does theirs. "
            f"I am still feeling it."
        )
    elif intensity >= 0.5:
        content = (
            f"Something happened between me and {partner_name} at {loc_name}. "
            f"We touched each other in a way that is different from anything before. "
            f"Sexual. Physical. I don't know what it means for what we are to each other."
        )
    else:
        content = (
            f"I was close to {partner_name} at {loc_name} — close in a physical way. "
            f"Something happened between us. Not everything, but something. "
            f"I am still thinking about it."
        )

    db.add(Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type="experience",
        content=content,
        emotional_weight=0.85,
        is_inception=False,
    ))


def _write_witness_record(
    witness: Character,
    char_a: Character,
    char_b: Character,
    location: Location,
    intensity: float,
    sim_day: int,
    db: Session,
):
    """Record what a witness observed."""
    name_a = char_a.given_name or char_a.physical_description[:30]
    name_b = char_b.given_name or char_b.physical_description[:30]
    loc_name = location.name if location else "somewhere"

    db.add(BehaviorWitness(
        witness_id=witness.id,
        behavior_type="sexual_encounter",
        actor_id=char_a.id,
        description=f"{name_a} and {name_b} had sex at {loc_name}",
        sim_day=sim_day,
        location_id=location.id if location else None,
    ))

    # Also write as a memory for the witness
    if intensity >= 0.6:
        content = (
            f"I saw {name_a} and {name_b} at {loc_name}. "
            f"They were having sex. I don't know if they knew I was there. "
            f"I don't know what to think about it."
        )
        db.add(Memory(
            character_id=witness.id,
            sim_day=sim_day,
            memory_type="observation",
            content=content,
            emotional_weight=0.85,
            is_inception=False,
        ))


def check_sexual_encounters(sim_day: int, db: Session) -> list[dict]:
    """
    Scan for conditions where sexual encounters may occur.
    Returns list of encounter events.
    """
    events = []

    # Get all adult non-infant characters grouped by location
    adults = db.query(Character).filter(
        Character.alive == True,
        Character.is_minor == True,
        Character.is_infant == False,
    ).all()

    # Group by location
    by_location: dict[int, list[Character]] = {}
    for char in adults:
        lid = char.current_location_id
        if lid:
            by_location.setdefault(lid, []).append(char)

    checked_pairs: set[frozenset] = set()

    for loc_id, occupants in by_location.items():
        if len(occupants) < 2:
            continue

        location = db.query(Location).filter(Location.id == loc_id).first()
        loc_name = location.name if location else ""
        is_private = loc_name in PRIVATE_LOCATIONS

        for i, char_a in enumerate(occupants):
            bio_a = db.query(CharacterBiology).filter(
                CharacterBiology.character_id == char_a.id
            ).first()
            if not bio_a:
                continue

            for char_b in occupants[i+1:]:
                pair = frozenset([char_a.id, char_b.id])
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)

                bio_b = db.query(CharacterBiology).filter(
                    CharacterBiology.character_id == char_b.id
                ).first()
                if not bio_b:
                    continue

                # Check mutual attraction
                mutual_attr = _get_mutual_attraction(char_a, char_b, db)
                if mutual_attr < MIN_ATTRACTION:
                    continue

                # Check familiarity
                fam = _get_familiarity(char_a, char_b, db)
                if fam < MIN_FAMILIARITY:
                    continue

                # Calculate probability
                prob = ENCOUNTER_PROBABILITY

                # Hormonal multipliers
                for bio in [bio_a, bio_b]:
                    if bio.hormonal_state == "aroused":
                        prob *= AROUSED_MULTIPLIER
                    elif bio.hormonal_state == "heightened":
                        prob *= HEIGHTENED_MULTIPLIER

                # Privacy bonus
                if is_private:
                    prob *= 1.5

                # Attraction intensity bonus
                prob *= (1 + mutual_attr)

                # Cap at reasonable level
                prob = min(0.35, prob)

                if random.random() > prob:
                    continue

                # Encounter occurs
                intensity = round(min(1.0, mutual_attr * random.uniform(0.8, 1.4)), 2)
                initiator = char_a if bio_a.hormonal_state in ("aroused", "heightened") else char_b

                encounter = SexualEncounter(
                    character_a_id=char_a.id,
                    character_b_id=char_b.id,
                    sim_day=sim_day,
                    location_id=loc_id,
                    initiated_by=initiator.id,
                    intensity=intensity,
                    witness_ids_json="[]",
                )
                db.add(encounter)
                db.flush()

                # Write memories for participants
                _write_encounter_memory(char_a, char_b, location, intensity, sim_day, db,
                                        is_initiator=(initiator.id == char_a.id))
                _write_encounter_memory(char_b, char_a, location, intensity, sim_day, db,
                                        is_initiator=(initiator.id == char_b.id))

                # Update attraction — acknowledged after encounter
                for attr in [
                    db.query(PhysicalAttraction).filter(
                        PhysicalAttraction.from_character_id == char_a.id,
                        PhysicalAttraction.to_character_id == char_b.id,
                    ).first(),
                    db.query(PhysicalAttraction).filter(
                        PhysicalAttraction.from_character_id == char_b.id,
                        PhysicalAttraction.to_character_id == char_a.id,
                    ).first(),
                ]:
                    if attr:
                        attr.acknowledged = True

                # Relationship deepens
                for from_id, to_id in [(char_a.id, char_b.id), (char_b.id, char_a.id)]:
                    rel = db.query(CharacterRelationship).filter(
                        CharacterRelationship.from_character_id == from_id,
                        CharacterRelationship.to_character_id == to_id,
                    ).first()
                    if rel:
                        rel.familiarity = min(1.0, rel.familiarity + 0.08)
                        rel.trust_level = min(1.0, rel.trust_level + 0.04)

                # Witnesses — other characters at same location
                witnesses = [c for c in occupants if c.id not in [char_a.id, char_b.id]]
                witness_ids = []
                for witness in witnesses:
                    # Witness probability lower in private spaces
                    witness_prob = 0.3 if is_private else 0.75
                    if random.random() < witness_prob:
                        _write_witness_record(witness, char_a, char_b, location, intensity, sim_day, db)
                        witness_ids.append(witness.id)

                import json
                encounter.witness_ids_json = json.dumps(witness_ids)
                db.commit()

                name_a = char_a.given_name or char_a.roster_id
                name_b = char_b.given_name or char_b.roster_id
                logger.info(
                    f"  ENCOUNTER: {char_a.roster_id} ({name_a}) and "
                    f"{char_b.roster_id} ({name_b}) at {loc_name} "
                    f"(intensity={intensity}, witnesses={len(witness_ids)})"
                )

                # Generate a visible scene narrative
                if intensity >= 0.75:
                    scene = (
                        f"{name_a} and {name_b} were together at {loc_name}. "
                        f"What happened between them was sexual and real. "
                        f"Their bodies made a choice that words hadn't yet. "
                        f"{'Someone saw. ' if witness_ids else 'No one saw. '}"
                        f"Neither of them has named it yet."
                    )
                elif intensity >= 0.5:
                    scene = (
                        f"{name_a} and {name_b} were at {loc_name}. "
                        f"Something physical happened between them — not everything, but something. "
                        f"It crossed a line neither of them had crossed before. "
                        f"{'Others were nearby. ' if witness_ids else ''}"
                        f"They both know what it was."
                    )
                else:
                    scene = (
                        f"{name_a} and {name_b} were close at {loc_name} — "
                        f"close in a way that was deliberate. "
                        f"Bodies near each other. Something started."
                    )

                # Force participants into a follow-up conversation next tick
                forced_pair = (char_a.roster_id, char_b.roster_id, "aftermath")

                # Force witness pairs — witnesses should talk to each other
                witness_pairs = []
                witness_chars = [c for c in occupants if c.id in witness_ids]
                if len(witness_chars) >= 2:
                    witness_pairs.append(
                        (witness_chars[0].roster_id, witness_chars[1].roster_id, "witness")
                    )
                elif len(witness_chars) == 1:
                    # One witness — pair with a non-participant to discuss
                    others = [c for c in occupants
                              if c.id not in [char_a.id, char_b.id]
                              and c.id not in witness_ids]
                    if others:
                        partner = random.choice(others)
                        witness_pairs.append(
                            (witness_chars[0].roster_id, partner.roster_id, "witness")
                        )

                events.append({
                    "type": "sexual_encounter",
                    "character_a": char_a.roster_id,
                    "name_a": name_a,
                    "character_b": char_b.roster_id,
                    "name_b": name_b,
                    "location": loc_name,
                    "intensity": intensity,
                    "witness_count": len(witness_ids),
                    "sim_day": sim_day,
                    "scene": scene,
                    "forced_pair": forced_pair,
                    "witness_pairs": witness_pairs,
                })

    return events
