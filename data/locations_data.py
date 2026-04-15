"""
The 12 named locations of Caldwell — a small abandoned southern city.
Live oaks, Spanish moss, brick from the 1940s–1980s. Warm year-round.
Feels recently abandoned, mid-life. Ceiling fans still turning.
"""

LOCATIONS = [
    {
        "name": "Central Square",
        "description": (
            "The heart of Caldwell. A wide brick plaza shaded by four enormous live oak trees "
            "draped in Spanish moss. A stone fountain at the center still runs. Wrought-iron "
            "benches ring the trees. The natural gathering point — voices carry here and "
            "everyone passes through eventually."
        ),
        "location_type": "public",
        "capacity": 30,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
    {
        "name": "The Meridian",
        "description": (
            "A four-story brick apartment building on the north edge of the square. "
            "Thirty-two units total. Eight have corner windows, working ceiling fans, "
            "and rooftop access — the desirable ones. The other twenty-four are fine but ordinary. "
            "The first contested resource in Caldwell."
        ),
        "location_type": "residential",
        "capacity": 32,
        "has_desirable_units": True,
        "desirable_unit_count": 8,
        "resource_tier": 2,
    },
    {
        "name": "Bayou Market",
        "description": (
            "A covered market hall two blocks from the square. Wooden stalls, "
            "concrete floors, corrugated tin roof. Food appears here daily — "
            "fresh produce, bread, preserved goods — in quantities sufficient for everyone. "
            "No one knows how or why. It simply does."
        ),
        "location_type": "public",
        "capacity": 20,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
    {
        "name": "The Workshop",
        "description": (
            "A low cinder-block building behind the market. Contains woodworking tools, "
            "basic metalworking equipment, a hand-cranked lathe, hammers, nails, rope, "
            "and shelves of hardware. Everything works. Power tools run on a generator "
            "that never seems to need fuel. Whoever controls this space controls what gets built."
        ),
        "location_type": "industrial",
        "capacity": 8,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 2,
    },
    {
        "name": "Caldwell Public Library",
        "description": (
            "A Carnegie-style brick building with tall windows and oak reading tables. "
            "Thousands of books — history, science, fiction, philosophy, medicine — "
            "but no power, no internet, no connection to anything outside. "
            "Whoever reads here first gains knowledge the others do not have. "
            "The most dangerous building in Caldwell."
        ),
        "location_type": "public",
        "capacity": 15,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 2,
    },
    {
        "name": "Community Center",
        "description": (
            "A large open-plan building with a scuffed hardwood floor, "
            "a stage at one end, folding chairs stacked against the walls, "
            "and a commercial kitchen in back. "
            "Big enough for all thirty people to gather. The natural venue for any meeting "
            "the group decides to hold — if they decide to hold meetings at all."
        ),
        "location_type": "public",
        "capacity": 30,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
    {
        "name": "Riverside Park",
        "description": (
            "A long narrow park running along a slow brown creek. "
            "Pecan and magnolia trees. A footbridge. Wooden picnic tables "
            "weathered to silver. Quiet. The place people come when they need "
            "to think or to be alone, or to have a conversation they don't want overheard."
        ),
        "location_type": "public",
        "capacity": 15,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
    {
        "name": "Warehouse Row",
        "description": (
            "Three connected brick warehouses on the south edge of the city. "
            "High ceilings, loading docks, deep shadows. Empty but structurally sound. "
            "Could become storage, housing, workshop, or anything else "
            "if someone decides it should. Currently unclaimed."
        ),
        "location_type": "industrial",
        "capacity": 12,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 2,
    },
    {
        "name": "Rooftop Garden",
        "description": (
            "The rooftop of the Meridian. Accessible by a painted metal staircase. "
            "Raised planting beds — some still have herbs growing wild: mint, rosemary, "
            "something that might be medicinal. The best unobstructed view of all of Caldwell. "
            "On clear nights, the stars are extraordinary. "
            "Whoever finds this first will not easily share it."
        ),
        "location_type": "public",
        "capacity": 6,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 2,
    },
    {
        "name": "The Chapel",
        "description": (
            "A small white clapboard building at the corner of two quiet streets. "
            "Empty inside — pews removed, walls bare, floors clean. "
            "No symbols, no affiliation. A space that feels designed for something "
            "the group has not yet invented. Acoustically perfect."
        ),
        "location_type": "public",
        "capacity": 20,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
    {
        "name": "The Schoolhouse",
        "description": (
            "A single-story brick building with six classrooms, desks bolted to the floor, "
            "chalkboards, chalk, reams of blank paper, and pencils. "
            "A clock on the wall that still keeps time. "
            "Nothing has been taught here yet. Nothing has been written on the boards. "
            "Whoever claims this space claims the power to decide what is learned."
        ),
        "location_type": "public",
        "capacity": 25,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 2,
    },
    {
        "name": "Lakeview Flats",
        "description": (
            "A two-block stretch of row houses on the west side — "
            "small, modest, wood-frame homes with front porches and screen doors. "
            "Twenty units, all roughly equivalent in size and comfort. "
            "The more private alternative to the Meridian. "
            "Quieter. More separated. Better for people who do not want neighbors."
        ),
        "location_type": "residential",
        "capacity": 20,
        "has_desirable_units": False,
        "desirable_unit_count": 0,
        "resource_tier": 1,
    },
]
