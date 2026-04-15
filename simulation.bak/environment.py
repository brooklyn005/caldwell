"""
environment.py — periodic pressure events that force collective response.

These are the conditions under which governance, religion, and ideology
actually emerge. Without pressure, people have nothing to organize around.

Schedule:
- Day 15: First food shortage (3 days, replenishment fails)
- Day 30: Cold snap (outdoor locations uncomfortable, sleep needed)
- Day 45: Second resource stress event
- Day 60+: Random events every 15-25 days

Each event:
1. Modifies the world state (food, location comfort, etc.)
2. Injects awareness into character prompts
3. Creates a topic seed pressure that pushes conversations toward the problem
4. Gets logged as a significant event
"""
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, Location, EnvironmentEvent, ResourcePool
)

logger = logging.getLogger("caldwell.environment")

# Event schedule — the skeleton of Caldwell's history
SCHEDULED_EVENTS = [
    {
        "day": 15,
        "type": "food_shortage",
        "description": (
            "The food at the market does not appear today. "
            "The stalls are empty. Whatever mechanism fills them has stopped. "
            "No one knows if it will come back."
        ),
        "duration": 3,
        "severity": 1.5,
    },
    {
        "day": 30,
        "type": "cold_snap",
        "description": (
            "The temperature dropped overnight and hasn't recovered. "
            "The air has a bite that wasn't there before. "
            "Sleeping outside or in unheated spaces is genuinely uncomfortable now."
        ),
        "duration": 5,
        "severity": 1.0,
    },
    {
        "day": 45,
        "type": "food_shortage",
        "description": (
            "The food supply is reduced to half its normal amount today. "
            "There is food — but not enough for everyone. "
            "Someone will go hungry."
        ),
        "duration": 2,
        "severity": 1.0,
    },
    {
        "day": 60,
        "type": "abundance",
        "description": (
            "The market is overflowing today — far more food than usual has appeared. "
            "Three times the normal supply. No one can explain it. "
            "The question of what to do with surplus is suddenly real."
        ),
        "duration": 3,
        "severity": 0.5,
    },
]

# Random event pool for Day 60+
RANDOM_EVENTS = [
    {
        "type": "food_shortage",
        "description": "The food supply failed to appear today. The market is empty.",
        "duration": 2,
        "severity": 1.2,
    },
    {
        "type": "food_shortage",
        "description": "Only partial food appeared today — enough for perhaps half the people here.",
        "duration": 1,
        "severity": 0.8,
    },
    {
        "type": "weather_heat",
        "description": "The heat today is oppressive. Working outside is difficult. Sleep is harder.",
        "duration": 3,
        "severity": 0.7,
    },
    {
        "type": "abundance",
        "description": "Twice the normal food appeared today. More than anyone can eat immediately.",
        "duration": 2,
        "severity": 0.5,
    },
    {
        "type": "noise_at_night",
        "description": "Something made noise outside the city limits last night. No one saw what it was.",
        "duration": 1,
        "severity": 0.6,
    },
    {
        "type": "strange_discovery",
        "description": "Something new appeared in the city that wasn't there before. No one knows where it came from.",
        "duration": 1,
        "severity": 0.5,
    },
]


def check_and_fire_events(sim_day: int, db: Session) -> list[dict]:
    """
    Check if any events should fire today.
    Returns list of event dicts for broadcasting.
    """
    fired = []

    # Check scheduled events
    for event_def in SCHEDULED_EVENTS:
        if event_def["day"] == sim_day:
            # Check if already created
            existing = db.query(EnvironmentEvent).filter(
                EnvironmentEvent.event_type == event_def["type"],
                EnvironmentEvent.start_day == sim_day,
            ).first()
            if not existing:
                ev = _create_event(event_def, sim_day, db)
                fired.append(ev)

    # Random events after Day 60
    if sim_day > 60:
        # Check if there's an active event already
        active = db.query(EnvironmentEvent).filter(
            EnvironmentEvent.start_day <= sim_day,
            EnvironmentEvent.resolved == False,
        ).filter(
            (EnvironmentEvent.end_day == None) |
            (EnvironmentEvent.end_day >= sim_day)
        ).first()

        if not active:
            # Check when last random event was
            last_random = db.query(EnvironmentEvent).filter(
                EnvironmentEvent.start_day > 60
            ).order_by(EnvironmentEvent.start_day.desc()).first()

            last_day = last_random.start_day if last_random else 60
            days_since = sim_day - last_day
            min_interval = 15

            if days_since >= min_interval:
                # Increasing probability as time since last event grows
                prob = min(0.3, (days_since - min_interval) * 0.02)
                if random.random() < prob:
                    event_def = random.choice(RANDOM_EVENTS).copy()
                    event_def["day"] = sim_day
                    ev = _create_event(event_def, sim_day, db)
                    fired.append(ev)

    # Resolve expired events
    _resolve_expired(sim_day, db)

    return fired


def _create_event(event_def: dict, sim_day: int, db: Session) -> dict:
    """Create an event and apply its world-state effects."""
    duration = event_def.get("duration", 1)
    end_day = sim_day + duration - 1

    ev = EnvironmentEvent(
        event_type=event_def["type"],
        description=event_def["description"],
        start_day=sim_day,
        end_day=end_day,
        severity=event_def.get("severity", 1.0),
        resolved=False,
    )
    db.add(ev)
    db.flush()

    # Apply world-state effects
    _apply_event_effects(event_def["type"], event_def.get("severity", 1.0), db)

    db.commit()

    logger.info(
        f"  EVENT [{event_def['type']}] Day {sim_day}: "
        f"{event_def['description'][:60]}..."
    )

    return {
        "type": event_def["type"],
        "description": event_def["description"],
        "severity": event_def.get("severity", 1.0),
        "sim_day": sim_day,
        "end_day": end_day,
    }


def _apply_event_effects(event_type: str, severity: float, db: Session):
    """Apply mechanical effects of an event."""
    if event_type == "food_shortage":
        # Zero out or reduce food pools
        pools = db.query(ResourcePool).filter(
            ResourcePool.resource_type == "food"
        ).all()
        for pool in pools:
            if severity >= 1.5:
                pool.quantity = 0.0  # total failure
            else:
                pool.quantity = max(0.0, pool.quantity * (1.0 - severity * 0.5))
        db.commit()

    elif event_type == "abundance":
        # Double the food
        pools = db.query(ResourcePool).filter(
            ResourcePool.resource_type == "food"
        ).all()
        for pool in pools:
            pool.quantity = min(pool.max_quantity, pool.quantity * 2.5)
        db.commit()


def _resolve_expired(sim_day: int, db: Session):
    """Mark events as resolved when their end_day has passed."""
    expired = db.query(EnvironmentEvent).filter(
        EnvironmentEvent.resolved == False,
        EnvironmentEvent.end_day < sim_day,
    ).all()
    for ev in expired:
        ev.resolved = True
    if expired:
        db.commit()


def get_active_events(sim_day: int, db: Session) -> list[EnvironmentEvent]:
    """Returns currently active events."""
    return db.query(EnvironmentEvent).filter(
        EnvironmentEvent.start_day <= sim_day,
        EnvironmentEvent.resolved == False,
    ).filter(
        (EnvironmentEvent.end_day == None) |
        (EnvironmentEvent.end_day >= sim_day)
    ).all()


def get_environment_prompt(sim_day: int, db: Session) -> str | None:
    """Returns environmental context for character prompts."""
    active = get_active_events(sim_day, db)
    if not active:
        return None

    parts = []
    for ev in active:
        if ev.event_type == "food_shortage":
            parts.append(
                "THE FOOD HAS NOT APPEARED. "
                "The market is empty or nearly empty. "
                "This is not normal. This has not happened before — or if it has, "
                "it did not last this long. People are hungry and there is nothing to eat."
            )
        elif ev.event_type == "cold_snap":
            parts.append(
                "The cold has been here for days now. "
                "Sleeping outside is genuinely uncomfortable. "
                "The warmth of the residential buildings has become something people notice."
            )
        elif ev.event_type == "abundance":
            parts.append(
                "There is far more food than usual today — more than anyone can eat. "
                "The question of what to do with it is suddenly real. "
                "Who decides? Who gets to take more? Does anyone?"
            )
        elif ev.event_type == "weather_heat":
            parts.append(
                "The heat is oppressive today. Working is harder. Sleep last night was difficult."
            )
        elif ev.event_type == "noise_at_night":
            parts.append(
                "Something made noise outside the city last night. "
                "You don't know what it was. No one does."
            )
        elif ev.event_type == "strange_discovery":
            parts.append(
                "Something appeared in Caldwell that wasn't there before. "
                "No one knows where it came from or what it means."
            )

    return "\n".join(parts) if parts else None
