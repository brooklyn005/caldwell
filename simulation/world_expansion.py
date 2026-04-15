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
import math
import random
import re
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, Memory, Dialogue,
    EmergentLocation, LocationClaim, DiscoveryCandidate, SilentAction,
)

logger = logging.getLogger("caldwell.world")

# ── Discovery signal detection ────────────────────────────────────────────────

# Physical location keywords that suggest a real place was found
_LOCATION_KEYWORDS = [
    "forest", "trail", "clearing", "ruin", "ruins", "shelter", "creek",
    "ridge", "hollow", "grove", "old building", "abandoned building",
    "abandoned", "hidden", "passage", "cave", "field", "garden",
    "courtyard", "basement", "rooftop", "tower", "path", "meadow",
    "stream", "pond", "structure", "warehouse", "shed", "room",
]

# Phrases that signal literal exploration
_EXPLORATION_PHRASES = [
    r"edge of\b", r"past the\b", r"beyond the\b", r"found a\b", r"found an\b",
    r"discover(?:ed|s)?\b", r"stumbled upon\b", r"stumbled across\b",
    r"stumbled into\b", r"explore[ds]?\b", r"ventured?\b",
    r"never been here\b", r"first time (?:here|in this)\b",
    r"no one has been\b", r"nobody has been\b",
    r"outside (?:the city|caldwell|the edge|the walls)\b",
    r"outside caldwell\b", r"beyond caldwell\b",
]

# Metaphorical phrases that must NOT trigger discovery
_METAPHOR_BLOCKLIST = [
    r"beyond my reach\b", r"beyond our reach\b",
    r"beyond words\b", r"beyond understanding\b",
    r"lost in thought\b", r"lost in my thoughts\b",
    r"lost in memories\b", r"lost myself\b",
    r"trail of thought\b", r"train of thought\b",
    r"hollow feeling\b", r"hollow inside\b", r"feels hollow\b",
    r"cleared my mind\b", r"clearing my head\b",
    r"abandoned hope\b", r"abandoned the idea\b",
    r"hidden feelings\b", r"hidden truth\b", r"hidden meaning\b",
    r"sheltered from\b", r"shelter of\b",
    r"path forward\b", r"path in life\b",
    r"found myself\b", r"found my way\b", r"found peace\b",
    r"found comfort\b", r"found meaning\b", r"found purpose\b",
]

# Outside indicators — if present alongside a keyword, territory_type = outside/frontier
_OUTSIDE_INDICATORS = [
    "outside", "beyond caldwell", "past the edge", "left caldwell",
    "away from caldwell", "edge of the city", "outside the walls",
    "far from", "further than", "past the boundary",
]


def _is_metaphorical(text: str) -> bool:
    """Return True if the text matches a known metaphorical pattern."""
    for pattern in _METAPHOR_BLOCKLIST:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_location_hints(text: str) -> list[str]:
    """
    Return keyword hints found in text that plausibly describe a physical place.
    Returns empty list if no exploration phrase is present or text is metaphorical.
    """
    lower = text.lower()
    if _is_metaphorical(lower):
        return []

    # Require at least one exploration phrase
    has_exploration = any(
        re.search(p, lower) for p in _EXPLORATION_PHRASES
    )
    if not has_exploration:
        return []

    # Collect matched location keywords
    hints = []
    for kw in _LOCATION_KEYWORDS:
        if kw in lower:
            hints.append(kw)
    return hints


def _territory_for_text(text: str) -> str:
    lower = text.lower()
    for indicator in _OUTSIDE_INDICATORS:
        if indicator in lower:
            return "outside"
    return "inside"


# ── Discovery candidate management ────────────────────────────────────────────

def _upsert_candidate(
    name_hint: str,
    source_id: int,
    source_type: str,
    territory_type: str,
    sim_day: int,
    db: Session,
) -> DiscoveryCandidate:
    """
    Find or create a DiscoveryCandidate for name_hint.
    Update confidence based on how many distinct source_types have contributed.
    Returns the updated candidate.
    """
    candidate = (
        db.query(DiscoveryCandidate)
        .filter(
            DiscoveryCandidate.name_hint == name_hint,
            DiscoveryCandidate.promoted_to_location_id.is_(None),
        )
        .first()
    )

    if not candidate:
        candidate = DiscoveryCandidate(
            name_hint=name_hint,
            confidence=0.3,
            source_ids_json=json.dumps([source_id]),
            source_types_json=json.dumps([source_type]),
            territory_type=territory_type,
            sim_day=sim_day,
        )
        db.add(candidate)
        db.flush()
        return candidate

    # Merge in new source
    source_ids = json.loads(candidate.source_ids_json or "[]")
    source_types = json.loads(candidate.source_types_json or "[]")

    if source_id not in source_ids:
        source_ids.append(source_id)
        candidate.source_ids_json = json.dumps(source_ids)

    if source_type not in source_types:
        source_types.append(source_type)
        candidate.source_types_json = json.dumps(source_types)

    # Confidence tiers based on number of distinct source types
    n_sources = len(set(source_types))
    if n_sources >= 3:
        candidate.confidence = 0.9
    elif n_sources >= 2:
        candidate.confidence = 0.6
    # else stays at 0.3

    return candidate


# Keywords suggesting claiming or naming a space
CLAIM_PATTERNS = [
    r"(?:this is|this will be|this becomes?) (?:my|our|mine)",
    r"(?:i |we )(?:claim|take|use|make) this (?:space|place|room|area)",
    r"(?:call|name|call it|name it) (?:the )?(\w+)",
    r"(?:my|our) (?:place|space|room|spot|territory)",
    r"(?:i live|we live|i stay|we stay) here",
]

# Pixel dimensions of the game map (must match W, H in game.html)
_MAP_W = 900.0
_MAP_H = 650.0
_MIN_DIST_PX = 80.0  # minimum pixel distance between any two locations

# Zone definitions: (xmin, xmax, ymin, ymax) as 0.0–1.0 fractions
# "inside"   — within current seed map bounds
# "frontier" — ring just outside seed bounds
# "outside"  — near the map edges, furthest out
_ZONE_OUTER = {
    "inside":   (0.15, 0.85, 0.15, 0.85),
    "frontier": (0.05, 0.95, 0.05, 0.95),
    "outside":  (0.02, 0.98, 0.02, 0.98),
}
# For ring zones, exclude the inner box
_ZONE_EXCLUDE = {
    "frontier": (0.15, 0.85, 0.15, 0.85),
    "outside":  (0.05, 0.95, 0.05, 0.95),
}

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


def _px_dist(ax: float, ay: float, bx: float, by: float) -> float:
    """Euclidean distance in pixels between two fractional coordinates."""
    return math.sqrt(((ax - bx) * _MAP_W) ** 2 + ((ay - by) * _MAP_H) ** 2)


def _sample_in_zone(territory_type: str) -> tuple[float, float]:
    """Return a random (x, y) within the appropriate zone (rejection sampling)."""
    xmin, xmax, ymin, ymax = _ZONE_OUTER.get(territory_type, _ZONE_OUTER["inside"])
    exclude = _ZONE_EXCLUDE.get(territory_type)
    for _ in range(300):
        x = random.uniform(xmin, xmax)
        y = random.uniform(ymin, ymax)
        if exclude:
            ex0, ex1, ey0, ey1 = exclude
            if ex0 <= x <= ex1 and ey0 <= y <= ey1:
                continue  # inside exclusion zone — retry
        return x, y
    # Fallback: force into a corner band of the outer box
    x = random.choice([
        random.uniform(xmin, xmin + 0.08),
        random.uniform(xmax - 0.08, xmax),
    ])
    y = random.uniform(ymin, ymax)
    return x, y


def assign_map_coordinates(db: Session, territory_type: str = "inside") -> tuple[float, float]:
    """
    Pick a collision-free (map_x, map_y) for a new location.

    Zone placement:
      inside   — within seed map bounds (0.15–0.85 × 0.15–0.85)
      frontier — ring just outside seed bounds
      outside  — near map edges

    Up to 50 random attempts; falls back to an expanding spiral if all collide.
    Guarantees no placement within 80px of any existing location.
    """
    occupied = [
        (loc.map_x, loc.map_y)
        for loc in db.query(Location).filter(
            Location.map_x.isnot(None),
            Location.map_y.isnot(None),
        ).all()
    ]

    def no_collision(x: float, y: float) -> bool:
        return all(_px_dist(x, y, ox, oy) >= _MIN_DIST_PX for ox, oy in occupied)

    # 50 random attempts within the target zone
    for _ in range(50):
        x, y = _sample_in_zone(territory_type)
        if no_collision(x, y):
            return x, y

    # Spiral fallback — expand outward from zone centre
    outer = _ZONE_OUTER.get(territory_type, _ZONE_OUTER["inside"])
    cx = (outer[0] + outer[1]) / 2
    cy = (outer[2] + outer[3]) / 2
    step = 0.06
    for ring in range(1, 50):
        radius = ring * step
        n_pts = max(8, ring * 8)
        for i in range(n_pts):
            angle = (2 * math.pi * i) / n_pts
            x = cx + radius * math.cos(angle)
            y = cy + (radius * _MAP_W / _MAP_H) * math.sin(angle)
            x = max(0.01, min(0.99, x))
            y = max(0.01, min(0.99, y))
            if no_collision(x, y):
                return x, y

    logger.warning("assign_map_coordinates: no collision-free position found, using random fallback")
    return (random.uniform(0.05, 0.95), random.uniform(0.05, 0.95))


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
    territory_type = "outside" if is_outside else "inside"
    map_x, map_y = assign_map_coordinates(db, territory_type)

    # Create the Location record with all emergent-world fields
    new_loc = Location(
        name=name,
        description=description,
        capacity=random.randint(3, 8),
        location_type="emergent_outside" if is_outside else "emergent_inside",
        is_seed=False,
        is_emergent=True,
        territory_type=territory_type,
        discovery_stage="confirmed",
        discovered_by_id=character.roster_id,
        discovered_on_day=sim_day,
        named_by_id=custom_name and character.roster_id or None,
        discovery_origin=discovery_text[:300],
        confidence=1.0,
        map_x=map_x,
        map_y=map_y,
    )
    db.add(new_loc)
    db.flush()

    # Also create the legacy emergent record for backward compat
    try:
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
    except Exception:
        pass  # legacy table may not exist in all deployments

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


def _loc_to_world_map_payload(loc: Location, discoverer_roster_id: str, sim_day: int) -> dict:
    """Build the /api/world_map-shaped dict for a newly created Location."""
    return {
        "id": loc.id,
        "name": loc.name,
        "is_seed": loc.is_seed,
        "is_emergent": loc.is_emergent,
        "territory_type": loc.territory_type,
        "discovery_stage": loc.discovery_stage,
        "location_category": loc.location_category,
        "map_x": loc.map_x,
        "map_y": loc.map_y,
        "danger_level": loc.danger_level,
        "claim_character_id": loc.claim_character_id,
        "use_count": loc.use_count,
        "discovered_by_id": loc.discovered_by_id or discoverer_roster_id,
        "discovered_on_day": loc.discovered_on_day or sim_day,
        "confidence": loc.confidence,
    }


def scan_for_discoveries(sim_day: int, db: Session) -> list[dict]:
    """
    Scan multiple signal sources for discovery language and accumulate confidence
    in DiscoveryCandidate rows. Promotes candidates to Location rows when ready.

    Sources scanned:
      - action memories (memory_type="action")
      - monologue / feeling memories (memory_type="feeling")
      - scene dialogue content (Dialogue.dialogue_json)
      - silent action descriptions (SilentAction.description)
    """
    # ── Collect raw signals ──────────────────────────────────────────────────
    signals: list[tuple[str, int, str]] = []  # (text, record_id, source_type)

    # Action memories
    for mem in (
        db.query(Memory)
        .filter(Memory.sim_day == sim_day, Memory.memory_type == "action")
        .all()
    ):
        signals.append((mem.content, mem.id, "action_memory"))

    # Monologue / feeling memories
    for mem in (
        db.query(Memory)
        .filter(Memory.sim_day == sim_day, Memory.memory_type == "feeling")
        .all()
    ):
        signals.append((mem.content, mem.id, "monologue"))

    # Scene dialogue — flatten all exchange texts
    for dlg in db.query(Dialogue).filter(Dialogue.sim_day == sim_day).all():
        try:
            exchanges = json.loads(dlg.dialogue_json or "[]")
            combined = " ".join(ex.get("text", "") for ex in exchanges if ex.get("text"))
            if combined:
                signals.append((combined, dlg.id, "dialogue"))
        except Exception:
            pass

    # Silent action descriptions
    for sa in db.query(SilentAction).filter(SilentAction.sim_day == sim_day).all():
        if sa.description:
            signals.append((sa.description, sa.id, "silent_action"))

    # ── Process signals into candidates ──────────────────────────────────────
    for text, record_id, source_type in signals:
        hints = _extract_location_hints(text)
        if not hints:
            continue
        territory = _territory_for_text(text)
        for hint in hints:
            _upsert_candidate(hint, record_id, source_type, territory, sim_day, db)

    try:
        db.flush()
    except Exception:
        db.rollback()
        return []

    # ── Promote candidates to Location rows ──────────────────────────────────
    discoveries = []
    candidates = (
        db.query(DiscoveryCandidate)
        .filter(
            DiscoveryCandidate.confidence >= 0.6,
            DiscoveryCandidate.promoted_to_location_id.is_(None),
        )
        .all()
    )

    for candidate in candidates:
        is_outside = candidate.territory_type == "outside"
        stage = "confirmed" if candidate.confidence >= 0.9 else "tentative"

        # Use first character from any source action memory as the discoverer
        discoverer = _find_discoverer(candidate, db)
        if not discoverer:
            continue

        new_loc = create_emergent_location(
            discoverer,
            f"A {candidate.name_hint} discovered by {discoverer.given_name or discoverer.roster_id}",
            is_outside,
            sim_day,
            db,
        )
        if not new_loc:
            continue

        # Override discovery_stage with candidate's confidence level
        new_loc.discovery_stage = stage
        candidate.promoted_to_location_id = new_loc.id

        # Only write memory and increment count for confirmed discoveries
        if stage == "confirmed":
            _record_discovery_for_character(discoverer, new_loc, sim_day, db)

        try:
            db.commit()
        except Exception:
            db.rollback()
            continue

        discoveries.append(_loc_to_world_map_payload(new_loc, discoverer.roster_id, sim_day))
        logger.info(
            f"Day {sim_day}: {discoverer.given_name or discoverer.roster_id} discovered "
            f"'{new_loc.name}' (confidence={candidate.confidence}, stage={stage})"
        )

    return discoveries


def _record_discovery_for_character(
    character: Character,
    location: Location,
    sim_day: int,
    db: Session,
) -> None:
    """Write a first-person discovery memory and increment discovery_count."""
    name = character.given_name or character.roster_id
    memory_text = (
        f"{name} found {location.name} — "
        f"{location.description[:120] if location.description else 'a new place in Caldwell'}."
    )
    mem = Memory(
        character_id=character.id,
        sim_day=sim_day,
        memory_type="discovery",
        content=memory_text,
        emotional_weight=0.8,
    )
    db.add(mem)

    if character.discovery_count is None:
        character.discovery_count = 0
    character.discovery_count += 1
    logger.info(
        f"Day {sim_day}: {name} discovery_count → {character.discovery_count} "
        f"(found '{location.name}')"
    )


def _find_discoverer(candidate: DiscoveryCandidate, db: Session) -> "Character | None":
    """Find a character to attribute as the discoverer for a candidate."""
    source_ids = json.loads(candidate.source_ids_json or "[]")
    source_types = json.loads(candidate.source_types_json or "[]")

    # Prefer an action memory source
    for sid, stype in zip(source_ids, source_types):
        if stype == "action_memory":
            mem = db.query(Memory).filter(Memory.id == sid).first()
            if mem:
                char = db.query(Character).filter(Character.id == mem.character_id).first()
                if char:
                    return char

    # Fall back to any memory source
    for sid, stype in zip(source_ids, source_types):
        if stype in ("action_memory", "monologue"):
            mem = db.query(Memory).filter(Memory.id == sid).first()
            if mem:
                char = db.query(Character).filter(Character.id == mem.character_id).first()
                if char:
                    return char

    # Last resort: first alive character
    return db.query(Character).filter(Character.alive == True).first()


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
