"""
world_expansion.py — manages organic world growth in Caldwell.

Handles:
1. Discovery detection — scanning action memories and dialogues for
   language suggesting a new space was found
2. Location creation — adding discovered spaces to the Location table
   so characters can actually move there
3. Location claiming — tracking who tends each space
4. Map position assignment — placing new locations on the live map
"""
import json
import logging
import random
import re
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, Memory, Dialogue,
    EmergentLocation, LocationClaim
)

logger = logging.getLogger("caldwell.world")

# Keywords suggesting discovery of a new space
DISCOVERY_PATTERNS = [
    r"find[s]? (?:a |an )?(?:new |hidden |abandoned |small |separate |empty )?(?:room|space|place|area|spot|building|structure|clearing|path|trail|door|passage|cave|shelter|field|garden|courtyard|basement|rooftop|tower|ruin)",
    r"discover[s]? (?:a |an )?(?:new |hidden |abandoned |small )?(?:room|space|place|area|spot|building)",
    r"stumbl(?:e|es|ed) (?:upon|across|into) (?:a |an )?(?:room|space|place|area|building|structure)",
    r"(?:explore[s]?|venture[s]?|walk[s]?) (?:beyond|past|outside|further than|away from)",
    r"(?:has never|had never|first time) (?:been|seen|noticed) (?:this|here)",
    r"(?:outside|beyond) (?:the|caldwell|the city|the walls|the edge)",
    r"(?:no one|nobody) (?:has|had) (?:been|come) here",
    r"(?:new|unexplored|unknown) territory",
    r"outside (?:the|caldwell)",
]

# Keywords suggesting claiming or naming a space
CLAIM_PATTERNS = [
    r"(?:this is|this will be|this becomes?) (?:my|our|mine)",
    r"(?:i |we )(?:claim|take|use|make) this (?:space|place|room|area)",
    r"(?:call|name|call it|name it) (?:the )?(\w+)",
    r"(?:my|our) (?:place|space|room|spot|territory)",
    r"(?:i live|we live|i stay|we stay) here",
]

# Map zones for placing new locations
# Inside Caldwell — gaps between existing locations
INSIDE_POSITIONS = [
    (0.35, 0.25), (0.65, 0.25), (0.15, 0.55),
    (0.85, 0.55), (0.35, 0.75), (0.65, 0.75),
    (0.50, 0.15), (0.50, 0.85), (0.20, 0.35),
]

# Outside Caldwell — beyond the edges
OUTSIDE_POSITIONS = [
    (0.05, 0.30), (0.05, 0.70), (0.95, 0.30),
    (0.95, 0.70), (0.30, 0.05), (0.70, 0.05),
    (0.30, 0.95), (0.70, 0.95), (0.02, 0.50),
    (0.98, 0.50), (0.50, 0.02), (0.50, 0.98),
]

# Location name generators for discovered spaces
INSIDE_NAME_TEMPLATES = [
    "The Hidden {noun}",
    "The Back {noun}",
    "The Old {noun}",
    "The Forgotten {noun}",
    "The Small {noun}",
    "The Lower {noun}",
    "The Upper {noun}",
]

OUTSIDE_NAME_TEMPLATES = [
    "The {adj} Field",
    "The {adj} Ground",
    "The Far {noun}",
    "The {adj} Trail",
    "The Outer {noun}",
    "Beyond the {noun}",
]

INSIDE_NOUNS = ["Room", "Hall", "Passage", "Corner", "Chamber", "Cellar", "Loft", "Court"]
OUTSIDE_NOUNS = ["Meadow", "Road", "Path", "Ridge", "Creek", "Forest", "Shore", "Hill"]
OUTSIDE_ADJS = ["Open", "Distant", "Wide", "Quiet", "Overgrown", "Sunlit", "Windswept"]


def _get_used_map_positions(db: Session) -> set[tuple]:
    """Get all currently used map positions to avoid overlap."""
    used = set()
    emergent = db.query(EmergentLocation).all()
    for e in emergent:
        if e.map_x and e.map_y:
            used.add((round(e.map_x, 2), round(e.map_y, 2)))
    return used


def _assign_map_position(is_outside: bool, db: Session) -> tuple[float, float]:
    """Assign a map position for a new location."""
    used = _get_used_map_positions(db)
    pool = OUTSIDE_POSITIONS if is_outside else INSIDE_POSITIONS
    for pos in pool:
        if pos not in used:
            return pos
    # If all slots taken, generate random position in appropriate zone
    if is_outside:
        return (random.choice([random.uniform(0, 0.1), random.uniform(0.9, 1.0)]),
                random.uniform(0.1, 0.9))
    return (random.uniform(0.2, 0.8), random.uniform(0.2, 0.8))


def _generate_location_name(is_outside: bool, discovery_text: str = "") -> str:
    """Generate a name for a new emergent location."""
    # Try to extract a noun from discovery text
    words = discovery_text.lower().split() if discovery_text else []
    location_nouns = ["room", "space", "building", "field", "path", "clearing",
                      "shelter", "garden", "courtyard", "basement", "tower"]
    found_noun = next((w for w in words if w in location_nouns), None)

    if is_outside:
        template = random.choice(OUTSIDE_NAME_TEMPLATES)
        noun = found_noun.capitalize() if found_noun else random.choice(OUTSIDE_NOUNS)
        adj = random.choice(OUTSIDE_ADJS)
        return template.format(noun=noun, adj=adj)
    else:
        template = random.choice(INSIDE_NAME_TEMPLATES)
        noun = found_noun.capitalize() if found_noun else random.choice(INSIDE_NOUNS)
        return template.format(noun=noun)


def _generate_location_description(
    character: Character,
    discovery_text: str,
    is_outside: bool,
) -> str:
    """Generate a description for a new location based on how it was discovered."""
    name = character.given_name or character.physical_description[:30]
    if is_outside:
        return (
            f"A space beyond Caldwell's familiar boundaries, first reached by {name}. "
            f"What was found there: {discovery_text[:150]}"
        )
    return (
        f"A space within Caldwell discovered by {name}. "
        f"{discovery_text[:150]}"
    )


def create_emergent_location(
    character: Character,
    discovery_text: str,
    is_outside: bool,
    sim_day: int,
    db: Session,
    custom_name: str | None = None,
) -> Location | None:
    """
    Create a new location that a character has discovered or built.
    Returns the new Location object so characters can be moved there.
    """
    name = custom_name or _generate_location_name(is_outside, discovery_text)

    # Check if a location with this name already exists
    existing = db.query(Location).filter(Location.name == name).first()
    if existing:
        logger.info(f"Location '{name}' already exists, skipping creation")
        return existing

    description = _generate_location_description(character, discovery_text, is_outside)
    map_x, map_y = _assign_map_position(is_outside, db)

    # Create the main Location record
    new_loc = Location(
        name=name,
        description=description,
        capacity=random.randint(3, 8),
        location_type="emergent_outside" if is_outside else "emergent_inside",
    )
    db.add(new_loc)
    db.flush()

    # Create the emergent record
    emergent = EmergentLocation(
        location_id=new_loc.id,
        discovered_by_id=character.id,
        discovery_day=sim_day,
        discovery_description=discovery_text[:300],
        is_outside=is_outside,
        origin_type="discovered" if not custom_name else "named",
        map_x=map_x,
        map_y=map_y,
    )
    db.add(emergent)
    db.commit()

    # Move the discovering character there
    character.current_location_id = new_loc.id
    db.commit()

    logger.info(
        f"Day {sim_day}: New location created — '{name}' "
        f"({'outside' if is_outside else 'inside'}) "
        f"discovered by {character.roster_id}"
    )
    return new_loc


def scan_for_discoveries(sim_day: int, db: Session) -> list[dict]:
    """
    Scan today's action memories and dialogues for discovery language.
    Creates new locations when strong discovery signals are found.
    """
    discoveries = []

    # Scan action memories from today
    memories = (
        db.query(Memory)
        .filter(
            Memory.sim_day == sim_day,
            Memory.memory_type == "action",
        )
        .all()
    )

    for mem in memories:
        text = mem.content.lower()
        score = 0
        for pattern in DISCOVERY_PATTERNS:
            if re.search(pattern, text):
                score += 1

        if score < 2:
            continue

        # Strong discovery signal
        char = db.query(Character).filter(
            Character.id == mem.character_id
        ).first()
        if not char:
            continue

        # Determine if outside
        is_outside = any(
            kw in text for kw in [
                "outside", "beyond", "wall", "edge", "far from",
                "away from caldwell", "left caldwell", "past the"
            ]
        )

        # Don't create if character is already at an emergent location
        existing_emergent = (
            db.query(EmergentLocation)
            .filter(EmergentLocation.location_id == char.current_location_id)
            .first()
        )
        if existing_emergent:
            continue

        new_loc = create_emergent_location(
            char, mem.content, is_outside, sim_day, db
        )
        if new_loc:
            discoveries.append({
                "character": char.roster_id,
                "location": new_loc.name,
                "is_outside": is_outside,
                "sim_day": sim_day,
            })

    return discoveries


def update_location_claims(sim_day: int, db: Session):
    """
    Update claim strengths based on today's location visits.
    Characters who are repeatedly at the same location build claims.
    """
    chars = db.query(Character).filter(Character.alive == True).all()

    for char in chars:
        if not char.current_location_id:
            continue

        claim = (
            db.query(LocationClaim)
            .filter(
                LocationClaim.character_id == char.id,
                LocationClaim.location_id == char.current_location_id,
            )
            .first()
        )

        if claim:
            claim.visit_count += 1
            claim.last_visit_day = sim_day
            claim.claim_strength = min(1.0, claim.visit_count / 30.0)
        else:
            claim = LocationClaim(
                character_id=char.id,
                location_id=char.current_location_id,
                visit_count=1,
                claim_strength=0.03,
                first_visit_day=sim_day,
                last_visit_day=sim_day,
            )
            db.add(claim)

    db.commit()


def get_location_owner(location_id: int, db: Session) -> Character | None:
    """
    Returns the character with the strongest claim on a location.
    Only returns if claim_strength > 0.5 (genuinely their space).
    """
    top_claim = (
        db.query(LocationClaim)
        .filter(
            LocationClaim.location_id == location_id,
            LocationClaim.claim_strength >= 0.5,
        )
        .order_by(LocationClaim.claim_strength.desc())
        .first()
    )
    if not top_claim:
        return None
    return db.query(Character).filter(
        Character.id == top_claim.character_id
    ).first()


def get_location_context_for_prompt(
    location: Location,
    character: Character,
    db: Session,
) -> str | None:
    """
    Returns additional context about a location for character prompts.
    Includes ownership info and whether the character was the discoverer.
    """
    parts = []

    # Check if emergent
    emergent = (
        db.query(EmergentLocation)
        .filter(EmergentLocation.location_id == location.id)
        .first()
    )
    if emergent:
        discoverer = db.query(Character).filter(
            Character.id == emergent.discovered_by_id
        ).first()
        if discoverer:
            disc_name = discoverer.given_name or discoverer.physical_description[:30]
            if discoverer.id == character.id:
                parts.append(
                    f"This is a place you found on Day {emergent.discovery_day}. "
                    f"You were the first here."
                )
            else:
                parts.append(
                    f"This place was first found by {disc_name}."
                )

        if emergent.is_outside:
            parts.append("You are outside Caldwell's familiar bounds.")

    # Check for strong claims
    owner = get_location_owner(location.id, db)
    if owner and owner.id != character.id:
        owner_name = owner.given_name or owner.physical_description[:30]
        parts.append(
            f"{owner_name} tends to this space more than anyone else. "
            f"It has become associated with them."
        )
    elif owner and owner.id == character.id:
        parts.append("This has become your space — you return here more than anyone.")

    return " ".join(parts) if parts else None
