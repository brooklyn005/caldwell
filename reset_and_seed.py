"""
reset_and_seed.py — wipe the database and start fresh.

Run this when you want a clean simulation with the updated architecture.
Drops all tables, recreates schema, seeds 17 characters + locations.

Usage:
    python3 reset_and_seed.py
    python3 reset_and_seed.py --confirm   (skips the prompt)
"""
import sys
import os
import random

sys.path.insert(0, os.path.dirname(__file__))


def reset_and_seed(skip_confirm: bool = False):
    if not skip_confirm:
        print("=" * 60)
        print("WARNING: This will DELETE all existing simulation data.")
        print("=" * 60)
        answer = input("Type 'yes' to continue: ").strip().lower()
        if answer != "yes":
            print("Aborted.")
            return

    print("\nResetting Caldwell...")

    from database.db import engine, SessionLocal, init_db
    from database.models import Base

    # Drop everything and rebuild
    print("  Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("  Recreating schema...")
    Base.metadata.create_all(bind=engine)
    print("  Schema ready.\n")

    # Seed fresh
    from database.models import (
        Character, Location, SimClock, ResourcePool, StatusScore,
    )
    from data.characters_data import CHARACTERS
    from data.locations_data import LOCATIONS

    db = SessionLocal()

    try:
        # ── Locations ────────────────────────────────────────────────────────
        print(f"Creating {len(LOCATIONS)} locations...")
        for loc_data in LOCATIONS:
            loc = Location(**loc_data)
            db.add(loc)
        db.commit()
        print("  Done.\n")

        # ── Characters ───────────────────────────────────────────────────────
        print(f"Creating {len(CHARACTERS)} characters (all on DeepSeek)...")

        start_locations = db.query(Location).all()
        loc_weights = {
            "Central Square": 5,
            "Bayou Market": 5,
            "Riverside Park": 4,
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
            # Pull out fields that aren't Column attributes
            traits = char_data.get("personality_traits", [])
            char_data_clean = {
                k: v for k, v in char_data.items()
                if k not in ("personality_traits", "private_belief", "fear")
            }

            strength = _generate_capability(char_data_clean.get("core_drive"), "strength")
            memory = _generate_capability(char_data_clean.get("core_drive"), "memory")
            persuasion = _generate_capability(char_data_clean.get("core_drive"), "persuasion")

            start_loc = random.choice(weighted_locs)

            char = Character(
                **char_data_clean,
                ai_model="deepseek",
                current_location_id=start_loc.id,
                strength_score=strength,
                memory_score=memory,
                persuasion_score=persuasion,
            )
            char.personality_traits = traits
            db.add(char)

        db.commit()

        all_chars = db.query(Character).order_by(Character.roster_id).all()
        for c in all_chars:
            minor_tag = " [MINOR]" if c.is_minor else ""
            loc = db.query(Location).filter(Location.id == c.current_location_id).first()
            print(
                f"  {c.roster_id} {(c.given_name or '?'):<10} age={c.age:<3} "
                f"{c.core_drive:<14} "
                f"str={c.strength_score} mem={c.memory_score} per={c.persuasion_score} "
                f"@ {(loc.name if loc else '?')}{minor_tag}"
            )
        print()

        # ── Resource pools ───────────────────────────────────────────────────
        print("Initializing resource pools (17-person food balance)...")
        market = db.query(Location).filter(Location.name == "Bayou Market").first()
        community = db.query(Location).filter(Location.name == "Community Center").first()

        alive_count = db.query(Character).filter(Character.alive == True).count()

        if market:
            # ~2.5 days of food at start — scarce but not immediately critical
            initial = round(alive_count * 2.5)
            max_q = round(alive_count * 5.0)
            replenish = round(alive_count * 1.8)   # slight deficit over 3 days
            db.add(ResourcePool(
                location_id=market.id,
                resource_type="food",
                quantity=float(initial),
                max_quantity=float(max_q),
                last_replenish_day=1,
                replenish_interval=3,
                replenish_amount=float(replenish),
            ))
        if community:
            db.add(ResourcePool(
                location_id=community.id,
                resource_type="food",
                quantity=float(round(alive_count * 0.5)),
                max_quantity=float(round(alive_count * 2.0)),
                last_replenish_day=1,
                replenish_interval=5,
                replenish_amount=float(round(alive_count * 0.7)),
            ))
        db.commit()
        print(f"  Market: {initial} units initial, replenishes {replenish} every 3 days.")
        print(f"  ({alive_count} people — food is perpetually close to tight.)\n")

        # ── Status scores ────────────────────────────────────────────────────
        chars = db.query(Character).filter(Character.alive == True).all()
        for char in chars:
            db.add(StatusScore(character_id=char.id, score=50.0))
        db.commit()
        print("  Status scores initialized at 50.\n")

        # ── Sim clock ────────────────────────────────────────────────────────
        db.add(SimClock(
            current_day=1, current_tick=0, is_running=False,
            sim_year=0, sim_month=1, sim_day_of_month=1,
        ))
        db.commit()
        print("  Simulation clock: Year 0, Day 1.\n")

        print("=" * 60)
        print("Caldwell is ready for a fresh run.")
        print(f"  {alive_count} characters | all on DeepSeek")
        print("  Daily Composition Engine active")
        print("  Consequence Engine active")
        print("  Silent Action Layer active")
        print("  Transient State tracking active")
        print("  Reader Summary (daybook) active")
        print()
        print("Run:   python3 main.py")
        print("Open:  http://localhost:8080")
        print("=" * 60)

    finally:
        db.close()


def _generate_capability(core_drive: str, capability: str) -> int:
    """
    Seeded capability scores with drive-correlated variance.
    Range 1-10. Creates natural interdependence.
    """
    drive_bonuses = {
        "strength": {
            "Survival": +2, "Power": +1, "Dominance": +2, "Order": +0,
            "Curiosity": -1, "Knowledge": -1, "Connection": +0,
            "Comfort": -1, "Grief": -1, "Belonging": +0,
            "Meaning": -1, "Purity": +0, "Envy": +0, "Tribalism": +1,
        },
        "memory": {
            "Knowledge": +3, "Curiosity": +2, "Order": +1, "Envy": +1,
            "Connection": +0, "Power": +0, "Dominance": -1,
            "Comfort": -1, "Survival": -1, "Grief": +0,
            "Belonging": +0, "Meaning": +1, "Purity": +0, "Tribalism": -1,
        },
        "persuasion": {
            "Power": +3, "Connection": +2, "Comfort": +1, "Tribalism": +2,
            "Belonging": +2, "Survival": +0, "Order": +0, "Dominance": +1,
            "Curiosity": -1, "Knowledge": -1, "Grief": -1,
            "Meaning": +0, "Purity": -1, "Envy": -1,
        },
    }

    base = random.randint(3, 7)
    bonus = drive_bonuses.get(capability, {}).get(core_drive or "", 0)
    noise = random.randint(-1, 1)
    return max(1, min(10, base + bonus + noise))


if __name__ == "__main__":
    skip = "--confirm" in sys.argv
    reset_and_seed(skip_confirm=skip)
