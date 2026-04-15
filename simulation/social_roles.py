"""
social_roles.py — tracks the emergence of durable public roles.

Characters begin as undifferentiated agents. Over time, their repeated
behaviors and social perceptions produce roles that others recognize.

Role examples:
- hunter: consistently goes out, comes back with food
- caretaker: consistently attends to others' needs
- judge: people bring disputes to them
- gossip_center: information flows through them
- truth_teller: says uncomfortable things, others expect it
- chronicler: records things, remembers everything
- reluctant_authority: people look to them without asking
- moral_voice: raises ethical objections
- teacher: transmits knowledge repeatedly
- peacemaker: resolves conflicts without escalating
- outsider: operates at the group's edge
- dangerous_flirt: introduces erotic tension
- practical_expert: goes-to person for physical problems

Roles affect:
- Who gets sought out for scenes
- How other characters speak to them
- What scenes they generate
- Their effective authority level
"""
import json
import logging
import random
from sqlalchemy.orm import Session
from database.models import (
    Character, SocialRole, BehavioralEvidence, BehavioralTendency,
    Scene, Memory, CharacterRelationship,
)

logger = logging.getLogger("caldwell.social_roles")

# Maps behavioral patterns to role labels
_BEHAVIORAL_ROLE_MAP = {
    # approach -> possible role
    "assertive": ["reluctant_authority", "truth_teller", "judge"],
    "collaborative": ["peacemaker", "caretaker", "teacher"],
    "vulnerable": ["confessor_magnet", "trusted_confidant"],
    "philosophical": ["moral_voice", "judge"],
    "nurturing": ["caretaker", "practical_teacher"],
    "challenging": ["truth_teller", "outsider"],
    "analytical": ["chronicler", "teacher"],
    "withdrawn": ["outsider", "watcher"],
    "playful": ["dangerous_flirt", "social_center"],
    "protective": ["guardian", "caretaker"],
}

# Role descriptions for prompt injection
_ROLE_DESCRIPTIONS = {
    "hunter":               "People expect you to go out and come back with something. It has become who you are.",
    "caretaker":            "People come to you when they are hurt or struggling. You have become the one who tends.",
    "judge":                "When disputes arise, people look to you to settle them. You didn't ask for this.",
    "gossip_center":        "Information moves through you. People tell you things, and you tell others. You are the node.",
    "truth_teller":         "You say the thing others won't. They've learned to expect it. Some resent it. Some need it.",
    "chronicler":           "You record things. People know you'll remember. That gives what you say a different weight.",
    "reluctant_authority":  "People look to you when they don't know what to do. You haven't asked for this. It happens anyway.",
    "moral_voice":          "You raise the question of whether something is right. Others have come to rely on that.",
    "teacher":              "You know things and you pass them on. People have noticed you do this and they come to you.",
    "peacemaker":           "When things get bad between people, you are the one who finds the path through. They know it.",
    "outsider":             "You operate at the edge of the group. It's not quite exclusion and not quite choice.",
    "dangerous_flirt":      "Your attention carries a charge. People feel it. Some seek it. Some avoid it.",
    "practical_expert":     "When something physical needs solving, people come to you. You've become the person who knows.",
    "confessor_magnet":     "People tell you things they don't tell others. You've become the keeper of private burdens.",
    "guardian":             "You put yourself between threats and the people who matter to you. That has become known.",
    "watcher":              "You observe more than you participate. People are aware of your watching.",
    "social_center":        "People want to be near you. You generate warmth and ease. Others orbit you.",
}


def update_social_roles(sim_day: int, db: Session) -> None:
    """Called every 5 days. Infers roles from behavioral patterns and scene history."""
    chars = db.query(Character).filter(
        Character.alive == True, Character.is_infant == False
    ).all()

    for char in chars:
        _infer_role(char, sim_day, db)

    db.commit()
    logger.info(f"  Social roles updated for {len(chars)} characters")


def _infer_role(char: Character, sim_day: int, db: Session) -> None:
    role_rec = db.query(SocialRole).filter(
        SocialRole.character_id == char.id
    ).first()
    if not role_rec:
        role_rec = SocialRole(character_id=char.id)
        db.add(role_rec)

    # Evidence from behavioral tendencies
    tendency = db.query(BehavioralTendency).filter(
        BehavioralTendency.character_id == char.id
    ).first()

    dominant_approach = tendency.dominant_approach if tendency else None
    approaches = tendency.approaches if tendency else {}

    # Evidence from personality
    traits = char.personality_traits
    drive = char.core_drive

    # Evidence from scene participation
    scenes = db.query(Scene).filter(
        Scene.sim_day <= sim_day,
    ).all()

    # Count scene types this character participated in
    scene_type_counts = {}
    for scene in scenes:
        ids = json.loads(scene.participant_ids_json or '[]')
        if char.id in ids:
            t = scene.scene_type
            scene_type_counts[t] = scene_type_counts.get(t, 0) + 1

    # Build candidate roles with confidence scores
    candidate_scores: dict[str, float] = {}

    # From behavioral approach
    if dominant_approach and dominant_approach in _BEHAVIORAL_ROLE_MAP:
        for role in _BEHAVIORAL_ROLE_MAP[dominant_approach]:
            candidate_scores[role] = candidate_scores.get(role, 0) + 0.3

    # From personality traits
    if "protective" in traits:
        candidate_scores["guardian"] = candidate_scores.get("guardian", 0) + 0.4
        candidate_scores["caretaker"] = candidate_scores.get("caretaker", 0) + 0.2
    if "nurturing" in traits or "warm" in traits:
        candidate_scores["caretaker"] = candidate_scores.get("caretaker", 0) + 0.4
    if "direct" in traits or "honest" in traits or "sharp" in traits:
        candidate_scores["truth_teller"] = candidate_scores.get("truth_teller", 0) + 0.4
    if "meticulous" in traits or "historian" in traits:
        candidate_scores["chronicler"] = candidate_scores.get("chronicler", 0) + 0.5
    if "philosophical" in traits:
        candidate_scores["moral_voice"] = candidate_scores.get("moral_voice", 0) + 0.3
    if "diplomatic" in traits:
        candidate_scores["peacemaker"] = candidate_scores.get("peacemaker", 0) + 0.4
    if "persuasive" in traits:
        candidate_scores["social_center"] = candidate_scores.get("social_center", 0) + 0.3
    if "observant" in traits or "perceptive" in traits:
        candidate_scores["watcher"] = candidate_scores.get("watcher", 0) + 0.2
    if "witty" in traits or "social" in traits:
        candidate_scores["social_center"] = candidate_scores.get("social_center", 0) + 0.3
    if "earthy" in traits or "self-sufficient" in traits:
        candidate_scores["practical_expert"] = candidate_scores.get("practical_expert", 0) + 0.4
    if "adventurous" in traits or "restless" in traits:
        candidate_scores["hunter"] = candidate_scores.get("hunter", 0) + 0.3

    # From scene participation history
    teaching_count = scene_type_counts.get("teaching", 0)
    if teaching_count >= 3:
        candidate_scores["teacher"] = candidate_scores.get("teacher", 0) + (teaching_count * 0.15)

    argument_count = scene_type_counts.get("argument", 0) + scene_type_counts.get("status_challenge", 0)
    if argument_count >= 4:
        candidate_scores["truth_teller"] = candidate_scores.get("truth_teller", 0) + 0.2
        candidate_scores["judge"] = candidate_scores.get("judge", 0) + 0.2

    intimacy_count = scene_type_counts.get("quiet_intimacy", 0)
    if intimacy_count >= 3 and not char.is_minor:
        candidate_scores["dangerous_flirt"] = candidate_scores.get("dangerous_flirt", 0) + 0.2
        candidate_scores["confessor_magnet"] = candidate_scores.get("confessor_magnet", 0) + 0.2

    gossip_count = scene_type_counts.get("gossip", 0)
    if gossip_count >= 4:
        candidate_scores["gossip_center"] = candidate_scores.get("gossip_center", 0) + (gossip_count * 0.1)

    # From drive
    drive_roles = {
        "Order": "judge",
        "Knowledge": "teacher",
        "Connection": "social_center",
        "Survival": "practical_expert",
        "Power": "reluctant_authority",
        "Meaning": "moral_voice",
        "Grief": "confessor_magnet",
    }
    if drive in drive_roles:
        role = drive_roles[drive]
        candidate_scores[role] = candidate_scores.get(role, 0) + 0.2

    # Age modifiers
    age = char.age
    if age >= 40:
        candidate_scores["reluctant_authority"] = candidate_scores.get("reluctant_authority", 0) + 0.3
        candidate_scores["moral_voice"] = candidate_scores.get("moral_voice", 0) + 0.2
    if age <= 16:
        # Young characters don't hold authority roles
        for r in ["reluctant_authority", "judge", "moral_voice"]:
            candidate_scores[r] = candidate_scores.get(r, 0) - 0.5

    # Pick top role
    if candidate_scores:
        best_role = max(candidate_scores, key=candidate_scores.get)
        best_score = candidate_scores[best_role]

        # Only assign if there's enough evidence
        if best_score >= 0.3:
            # Build confidence as a rolling average
            old_confidence = role_rec.role_confidence or 0.0
            if role_rec.primary_role == best_role:
                # Reinforcement
                role_rec.role_confidence = min(old_confidence + 0.08, 1.0)
                role_rec.public_visibility = min((role_rec.public_visibility or 0) + 0.05, 1.0)
                role_rec.last_reinforced_day = sim_day
            else:
                # New or shifting role — start at lower confidence
                if best_score > old_confidence + 0.3:  # Strong evidence for change
                    role_rec.primary_role = best_role
                    role_rec.role_confidence = min(best_score * 0.6, 0.7)
                    role_rec.emerged_day = sim_day
                    role_rec.last_reinforced_day = sim_day
                    logger.info(
                        f"  ROLE: {char.given_name or char.roster_id} → {best_role} "
                        f"(confidence {role_rec.role_confidence:.2f})"
                    )

            # Secondary role
            candidates_minus_best = {k: v for k, v in candidate_scores.items() if k != best_role}
            if candidates_minus_best:
                second_role = max(candidates_minus_best, key=candidates_minus_best.get)
                if candidates_minus_best[second_role] >= 0.25:
                    role_rec.secondary_role = second_role

            # Public reputation phrase
            if role_rec.primary_role and role_rec.role_confidence >= 0.4:
                role_rec.public_reputation = _ROLE_DESCRIPTIONS.get(role_rec.primary_role, "")


def get_role_for_prompt(char: Character, db: Session) -> str:
    """Returns the character's social role as a prompt injection."""
    role_rec = db.query(SocialRole).filter(
        SocialRole.character_id == char.id
    ).first()

    if not role_rec or not role_rec.primary_role:
        return ""
    if role_rec.role_confidence < 0.3 or role_rec.public_visibility < 0.2:
        return ""

    desc = _ROLE_DESCRIPTIONS.get(role_rec.primary_role, "")
    if not desc:
        return ""

    return f"YOUR ROLE HERE:\n{desc}"


def get_all_roles_for_reader(db: Session) -> list[dict]:
    """Returns all character roles for the reader panel."""
    roles = db.query(SocialRole).filter(
        SocialRole.primary_role.isnot(None),
        SocialRole.role_confidence >= 0.3,
    ).all()

    result = []
    for role_rec in roles:
        char = db.query(Character).filter(
            Character.id == role_rec.character_id, Character.alive == True
        ).first()
        if not char:
            continue
        result.append({
            "name": char.given_name or char.roster_id,
            "role": role_rec.primary_role,
            "confidence": role_rec.role_confidence,
            "public_visibility": role_rec.public_visibility,
            "description": role_rec.public_reputation or "",
        })

    return sorted(result, key=lambda x: -x["confidence"])
