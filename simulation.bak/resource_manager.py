"""
resource_manager.py — manages scarce resources in Caldwell.

Food is NOT infinite. It appears at Bayou Market every 3 sim days
in quantities that are slightly less than what everyone needs.
Characters who get there first eat. Characters who don't, go hungry.

This is the engine of social complexity. Distribution politics,
sharing, hoarding, alliances around food — all of this emerges
from the simple fact that there isn't always enough.

The scarcity is real but not catastrophic. Most days most people eat.
But the possibility of not eating is always present, and that possibility
changes everything about how people relate to each other.
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, ResourcePool, StatusScore,
    CharacterBiology, BehaviorWitness
)

logger = logging.getLogger("caldwell.resources")

# Food economics
FOOD_PER_CHARACTER_PER_DAY = 1.0
REPLENISH_INTERVAL = 1          # every day — food is not the constraint anymore
REPLENISH_AMOUNT = 90.0         # full replenishment daily — food is not the drama
MARKET_FOOD_MAX = 120.0         # generous capacity

# Status effects of resource behavior
STATUS_SHARE_BONUS = 4.0
STATUS_HOARD_PENALTY = 5.0
STATUS_HELP_BONUS = 3.0


def initialize_resources(db: Session):
    """Set up initial resource pools. Called once at first tick."""
    existing = db.query(ResourcePool).count()
    if existing > 0:
        return

    market = db.query(Location).filter(Location.name == "Bayou Market").first()
    community = db.query(Location).filter(Location.name == "Community Center").first()

    if market:
        pool = ResourcePool(
            location_id=market.id,
            resource_type="food",
            quantity=60.0,  # 2 days initial supply
            max_quantity=MARKET_FOOD_MAX,
            last_replenish_day=1,
            replenish_interval=REPLENISH_INTERVAL,
            replenish_amount=REPLENISH_AMOUNT,
        )
        db.add(pool)

    if community:
        pool2 = ResourcePool(
            location_id=community.id,
            resource_type="food",
            quantity=20.0,  # secondary smaller supply
            max_quantity=40.0,
            last_replenish_day=1,
            replenish_interval=5,
            replenish_amount=15.0,
        )
        db.add(pool2)

    db.commit()
    logger.info("Resource pools initialized — food scarcity active")


def initialize_status_scores(db: Session):
    """Give every character a starting status score."""
    chars = db.query(Character).filter(Character.alive == True).all()
    for char in chars:
        existing = db.query(StatusScore).filter(
            StatusScore.character_id == char.id
        ).first()
        if not existing:
            db.add(StatusScore(
                character_id=char.id,
                score=50.0,
                updated_day=0,
            ))
    db.commit()


def tick_resources(sim_day: int, db: Session) -> dict:
    """
    Advance resource pools by one day.
    Replenish food if interval has passed.
    Returns summary of resource state.
    """
    pools = db.query(ResourcePool).all()
    replenished = []
    shortages = []

    for pool in pools:
        days_since = sim_day - pool.last_replenish_day
        if days_since >= pool.replenish_interval:
            old_qty = pool.quantity
            pool.quantity = min(
                pool.max_quantity,
                pool.quantity + pool.replenish_amount
            )
            pool.last_replenish_day = sim_day
            loc = db.query(Location).filter(Location.id == pool.location_id).first()
            replenished.append({
                "location": loc.name if loc else "unknown",
                "added": pool.quantity - old_qty,
                "total": pool.quantity,
            })
            logger.info(
                f"  Food replenished at {loc.name if loc else '?'}: "
                f"+{pool.quantity - old_qty:.0f} units (total: {pool.quantity:.0f})"
            )

        if pool.quantity < 5.0:
            loc = db.query(Location).filter(Location.id == pool.location_id).first()
            shortages.append(loc.name if loc else "unknown")

    db.commit()
    return {"replenished": replenished, "shortages": shortages}


def consume_food(character: Character, location: Location, sim_day: int, db: Session) -> bool:
    """
    Attempt to consume food at current location.
    Returns True if character ate, False if no food available.
    Records sharing/hoarding behaviors for status system.
    """
    bio = db.query(CharacterBiology).filter(
        CharacterBiology.character_id == character.id
    ).first()
    if not bio:
        return False

    # Already satisfied — don't consume more
    if bio.hunger <= 2.0:
        return True

    pool = db.query(ResourcePool).filter(
        ResourcePool.location_id == location.id,
        ResourcePool.resource_type == "food",
    ).first()

    if not pool or pool.quantity <= 0:
        return False

    # Consume one unit
    pool.quantity = max(0.0, pool.quantity - FOOD_PER_CHARACTER_PER_DAY)
    bio.hunger = max(0.0, bio.hunger - 5.0)
    bio.last_ate_day = sim_day

    # Check if food is critically low — hoarding behavior becomes possible
    if pool.quantity < 10.0:
        # Character with high strength/survival could take more
        # This will be handled through action injection and conversation
        pass

    db.commit()
    return True


def get_resource_status(db: Session) -> dict:
    """Returns current resource state for prompts and UI."""
    pools = db.query(ResourcePool).all()
    total_food = sum(p.quantity for p in pools if p.resource_type == "food")
    chars = db.query(Character).filter(
        Character.alive == True,
        Character.is_infant == False,
    ).count()

    days_remaining = total_food / max(chars, 1) if chars > 0 else 0

    if days_remaining > 2:
        level = "sufficient"
        description = "Food is available. Not abundant, but there."
    elif days_remaining > 1:
        level = "low"
        description = "Food is running low. Not everyone will eat today."
    elif days_remaining > 0.3:
        level = "scarce"
        description = "Food is almost gone. People are going hungry."
    else:
        level = "none"
        description = "There is no food. The market is empty."

    return {
        "total_food": round(total_food, 1),
        "level": level,
        "description": description,
        "days_remaining": round(days_remaining, 1),
        "pools": [
            {
                "location_id": p.location_id,
                "quantity": round(p.quantity, 1),
                "last_replenish": p.last_replenish_day,
                "next_replenish": p.last_replenish_day + p.replenish_interval,
            }
            for p in pools
        ],
    }


def update_status_for_behavior(
    character: Character,
    behavior: str,
    delta: float,
    sim_day: int,
    db: Session,
):
    """Update a character's status score based on observed behavior."""
    import json
    status = db.query(StatusScore).filter(
        StatusScore.character_id == character.id
    ).first()
    if not status:
        status = StatusScore(character_id=character.id, score=50.0)
        db.add(status)
        db.flush()

    status.score = max(0.0, min(100.0, status.score + delta))
    status.updated_day = sim_day

    # Track behavior counts
    if behavior == "shared_food":
        status.times_shared_food += 1
    elif behavior == "hoarded":
        status.times_hoarded += 1
    elif behavior == "helped":
        status.times_helped += 1
    elif behavior == "deferred_to":
        status.times_deferred_to += 1

    # Update history
    try:
        history = json.loads(status.score_history or "[]")
        history.append([sim_day, round(status.score, 1)])
        if len(history) > 100:
            history = history[-100:]
        status.score_history = json.dumps(history)
    except Exception:
        pass

    db.commit()


def get_status_context(character: Character, db: Session) -> str | None:
    """Returns status context for character's prompt."""
    status = db.query(StatusScore).filter(
        StatusScore.character_id == character.id
    ).first()
    if not status:
        return None

    score = status.score

    if score >= 75:
        return (
            "People here tend to listen when you speak. "
            "You have done things that others noticed and respected. "
            "You carry some weight in this place."
        )
    elif score >= 60:
        return (
            "You have built some standing here through what you have done. "
            "Not everyone knows it but some do."
        )
    elif score <= 25:
        return (
            "You feel like people look past you here. "
            "Something you did — or didn't do — has cost you. "
            "You are aware of it even if you don't know exactly why."
        )
    elif score <= 40:
        return (
            "You sense that some people here don't quite trust you. "
            "You're not sure why or what you could do about it."
        )
    return None
