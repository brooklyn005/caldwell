"""
seed.py — populate Caldwell for a fresh simulation start.

Key changes from previous version:
- All characters use DeepSeek (no Haiku splits)
- Characters get differentiated capability scores (strength, memory, persuasion)
- Characters scattered across locations at start (not all at Central Square)
- Resource pools initialized (food is scarce, not infinite)
- Status scores initialized at 50

Run once before starting the simulation.
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from database.db import init_db, SessionLocal
from database.models import (
    Character, Location, SimClock, DailySpend,
    ResourcePool, StatusScore
)
from data.characters_data import CHARACTERS
from data.locations_data import LOCATIONS


def seed():
    print("Initializing Caldwell...")
    init_db()
    db = SessionLocal()

    try:
        # ── Locations ────────────────────────────────────────────────────────
        if db.query(Location).count() == 0:
            print(f"Creating {len(LOCATIONS)} locations...")
            for loc_data in LOCATIONS:
                loc = Location(**loc_data)
                db.add(loc)
            db.commit()
            print("  Locations created.")
        else:
            print("  Locations already exist — skipping.")

        # ── Characters ───────────────────────────────────────────────────────
        if db.query(Character).count() == 0:
            print(f"Creating {len(CHARACTERS)} characters...")

            # All characters use DeepSeek
            # Scatter starting locations across the city
            start_locations = db.query(Location).all()
            loc_weights = {
                "Central Square": 8,
                "Bayou Market": 6,
                "Riverside Park": 5,
                "Community Center": 4,
                "The Workshop": 3,
                "Lakeview Flats": 3,
                "The Meridian": 3,
                "Warehouse Row": 2,
                "Caldwell Public Library": 2,
                "The Schoolhouse": 2,
                "Rooftop Garden": 1,
                "The Chapel": 1,
            }
            weighted_locs = []
            for loc in start_locations:
                weight = loc_weights.get(loc.name, 1)
                weighted_locs.extend([loc] * weight)

            for char_data in CHARACTERS:
                traits = char_data.pop("personality_traits", [])
                char_data.pop("private_belief", None)
                char_data.pop("fear", None)

                # Differentiated capabilities — seeded with variance
                # Each character gets a unique profile that creates interdependence
                strength = _generate_capability(char_data.get("core_drive"), "strength")
                memory = _generate_capability(char_data.get("core_drive"), "memory")
                persuasion = _generate_capability(char_data.get("core_drive"), "persuasion")

                start_loc = random.choice(weighted_locs)

                char = Character(
                    **char_data,
                    ai_model="deepseek",
                    current_location_id=start_loc.id,
                    strength_score=strength,
                    memory_score=memory,
                    persuasion_score=persuasion,
                )
                char.personality_traits = traits
                db.add(char)

            db.commit()

            # Print assignments
            print("  Characters created with capabilities:")
            all_chars = db.query(Character).order_by(Character.roster_id).all()
            for c in all_chars:
                minor_tag = " [MINOR]" if c.is_minor else ""
                loc = db.query(Location).filter(Location.id == c.current_location_id).first()
                print(
                    f"    {c.roster_id} {c.given_name or '(unnamed)':<8} age={c.age:<3} {c.core_drive:<12} "
                    f"str={c.strength_score} mem={c.memory_score} per={c.persuasion_score} "
                    f"@ {loc.name if loc else '?'}{minor_tag}"
                )
        else:
            print("  Characters already exist — skipping.")

        # ── Resource pools ───────────────────────────────────────────────────
        if db.query(ResourcePool).count() == 0:
            print("Initializing resource pools (food scarcity system)...")
            market = db.query(Location).filter(Location.name == "Bayou Market").first()
            community = db.query(Location).filter(Location.name == "Community Center").first()

            if market:
                # 60 units initial — 2 days for 30 people
                # Replenishes 42 units every 3 days — slightly scarce
                db.add(ResourcePool(
                    location_id=market.id,
                    resource_type="food",
                    quantity=60.0,
                    max_quantity=90.0,
                    last_replenish_day=1,
                    replenish_interval=3,
                    replenish_amount=42.0,
                ))
            if community:
                # Smaller secondary supply
                db.add(ResourcePool(
                    location_id=community.id,
                    resource_type="food",
                    quantity=15.0,
                    max_quantity=35.0,
                    last_replenish_day=1,
                    replenish_interval=5,
                    replenish_amount=12.0,
                ))
            db.commit()
            print("  Resource pools initialized — food is scarce.")
        else:
            print("  Resource pools already initialized — skipping.")

        # ── Status scores ────────────────────────────────────────────────────
        chars = db.query(Character).filter(Character.alive == True).all()
        for char in chars:
            existing = db.query(StatusScore).filter(
                StatusScore.character_id == char.id
            ).first()
            if not existing:
                db.add(StatusScore(character_id=char.id, score=50.0))
        db.commit()
        print("  Status scores initialized at 50.")

        # ── Sim clock ────────────────────────────────────────────────────────
        if not db.query(SimClock).first():
            db.add(SimClock(
                current_day=1, current_tick=0, is_running=False,
                sim_year=0, sim_month=1, sim_day_of_month=1,
            ))
            db.commit()
            print("  Simulation clock initialized at Year 0, Day 1.")
        else:
            print("  Clock already initialized — skipping.")

        print()
        print("Caldwell is ready.")
        print("Run:  python main.py")
        print("Then open:  http://localhost:8080")
        print()
        print("Key design features:")
        print("  - Food is scarce — 42 units per 3 days for 30 people")
        print("  - First pressure event: Day 15 (food shortage)")
        print("  - All characters on DeepSeek")
        print("  - Characters scattered across city at start")
        print("  - Status economy active")
        print("  - Sexual behavior emergent from biology + attraction")

    finally:
        db.close()


def _generate_capability(core_drive: str, capability: str) -> int:
    """
    Generate capability scores with drive-correlated variance.
    Creates natural interdependence — no one is good at everything.
    Range: 1-10
    """
    # Drive-capability correlations
    drive_bonuses = {
        "strength": {
            "Survival": +2, "Power": +1, "Order": +0,
            "Curiosity": -1, "Knowledge": -1, "Connection": +0, "Comfort": -1,
        },
        "memory": {
            "Knowledge": +3, "Curiosity": +2, "Order": +1,
            "Connection": +0, "Power": +0, "Comfort": -1, "Survival": -1,
        },
        "persuasion": {
            "Power": +3, "Connection": +2, "Comfort": +1,
            "Survival": +0, "Order": +0, "Curiosity": -1, "Knowledge": -1,
        },
    }

    base = random.randint(3, 7)
    bonus = drive_bonuses.get(capability, {}).get(core_drive, 0)
    noise = random.randint(-1, 1)
    return max(1, min(10, base + bonus + noise))


if __name__ == "__main__":
    seed()
