"""
procreation.py — manages conception, pregnancy, and birth in Caldwell.

This system runs once per tick after biology has updated.
It checks for conception conditions among adult pairs,
tracks pregnancies, and handles births.

Conception conditions (all required):
- Both adults (not minor), different sexes
- Mutual physical attraction >= 0.5
- Familiarity >= 0.4, trust >= 0.15
- Female in ovulation phase (cycle day 14)
- Female not already pregnant
- ~15% probability per ovulation event

Gestation: 93 sim days (~280 real days at 3:1 ratio)

Infants:
- Created as Character records with is_infant=True, age=0
- Excluded from all conversation and AI systems
- Age normally — become minor speakers at ~age 2 (sim day ~243 after birth)
- Parents carry awareness of infant in their prompts
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, CharacterBiology, PhysicalAttraction,
    CharacterRelationship, Pregnancy
)

logger = logging.getLogger("caldwell.procreation")

# Gestation in sim days (280 real days / 3)
GESTATION_SIM_DAYS = 93

# Age at which infant becomes a speaking minor (in real years)
SPEAKING_AGE_YEARS = 2.0
SPEAKING_AGE_SIM_DAYS = int(SPEAKING_AGE_YEARS * 365.25 / 3)

# Conception probability per ovulation event
CONCEPTION_PROBABILITY = 0.15


def _get_bio(character_id: int, db: Session) -> CharacterBiology | None:
    return (
        db.query(CharacterBiology)
        .filter(CharacterBiology.character_id == character_id)
        .first()
    )


def _is_pregnant(character: Character, db: Session) -> bool:
    return (
        db.query(Pregnancy)
        .filter(
            Pregnancy.mother_id == character.id,
            Pregnancy.status == "pregnant",
        )
        .first()
    ) is not None


def _get_mutual_attraction(
    char_a: Character,
    char_b: Character,
    db: Session,
) -> tuple[float, float]:
    """Returns (a_to_b, b_to_a) attraction levels."""
    a_to_b = (
        db.query(PhysicalAttraction)
        .filter(
            PhysicalAttraction.from_character_id == char_a.id,
            PhysicalAttraction.to_character_id == char_b.id,
        )
        .first()
    )
    b_to_a = (
        db.query(PhysicalAttraction)
        .filter(
            PhysicalAttraction.from_character_id == char_b.id,
            PhysicalAttraction.to_character_id == char_a.id,
        )
        .first()
    )
    return (
        a_to_b.attraction_level if a_to_b else 0.0,
        b_to_a.attraction_level if b_to_a else 0.0,
    )


def _get_relationship(
    char_a: Character,
    char_b: Character,
    db: Session,
) -> CharacterRelationship | None:
    return (
        db.query(CharacterRelationship)
        .filter(
            CharacterRelationship.from_character_id == char_a.id,
            CharacterRelationship.to_character_id == char_b.id,
        )
        .first()
    )


def _generate_infant_description(
    mother: Character,
    father: Character | None,
) -> str:
    """Generate physical description for newborn based on parents."""
    mom_desc = mother.physical_description[:60] if mother.physical_description else ""
    if father:
        dad_desc = father.physical_description[:60] if father.physical_description else ""
        return (
            f"A newborn infant. Child of "
            f"{mother.given_name or mother.roster_id} and "
            f"{father.given_name or father.roster_id}. "
            f"Small, dependent, not yet able to speak or move independently."
        )
    return (
        f"A newborn infant. Child of "
        f"{mother.given_name or mother.roster_id}. "
        f"Small, dependent, not yet able to speak or move independently."
    )


def _generate_infant_roster_id(db: Session) -> str:
    """Generate a unique roster ID for the infant."""
    # Count existing infants
    existing = db.query(Character).filter(
        Character.roster_id.like("INF-%")
    ).count()
    return f"INF-{existing + 1:02d}"


def check_conception(sim_day: int, db: Session) -> list[dict]:
    """
    Check all adult opposite-sex pairs for conception conditions.
    Only fires on ovulation day (menstrual cycle day 14).
    Returns list of conception events.
    """
    events = []

    # Get all adult females in ovulation
    ovulating = (
        db.query(Character)
        .join(CharacterBiology, CharacterBiology.character_id == Character.id)
        .filter(
            Character.alive == True,
            Character.is_minor == False,
            Character.is_infant == False,
            CharacterBiology.menstrual_phase == "ovulation",
        )
        .all()
    )

    if not ovulating:
        return events

    # Get all adult males
    adult_males = (
        db.query(Character)
        .filter(
            Character.alive == True,
            Character.is_minor == False,
            Character.is_infant == False,
            Character.gender == "M",
        )
        .all()
    )

    for female in ovulating:
        # Skip if already pregnant
        if _is_pregnant(female, db):
            continue

        female_bio = _get_bio(female.id, db)
        if not female_bio:
            continue

        for male in adult_males:
            # Check mutual attraction threshold
            f_to_m, m_to_f = _get_mutual_attraction(female, male, db)
            if f_to_m < 0.5 or m_to_f < 0.5:
                continue

            # Check relationship depth
            rel = _get_relationship(female, male, db)
            if not rel or rel.familiarity < 0.4 or rel.trust_level < 0.15:
                continue

            # Check if they're co-located (must be together)
            if female.current_location_id != male.current_location_id:
                continue

            # Roll for conception
            if random.random() > CONCEPTION_PROBABILITY:
                continue

            # Conception occurs
            pregnancy = Pregnancy(
                mother_id=female.id,
                father_id=male.id,
                conception_day=sim_day,
                expected_birth_day=sim_day + GESTATION_SIM_DAYS,
                status="pregnant",
            )
            db.add(pregnancy)
            db.commit()

            mother_name = female.given_name or female.roster_id
            father_name = male.given_name or male.roster_id
            logger.info(
                f"  CONCEPTION: {female.roster_id} ({mother_name}) "
                f"and {male.roster_id} ({father_name}) — "
                f"birth expected Day {sim_day + GESTATION_SIM_DAYS}"
            )

            events.append({
                "type": "conception",
                "mother": female.roster_id,
                "mother_name": mother_name,
                "father": male.roster_id,
                "father_name": father_name,
                "expected_birth_day": sim_day + GESTATION_SIM_DAYS,
                "sim_day": sim_day,
            })

            # Only one conception per female per tick
            break

    return events


def check_births(sim_day: int, db: Session) -> list[dict]:
    """
    Check for pregnancies that have reached term and deliver.
    Creates a new Character record for the infant.
    """
    events = []

    due = (
        db.query(Pregnancy)
        .filter(
            Pregnancy.status == "pregnant",
            Pregnancy.expected_birth_day <= sim_day,
        )
        .all()
    )

    for pregnancy in due:
        mother = db.query(Character).filter(
            Character.id == pregnancy.mother_id
        ).first()
        father = db.query(Character).filter(
            Character.id == pregnancy.father_id
        ).first() if pregnancy.father_id else None

        if not mother:
            continue

        # Determine infant gender
        gender = random.choice(["F", "M"])

        # Create infant character
        roster_id = _generate_infant_roster_id(db)
        description = _generate_infant_description(mother, father)

        infant = Character(
            roster_id=roster_id,
            gender=gender,
            age=0,
            is_minor=True,
            is_infant=True,
            alive=True,
            physical_description=description,
            natural_tendency=(
                "An infant. Cannot yet speak, walk independently, or care for itself. "
                "Entirely dependent on others for survival."
            ),
            core_drive="Survival",
            personality_traits="",
            ai_model="deepseek",
            current_location_id=mother.current_location_id,
        )
        db.add(infant)
        db.flush()

        # Update pregnancy record
        pregnancy.status = "born"
        pregnancy.actual_birth_day = sim_day
        pregnancy.born_character_id = infant.id
        db.commit()

        mother_name = mother.given_name or mother.roster_id
        father_name = father.given_name or father.roster_id if father else "unknown"

        logger.info(
            f"  BIRTH: {roster_id} born to {mother.roster_id} ({mother_name}) "
            f"and {father.roster_id if father else 'unknown'} ({father_name}) "
            f"on Day {sim_day}"
        )

        events.append({
            "type": "birth",
            "infant_roster_id": roster_id,
            "gender": gender,
            "mother": mother.roster_id,
            "mother_name": mother_name,
            "father": father.roster_id if father else None,
            "father_name": father_name,
            "sim_day": sim_day,
        })

    return events


def check_infant_maturation(sim_day: int, db: Session) -> list[dict]:
    """
    Check if any infants have reached speaking age (~2 real years).
    Removes is_infant flag so they enter the conversation system.
    """
    events = []

    infants = db.query(Character).filter(
        Character.is_infant == True,
        Character.alive == True,
    ).all()

    for infant in infants:
        bio = _get_bio(infant.id, db)
        if bio and bio.age_float and bio.age_float >= SPEAKING_AGE_YEARS:
            infant.is_infant = False
            db.commit()
            logger.info(
                f"  MATURATION: {infant.roster_id} has reached speaking age "
                f"({bio.age_float:.1f} years)"
            )
            events.append({
                "type": "infant_maturation",
                "roster_id": infant.roster_id,
                "age": bio.age_float,
                "sim_day": sim_day,
            })

    return events


def get_pregnancy_status(character: Character, db: Session) -> dict | None:
    """Returns pregnancy info for a character's prompt if applicable."""
    # Check if mother
    active = (
        db.query(Pregnancy)
        .filter(
            Pregnancy.mother_id == character.id,
            Pregnancy.status == "pregnant",
        )
        .first()
    )
    if active:
        days_remaining = active.expected_birth_day - active.conception_day
        days_elapsed = max(0, days_remaining - (active.expected_birth_day - active.conception_day))
        trimester = 1 if days_elapsed < 31 else (2 if days_elapsed < 62 else 3)
        return {
            "pregnant": True,
            "trimester": trimester,
            "days_remaining": days_remaining,
            "father_id": active.father_id,
        }

    # Check if has living infant
    born = (
        db.query(Pregnancy)
        .filter(
            Pregnancy.mother_id == character.id,
            Pregnancy.status == "born",
        )
        .all()
    )
    living_infants = []
    for p in born:
        if p.born_character_id:
            infant = db.query(Character).filter(
                Character.id == p.born_character_id,
                Character.alive == True,
            ).first()
            if infant:
                living_infants.append(infant)

    if living_infants:
        names = [
            (inf.given_name or inf.roster_id) for inf in living_infants
        ]
        return {
            "pregnant": False,
            "has_infant": True,
            "infant_names": names,
            "infant_count": len(living_infants),
        }

    return None
