"""
The 12 named locations of Caldwell — a small abandoned southern city.
Live oaks, Spanish moss, brick from the 1940s–1980s. Warm year-round.
Feels recently abandoned, mid-life. Ceiling fans still turning.
"""

# Coordinates match LOCATION_LAYOUT in static/game.html (0.0–1.0 fractions of map W×H)
_SEED_COORDS = {
    "Central Square":          (0.50, 0.50),
    "The Meridian":            (0.50, 0.22),
    "Bayou Market":            (0.73, 0.47),
    "The Workshop":            (0.70, 0.70),
    "Caldwell Public Library": (0.27, 0.28),
    "Community Center":        (0.50, 0.76),
    "Riverside Park":          (0.15, 0.55),
    "Warehouse Row":           (0.60, 0.88),
    "Rooftop Garden":          (0.50, 0.10),
    "The Chapel":              (0.75, 0.25),
    "The Schoolhouse":         (0.30, 0.12),
    "Lakeview Flats":          (0.14, 0.78),
}

_SEED_DEFAULTS = {
    "is_seed": True,
    "is_emergent": False,
    "discovery_stage": "confirmed",
    "territory_type": "inside",
    "confidence": 1.0,
    "danger_level": 0.0,
    "use_count": 0,
}


def _loc(name, description, location_type, capacity, has_desirable_units,
         desirable_unit_count, resource_tier, location_category=None):
    x, y = _SEED_COORDS[name]
    return {
        "name": name,
        "description": description,
        "location_type": location_type,
        "capacity": capacity,
        "has_desirable_units": has_desirable_units,
        "desirable_unit_count": desirable_unit_count,
        "resource_tier": resource_tier,
        "location_category": location_category,
        "map_x": x,
        "map_y": y,
        **_SEED_DEFAULTS,
    }


LOCATIONS = [
    _loc(
        "Central Square",
        (
            "The heart of Caldwell. A wide brick plaza shaded by four enormous live oak trees "
            "draped in Spanish moss. A stone fountain at the center still runs. Wrought-iron "
            "benches ring the trees. The natural gathering point — voices carry here and "
            "everyone passes through eventually."
        ),
        "public", 30, False, 0, 1, "gathering_place",
    ),
    _loc(
        "The Meridian",
        (
            "A four-story brick apartment building on the north edge of the square. "
            "Thirty-two units total. Eight have corner windows, working ceiling fans, "
            "and rooftop access — the desirable ones. The other twenty-four are fine but ordinary. "
            "The first contested resource in Caldwell."
        ),
        "residential", 32, True, 8, 2, "shelter",
    ),
    _loc(
        "Bayou Market",
        (
            "A covered market hall two blocks from the square. Wooden stalls, "
            "concrete floors, corrugated tin roof. Food appears here daily — "
            "fresh produce, bread, preserved goods — in quantities sufficient for everyone. "
            "No one knows how or why. It simply does."
        ),
        "public", 20, False, 0, 1, "gathering_place",
    ),
    _loc(
        "The Workshop",
        (
            "A low cinder-block building behind the market. Contains woodworking tools, "
            "basic metalworking equipment, a hand-cranked lathe, hammers, nails, rope, "
            "and shelves of hardware. Everything works. Power tools run on a generator "
            "that never seems to need fuel. Whoever controls this space controls what gets built."
        ),
        "industrial", 8, False, 0, 2, "work_area",
    ),
    _loc(
        "Caldwell Public Library",
        (
            "A Carnegie-style brick building with tall windows and oak reading tables. "
            "Thousands of books — history, science, fiction, philosophy, medicine — "
            "but no power, no internet, no connection to anything outside. "
            "Whoever reads here first gains knowledge the others do not have. "
            "The most dangerous building in Caldwell."
        ),
        "public", 15, False, 0, 2, "gathering_place",
    ),
    _loc(
        "Community Center",
        (
            "A large open-plan building with a scuffed hardwood floor, "
            "a stage at one end, folding chairs stacked against the walls, "
            "and a commercial kitchen in back. "
            "Big enough for all thirty people to gather. The natural venue for any meeting "
            "the group decides to hold — if they decide to hold meetings at all."
        ),
        "public", 30, False, 0, 1, "gathering_place",
    ),
    _loc(
        "Riverside Park",
        (
            "A long narrow park running along a slow brown creek. "
            "Pecan and magnolia trees. A footbridge. Wooden picnic tables "
            "weathered to silver. Quiet. The place people come when they need "
            "to think or to be alone, or to have a conversation they don't want overheard."
        ),
        "public", 15, False, 0, 1, "water_source",
    ),
    _loc(
        "Warehouse Row",
        (
            "Three connected brick warehouses on the south edge of the city. "
            "High ceilings, loading docks, deep shadows. Empty but structurally sound. "
            "Could become storage, housing, workshop, or anything else "
            "if someone decides it should. Currently unclaimed."
        ),
        "industrial", 12, False, 0, 2, "work_area",
    ),
    _loc(
        "Rooftop Garden",
        (
            "The rooftop of the Meridian. Accessible by a painted metal staircase. "
            "Raised planting beds — some still have herbs growing wild: mint, rosemary, "
            "something that might be medicinal. The best unobstructed view of all of Caldwell. "
            "On clear nights, the stars are extraordinary. "
            "Whoever finds this first will not easily share it."
        ),
        "public", 6, False, 0, 2, "garden",
    ),
    _loc(
        "The Chapel",
        (
            "A small white clapboard building at the corner of two quiet streets. "
            "Empty inside — pews removed, walls bare, floors clean. "
            "No symbols, no affiliation. A space that feels designed for something "
            "the group has not yet invented. Acoustically perfect."
        ),
        "public", 20, False, 0, 1, "ritual_area",
    ),
    _loc(
        "The Schoolhouse",
        (
            "A single-story brick building with six classrooms, desks bolted to the floor, "
            "chalkboards, chalk, reams of blank paper, and pencils. "
            "A clock on the wall that still keeps time. "
            "Nothing has been taught here yet. Nothing has been written on the boards. "
            "Whoever claims this space claims the power to decide what is learned."
        ),
        "public", 25, False, 0, 2, "gathering_place",
    ),
    _loc(
        "Lakeview Flats",
        (
            "A two-block stretch of row houses on the west side — "
            "small, modest, wood-frame homes with front porches and screen doors. "
            "Twenty units, all roughly equivalent in size and comfort. "
            "The more private alternative to the Meridian. "
            "Quieter. More separated. Better for people who do not want neighbors."
        ),
        "residential", 20, False, 0, 1, "shelter",
    ),
]
