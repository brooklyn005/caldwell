"""
location_memory.py — tracks what each location becomes known for.

Places accumulate social meaning from repeated use and significant events.
Central Square becomes the accusation stage.
Riverside Park becomes the confession space.
The Chapel becomes where people go to be honest or afraid.
Workshop becomes masculine contest and apprenticeship.

This feeds back into scene selection (locations are chosen partly for their
social meaning) and into prompts (characters know what a place is for).
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Location, LocationMemory, Scene, CharacterRelationship,
    SignificantEvent, LocationClaim,
)

logger = logging.getLogger("caldwell.location_memory")

# Scene types → location meaning associations
_SCENE_TO_MEANING = {
    "argument":         "conflict_zone",
    "status_challenge": "power_space",
    "correction":       "accountability_space",
    "quiet_intimacy":   "intimate_space",
    "resentment":       "private_space",
    "gossip":           "social_space",
    "teaching":         "learning_space",
    "ritual":           "sacred_space",
    "distribution":     "resource_space",
    "preparation":      "labor_space",
    "return":           "communal_space",
    "ambient_meal":     "comfort_space",
    "ambient_labor":    "labor_space",
    "ambient_care":     "intimate_space",
}

# Mood associations
_MOOD_BY_SCENE_DENSITY = {
    "argument":         "tense",
    "status_challenge": "charged",
    "quiet_intimacy":   "intimate",
    "ritual":           "sacred",
    "distribution":     "contested",
    "teaching":         "calm",
    "gossip":           "social",
    "resentment":       "heavy",
    "preparation":      "purposeful",
    "return":           "communal",
}

# Privacy score by scene type
_PRIVACY_BY_SCENE = {
    "quiet_intimacy": 0.9,
    "resentment": 0.8,
    "argument": 0.3,
    "status_challenge": 0.2,
    "gossip": 0.6,
    "distribution": 0.1,
    "ritual": 0.5,
    "teaching": 0.4,
}


def update_location_memory_after_scene(
    scene_type: str,
    location: Location,
    participant_roster_ids: list[str],
    sim_day: int,
    db: Session,
    significant: bool = False,
    event_summary: str | None = None,
) -> None:
    """
    Called after each scene completes. Updates the location's memory.
    """
    mem = db.query(LocationMemory).filter(
        LocationMemory.location_id == location.id
    ).first()

    if not mem:
        mem = LocationMemory(
            location_id=location.id,
            first_recorded_day=sim_day,
        )
        db.add(mem)

    # Update scene counts
    counts = mem.scene_counts
    counts[scene_type] = counts.get(scene_type, 0) + 1
    mem.scene_counts_json = json.dumps(counts)

    # Update identity tags
    new_tag = _SCENE_TO_MEANING.get(scene_type)
    tags = mem.identity_tags
    if new_tag and new_tag not in tags:
        tags.append(new_tag)
        # Cap at 5 most recent/relevant tags
        mem.identity_tags = tags[-5:]

    # Update dominant mood from most common scene type
    most_common_scene = max(counts, key=counts.get)
    new_mood = _MOOD_BY_SCENE_DENSITY.get(most_common_scene)
    if new_mood:
        mem.dominant_mood = new_mood

    # Update privacy score (rolling average)
    scene_privacy = _PRIVACY_BY_SCENE.get(scene_type, 0.5)
    if mem.privacy_score is None:
        mem.privacy_score = scene_privacy
    else:
        mem.privacy_score = (mem.privacy_score * 0.8) + (scene_privacy * 0.2)

    # Charge level — intimate and conflict scenes charge a space
    if scene_type in ("quiet_intimacy", "argument", "status_challenge", "correction"):
        mem.charge_level = min((mem.charge_level or 0) + 0.1, 1.0)
    else:
        mem.charge_level = max((mem.charge_level or 0) - 0.05, 0.0)

    # Significant events
    if significant and event_summary:
        events = json.loads(mem.significant_events_json or '[]')
        events.append({"day": sim_day, "summary": event_summary})
        if len(events) > 20:
            events = events[-20:]
        mem.significant_events_json = json.dumps(events)
        mem.last_notable_event = event_summary
        mem.last_notable_day = sim_day

    mem.updated_at = __import__('datetime').datetime.utcnow()
    db.commit()


def get_location_memory_for_prompt(location: Location, db: Session) -> str:
    """
    Returns a natural-language string about what this location has become.
    Injected into scene prompts to give characters a sense of place history.
    """
    mem = db.query(LocationMemory).filter(
        LocationMemory.location_id == location.id
    ).first()

    if not mem or not mem.identity_tags:
        return ""

    tags = mem.identity_tags
    mood = mem.dominant_mood
    charge = mem.charge_level or 0.0
    notable = mem.last_notable_event
    notable_day = mem.last_notable_day

    # Build description
    parts = []

    # What kind of place has this become
    tag_phrases = {
        "conflict_zone":       "Arguments happen here. People have said things in this space they can't take back.",
        "power_space":         "This is where standing gets decided. People perform differently when they're here.",
        "accountability_space": "This is where you get called on things. People hold each other to account here.",
        "intimate_space":      "Private things happen here. The space holds confessions, closeness, lowered guards.",
        "private_space":       "People come here when they don't want to be seen. The privacy is understood.",
        "social_space":        "This is where the group gathers and talks. Reputation gets made and unmade here.",
        "learning_space":      "Teaching happens here. The gap between knowing and not-knowing is visible in this space.",
        "sacred_space":        "Something has made this place feel different from the others. People treat it carefully.",
        "resource_space":      "Food and goods move through here. Control of this space means something.",
        "labor_space":         "People work here. Sweat and effort have marked this space.",
        "communal_space":      "This is where people come back to each other. Returns and reunions live here.",
        "comfort_space":       "People relax here. The social armor comes down.",
    }

    for tag in tags[-3:]:  # Most recent 3 tags
        if tag in tag_phrases:
            parts.append(tag_phrases[tag])
            break  # Just the most relevant one

    # Mood
    mood_phrases = {
        "tense":      "The air in this space carries history. People are careful here.",
        "charged":    "There's weight to being here. People feel watched.",
        "intimate":   "Something about this space invites honesty.",
        "sacred":     "This space has become something. People feel it without saying so.",
        "contested":  "Control of this space is not settled. People assert themselves here.",
        "calm":       "This space has a quality of patience. People slow down when they enter.",
        "social":     "This is where the group exists as a group. You are never unobserved here.",
        "heavy":      "Something unresolved lives in this space. You can feel the weight when you enter.",
        "purposeful": "Work is what this space is for. People know why they're here.",
        "communal":   "This space belongs to everyone. That can feel like safety or like exposure.",
    }
    if mood and mood in mood_phrases:
        parts.append(mood_phrases[mood])

    # Recent notable event
    if notable and notable_day:
        parts.append(f"Something happened here recently — {notable.lower()}")

    # High charge warning
    if charge > 0.7:
        parts.append("This space is charged right now. What you say here will be felt.")

    if not parts:
        return ""

    return f"ABOUT THIS PLACE ({location.name}):\n" + " ".join(parts)


def get_location_identity_for_selection(location: Location, db: Session) -> dict:
    """
    Returns metadata used by scene selection to match scene types to locations.
    """
    mem = db.query(LocationMemory).filter(
        LocationMemory.location_id == location.id
    ).first()

    if not mem:
        return {"tags": [], "mood": None, "privacy": 0.5, "charge": 0.0}

    return {
        "tags": mem.identity_tags,
        "mood": mem.dominant_mood,
        "privacy": mem.privacy_score or 0.5,
        "charge": mem.charge_level or 0.0,
        "who_controls": mem.who_controls,
        "who_avoids": mem.who_avoids,
    }


def get_all_location_memories(db: Session) -> list[dict]:
    """For reader summary: returns all locations with their current identity."""
    mems = db.query(LocationMemory).all()
    result = []
    for mem in mems:
        loc = db.query(Location).filter(Location.id == mem.location_id).first()
        if not loc:
            continue
        if not mem.identity_tags and not mem.dominant_mood:
            continue
        result.append({
            "location": loc.name,
            "tags": mem.identity_tags,
            "mood": mem.dominant_mood,
            "charge": mem.charge_level or 0.0,
            "last_event": mem.last_notable_event,
        })
    return result
