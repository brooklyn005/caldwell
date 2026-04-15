"""
topic_seeds.py — generates a substantive topic for each character
to bring into a conversation.

Priority order:
0.  Critical biological override (hunger > 8.5, first menstruation)
0.5 Recent dramatic events (experience/observation memories < 2 days, weight >= 0.7)
1.  Food cooldown check — skip food if discussed recently and not critical
2.  Social complexity — weighted toward civilization-building topics
3.  Inception memories
4.  Body/desire
5.  Recent physical action (40% prob)
6.  Drive framework
7.  Drive fallback
"""
import random
from database.models import Character, Memory
from sqlalchemy.orm import Session

DRIVE_FRAMEWORKS = {
    "Curiosity": [
        "You have been trying to understand {memory_fragment} and the question won't leave you alone.",
        "Something doesn't add up: {memory_fragment}. You want to talk it through.",
        "You've been observing {memory_fragment} and want to know what someone else makes of it.",
        "A question has been forming for days: why does {memory_fragment}? You need to think out loud.",
    ],
    "Knowledge": [
        "You've noticed a pattern — {memory_fragment} — and you want to test whether it holds.",
        "You think you've figured something out about {memory_fragment}. You want to see if it sounds right.",
        "Something you observed about {memory_fragment} has been nagging at you.",
        "You've been tracking {memory_fragment} and you think it means something.",
    ],
    "Connection": [
        "You've been thinking about {memory_fragment} and wondering how the other person felt about it.",
        "Something happened — {memory_fragment} — and you haven't been able to let it go.",
        "You want to know this person better. You've been thinking about {memory_fragment}.",
        "You felt something shift after {memory_fragment} and you want to understand if others felt it too.",
    ],
    "Power": [
        "You've been watching how decisions get made here — {memory_fragment} — and it bothers you.",
        "Something about {memory_fragment} showed you who actually has influence in this place.",
        "You have an idea about {memory_fragment} and you want to get someone on board.",
        "You've been thinking about {memory_fragment} and what it would take to change it.",
    ],
    "Order": [
        "There's a problem that needs solving — {memory_fragment} — and you've been working out an approach.",
        "You've been thinking about how to organize {memory_fragment} more effectively.",
        "Something about {memory_fragment} is inefficient and it has been bothering you.",
        "You want to propose something about {memory_fragment}. You need someone to think it through with.",
    ],
    "Comfort": [
        "You've been uneasy about {memory_fragment} and you want to know if others feel the same.",
        "Something about {memory_fragment} made you feel less safe than before.",
        "You've been wondering if {memory_fragment} means things here are changing.",
        "You found something that brought you comfort — {memory_fragment} — and you want to share it.",
    ],
    "Survival": [
        "You've been assessing {memory_fragment} and trying to decide if it's a threat.",
        "You want to know where this person stands on {memory_fragment} before you decide how much to trust them.",
        "Something about {memory_fragment} made you reassess who is safe here.",
        "You've been thinking about {memory_fragment} as a possible resource or risk.",
    ],
}

DRIVE_FALLBACK_SEEDS = {
    "Curiosity": "You've been watching how people in this place have changed over time. Something has shifted that you can't name yet.",
    "Knowledge": "You've been mapping in your head which people tend to be where, and when. A pattern is forming.",
    "Connection": "You've been thinking about who here you actually know versus who you've just been near. The distinction feels important.",
    "Power": "You've been noticing who speaks and who listens when more than two people are together. It's more consistent than you'd expect.",
    "Order": "There's no real system for how things work here yet. You've been thinking about what would help.",
    "Comfort": "You've been wondering whether what you have here is stable, or whether something could take it away.",
    "Survival": "You've been thinking about who you would trust if things went badly here. Your list is short.",
}

DEPTH_ENCOURAGERS = [
    "Don't just react — explore this together. Ask what the other person actually thinks.",
    "Take this somewhere real. Share what you actually feel or believe, not just what's safe.",
    "This is a chance to think out loud with someone. Go deeper than small talk.",
    "Ask a question you genuinely don't know the answer to.",
    "Say something you haven't said to anyone else yet.",
]

GOVERNANCE_TOPICS = [
    (
        "This place has been running without anyone deciding how it runs. "
        "You've been noticing the decisions that get made anyway — by default, by habit, "
        "by whoever speaks first. You want to talk about whether that's working "
        "or whether something needs to be said out loud."
    ),
    (
        "When someone here does something that most people don't like, "
        "nothing formally happens. But something does happen — people talk, "
        "people avoid, people remember. You've been thinking about what that is "
        "and whether it's enough."
    ),
    (
        "You've been thinking about who gets listened to here and who doesn't. "
        "It's not random. There's a pattern. You want to see if the person "
        "you're with has noticed it too."
    ),
    (
        "If two people here genuinely disagreed about something that affected everyone, "
        "how would it get resolved? You've been working through this question "
        "and you haven't come to a satisfying answer."
    ),
    (
        "You've started to notice that some things in this place are treated as "
        "belonging to everyone and some things are treated as belonging to whoever "
        "uses them most. No one decided this. It just happened. "
        "You want to talk about whether that's right."
    ),
]

# ── NEW: Civilization building topics ─────────────────────────────────────────
CIVILIZATION_BUILDING_TOPICS = [
    (
        "You've been thinking about what you are actually good at here — "
        "what you can do that others can't, or do better. "
        "You've noticed others have things they're good at too. "
        "You want to talk about whether that matters, whether it should be organized, "
        "whether roles should exist here or just emerge on their own."
    ),
    (
        "Something you built or made or fixed is still here. Still working. "
        "You've been sitting with what it feels like to make something that lasts. "
        "You want to know if the person you're with has felt that — "
        "or if they've thought about making something permanent."
    ),
    (
        "You've been here long enough that this is no longer just surviving. "
        "You are past the immediate. You want to talk about what comes next — "
        "not tomorrow, but further. What does this place look like in a year? "
        "What would make it better? What would make it worse?"
    ),
    (
        "There are things here that break down — things that need maintaining, "
        "fixing, tending. No one decided who does that. Some people just do it. "
        "You've been thinking about whether that's fair and whether "
        "it should be different."
    ),
    (
        "You've been thinking about what gets passed on here. "
        "If a child grows up in this place, what will they know? "
        "What will they believe? Who will teach them? "
        "You haven't talked about this with anyone yet but it's been with you."
    ),
    (
        "There are things people have learned here — practical things, "
        "real things that help. You've been thinking about how that knowledge "
        "moves from one person to another, and whether it could be "
        "shared more deliberately."
    ),
    (
        "You've been thinking about conflict — not an argument you had, "
        "but what happens when two people here truly want different things "
        "and neither is wrong. How does that get resolved? "
        "What does resolution even look like without rules?"
    ),
    (
        "Some people here seem to have more than others. Not dramatically — "
        "but consistently. More food, more space, more say in things. "
        "You've been watching whether anyone else has noticed "
        "and whether it matters."
    ),
]

# ── NEW: Food as governance — reframes food from anxiety to system question ───
FOOD_AS_GOVERNANCE_TOPICS = [
    (
        "The food appears and disappears and no one controls it. "
        "But people do control who gets to it first, who waits, who goes without. "
        "That control is real even if it's not formal. "
        "You want to talk about whether that's the system you want to live in."
    ),
    (
        "You've been watching how food moves here — who takes, who shares, "
        "who remembers when they got less. There's a kind of fairness emerging "
        "or not emerging. You want to talk about what it should look like."
    ),
    (
        "You've been thinking about what people owe each other when there isn't enough. "
        "Not as a rule — just as a question. If you had more than you needed, "
        "and someone else had none, what would you do? "
        "What do you think others would do?"
    ),
    (
        "Food keeps appearing and you've stopped wondering where it comes from. "
        "What you haven't stopped wondering about is what would happen if it stopped. "
        "How long would things hold together? "
        "Who here would you trust in that situation?"
    ),
]

RELATIONSHIP_COMPLEXITY_TOPICS = [
    (
        "Something shifted between you and someone else in this place — "
        "you can't point to the exact moment but the relationship is different now. "
        "You've been trying to understand what changed and whether it matters."
    ),
    (
        "There are people here you trust and people you don't. "
        "You've been thinking about how you know which is which — "
        "what it is that makes you trust someone or not. "
        "The answer is more complicated than you expected."
    ),
    (
        "You've been thinking about what it means to owe someone something here. "
        "Not in a formal way — there are no rules about this. "
        "But you feel it anyway. Someone has done something for you "
        "and you haven't figured out what you owe them back."
    ),
    (
        "Two people you know well don't get along. "
        "You've been watching it and thinking about whether you're supposed to do something. "
        "You're not sure where the line is between your business and not your business."
    ),
    (
        "You've been close to someone here in a way you didn't expect. "
        "It happened gradually and now you're not sure what it means "
        "or what they think it means. You need to talk about it "
        "— maybe not to them, but to someone."
    ),
]

MEANING_AND_PROVISION_TOPICS = [
    (
        "You've been here long enough now to have a sense of what this place is. "
        "But you don't know what it's for. Whether it's for anything. "
        "Whether that question even makes sense. "
        "It's been sitting with you and you want to think it through with someone."
    ),
    (
        "You've been thinking about what you want your life here to be. "
        "Not just survival — you're past that. Something more specific. "
        "What you want to have built or understood or felt. "
        "You're not sure things will change but you act as if they might."
    ),
    (
        "Some people here seem to have settled into this place. "
        "Others still seem like they're waiting for something. "
        "You've been thinking about which one you are and why."
    ),
    (
        "You've been thinking about what makes a day here feel worth something. "
        "Not just getting through it — worth something. "
        "You haven't found a clean answer but you've found the edges of it."
    ),
]

IDENTITY_AND_NORMS_TOPICS = [
    (
        "There are things people do here that nobody discussed but everyone seems to "
        "have agreed to. You've been thinking about one of them — "
        "whether you actually agreed to it or just went along. "
        "There's a difference and you've been sitting with it."
    ),
    (
        "You have a name now — most people here do. "
        "You've been thinking about what it means that names happened, "
        "how they happened, and what you would have called yourself "
        "if you'd chosen before anyone else had a chance to."
    ),
    (
        "You've been thinking about what kind of person you are here "
        "compared to what kind of person you might have been somewhere else. "
        "This place has made you a specific version of yourself. "
        "You're not sure if you chose it."
    ),
    (
        "Something happened here that revealed what people actually value "
        "when they have to choose. Not what they say they value — "
        "what they actually do when it matters. "
        "You've been thinking about what that revealed about someone specific."
    ),
]

SEXUALITY_AND_BODY_TOPICS = [
    (
        "What does it mean to want someone? You have felt it — "
        "a pull toward a specific person that is not about talking or thinking. "
        "You don't know if others feel this. You don't know if it is "
        "allowed or what allowed even means here."
    ),
    (
        "Your body does things on its own sometimes — feelings, "
        "reactions, responses to other people's presence. "
        "You have been alone with your body and learned things about it. "
        "You wonder if the person you're with knows these things about themselves too."
    ),
    (
        "No one has talked about what happens between people's bodies here. "
        "It has happened or you have thought about it happening. "
        "There is no word for it, no agreement about what it means "
        "or what it makes two people to each other afterward."
    ),
    (
        "You have been near someone without clothing — yourself or them — "
        "and there was no framework for what that meant. "
        "In this place the body is just the body. "
        "You are still deciding what you think about that."
    ),
]


def get_recent_meaningful_memory(character: Character, db: Session) -> str | None:
    mem = (
        db.query(Memory)
        .filter(
            Memory.character_id == character.id,
            Memory.emotional_weight >= 0.5,
            Memory.memory_type.in_(["conversation", "observation", "inception", "feeling"]),
        )
        .order_by(Memory.emotional_weight.desc(), Memory.sim_day.desc())
        .first()
    )
    if not mem:
        return None
    content = mem.content.strip()
    if content.startswith("["):
        bracket_end = content.find("]")
        if bracket_end >= 0:
            content = content[bracket_end + 1:].strip()
    return content[:70] if content else None


def _get_sim_day(db: Session) -> int:
    from database.models import TickLog
    last = db.query(TickLog).order_by(TickLog.sim_day.desc()).first()
    return last.sim_day if last else 1


def _get_relationship_topic(character: Character, db: Session) -> str | None:
    from database.models import CharacterRelationship

    rels = (
        db.query(CharacterRelationship)
        .filter(CharacterRelationship.from_character_id == character.id)
        .order_by(CharacterRelationship.familiarity.desc())
        .limit(8)
        .all()
    )
    if not rels:
        return None

    for rel in rels:
        other = db.query(Character).filter(Character.id == rel.to_character_id).first()
        if not other:
            continue
        other_name = other.given_name or other.physical_description[:35]
        fam = rel.familiarity or 0
        trust = rel.trust_level or 0

        if fam > 0.5 and trust < 0.15:
            return (
                f"You know {other_name} well — maybe better than anyone else here. "
                f"But something sits uneasy between you. "
                f"You haven't named it but you feel it every time you're in the same space. "
                f"You've been trying to decide if it's worth bringing into the open."
            )

        if fam > 0.6 and trust > 0.2 and rel.interaction_count > 15:
            return (
                f"You and {other_name} have spent more time together than almost anyone else here. "
                f"You've been thinking about what that means — "
                f"what it makes the two of you to each other, "
                f"and whether there's a word for it yet in this place."
            )

        if fam > 0.3 and rel.interaction_count > 8:
            return (
                f"You've talked with {other_name} enough to know something about them "
                f"but you're not sure what you are to each other. "
                f"You've been sitting with that uncertainty."
            )

    return None


def _food_discussed_recently(character: Character, sim_day: int, db: Session) -> bool:
    """Returns True if character discussed food in the last 3 sim days."""
    recent = (
        db.query(Memory)
        .filter(
            Memory.character_id == character.id,
            Memory.sim_day >= sim_day - 3,
            Memory.memory_type.in_(["conversation", "action"]),
        )
        .all()
    )
    food_words = {"food", "hungry", "hunger", "eat", "eating", "market", "starving"}
    for mem in recent:
        content_lower = (mem.content or "").lower()
        if any(w in content_lower for w in food_words):
            return True
    return False



def generate_activity_topic_seed(
    character: Character,
    other: Character,
    action_verb: str,
    role: str,
    db: Session,
) -> str:
    """
    Generate a topic seed grounded in a physical activity that is happening.

    role: "actor" (doing the work) or "observer" (watching/arriving)
    """
    other_name = other.given_name or other.physical_description[:30]

    ACTOR_SEEDS = {
        "cook": [
            f"Your hands are in the work. The food is taking shape. {other_name} is here — watching or helping. "
            f"You could stay quiet and just cook, or you could say something. What's on your mind while you work?",
        ],
        "hunt": [
            f"You just got back. The effort is still on you — in your body, in your mood. "
            f"{other_name} is here. There's something real to talk about.",
            f"You're going out to hunt. The preparation is happening — assessing, checking. "
            f"{other_name} is nearby. Something might get said before you go.",
        ],
        "build": [
            f"Your hands are in it. The work is slow and physical and real. "
            f"{other_name} is here — maybe helping, maybe watching. "
            f"Talk while you work. What's actually on your mind?",
        ],
        "fish": [
            f"The line is in the water. The waiting is part of the work. "
            f"{other_name} is here. The quiet gives people room to say things.",
        ],
        "forage": [
            f"You're moving through the area, eyes on the ground. {other_name} is with you. "
            f"The work is quiet but your mind isn't. What's in it?",
        ],
        "patrol": [
            f"You're walking the perimeter — alert, deliberate. {other_name} is with you. "
            f"Movement gives people room to talk. What comes up?",
        ],
        "repair": [
            f"The broken thing is in front of you and you're working on it. "
            f"{other_name} is nearby. This is the kind of work that makes people talk.",
        ],
        "teach": [
            f"You're trying to pass something on to {other_name}. "
            f"Knowledge you have that they don't. The transfer is happening right now.",
        ],
        "tend": [
            f"The maintenance work is in your hands. {other_name} is here. "
            f"This kind of work gives people time to think and time to talk.",
        ],
        "gather": [
            f"You're distributing — making decisions about what goes where and to whom. "
            f"{other_name} is here watching the decisions get made.",
        ],
    }

    OBSERVER_SEEDS = {
        "cook": [
            f"{other_name} is cooking. You are watching. The smell of it is real. "
            f"You have something to say or you're deciding whether to say it.",
        ],
        "hunt": [
            f"{other_name} just got back from hunting. The effort shows. "
            f"You're here. There's a real thing to talk about.",
        ],
        "build": [
            f"{other_name} is building something. Their hands are busy. "
            f"You stopped to watch or you came to help. Either way you're here now.",
        ],
        "fish": [
            f"{other_name} is fishing. You came by. The quiet is real. "
            f"You could stay quiet too, or you could say something.",
        ],
        "patrol": [
            f"{other_name} is back from walking the perimeter. "
            f"There's something specific in how they hold themselves. You want to know what they found.",
        ],
        "teach": [
            f"{other_name} is teaching you something right now. "
            f"The knowledge is moving from them to you. What do you actually want to know?",
        ],
    }

    seeds = ACTOR_SEEDS if role == "actor" else OBSERVER_SEEDS
    options = seeds.get(action_verb, [
        f"The work is happening — {action_verb}ing. {other_name} is here. "
        f"What do you say while your hands are busy?"
    ])

    import random as _random
    return _random.choice(options)



def generate_topic_seed(character: Character, db: Session) -> str:
    from database.models import Location
    from simulation.biology import get_or_create_biology

    drive = character.core_drive
    frameworks = DRIVE_FRAMEWORKS.get(drive, DRIVE_FRAMEWORKS["Curiosity"])
    bio = get_or_create_biology(character, db)
    loc = db.query(Location).filter(Location.id == character.current_location_id).first()
    loc_name = loc.name if loc else ""
    sim_day = _get_sim_day(db)

    # ── Priority 0: Critical biological override ──────────────────────────────
    if (character.gender == 'F' and bio.menstrual_phase == "menstruation"
            and bio.first_menstruation_occurred and not bio.menstruation_known):
        return (
            "Something is happening in your body that you do not understand and it is frightening. "
            "There is blood and pain. You do not have words for this. "
            "You need to tell someone or find out if something is wrong with you. "
            "This is the only thing on your mind."
        )

    if bio.hunger > 9.8:  # Essentially never fires — food is not a conversation topic
        return (
            f"You are in genuine physical distress from hunger. "
            f"Your body can barely focus on anything else."
        )

    if bio.fatigue > 8.5:
        return (
            f"Your body is collapsing from exhaustion. You are at {loc_name}. "
            f"The tiredness is making you say things you might not otherwise say."
        )

    # Bathroom urgency handled by movement, not conversation topic

    if (character.gender == 'F' and bio.menstrual_phase == "menstruation"
            and bio.menstruation_known and random.random() < 0.35):
        return (
            "The bleeding has returned — the monthly thing your body does. "
            "The cramping is real today and affecting everything. "
            "You may want to talk about it or ignore it, but it is present."
        )

    # ── Priority 0.5: Recent dramatic events ─────────────────────────────────
    try:
        recent_dramatic = (
            db.query(Memory)
            .filter(
                Memory.character_id == character.id,
                Memory.memory_type.in_(["experience", "observation"]),
                Memory.emotional_weight >= 0.7,
                Memory.sim_day >= sim_day - 2,
            )
            .order_by(Memory.emotional_weight.desc(), Memory.sim_day.desc())
            .first()
        )
        if recent_dramatic and random.random() < 0.75:
            content_preview = recent_dramatic.content[:120].rstrip(".")
            if recent_dramatic.memory_type == "experience":
                return (
                    f"Something happened very recently that you are still processing. "
                    f"{content_preview}. "
                    f"It is still with you — in your body, in your thoughts. "
                    f"You haven't fully decided what it means yet."
                )
            else:
                return (
                    f"You saw something recently that you haven't been able to stop "
                    f"thinking about. {content_preview}. "
                    f"You don't know what you're supposed to do with it — "
                    f"whether to say something, ask something, or just let it sit."
                )
    except Exception:
        pass

    # ── Priority 1: Social complexity (Day 30+) ───────────────────────────────
    if sim_day >= 30:
        social_roll = random.random()

        if social_roll < 0.30:
            rel_topic = _get_relationship_topic(character, db)
            if rel_topic:
                return rel_topic

        elif social_roll < 0.50:
            return random.choice(GOVERNANCE_TOPICS)

        elif social_roll < 0.68:
            # Civilization building — new, high weight
            return random.choice(CIVILIZATION_BUILDING_TOPICS)

        elif social_roll < 0.76:
            return random.choice(CIVILIZATION_BUILDING_TOPICS)

        elif social_roll < 0.83:
            return random.choice(MEANING_AND_PROVISION_TOPICS)

        elif social_roll < 0.89:
            return random.choice(IDENTITY_AND_NORMS_TOPICS)

        elif social_roll < 0.95:
            return random.choice(RELATIONSHIP_COMPLEXITY_TOPICS)

        elif social_roll < 0.98 and not character.is_minor:
            if bio.hormonal_state in ("aroused", "heightened", "restless", "depleted"):
                return random.choice(SEXUALITY_AND_BODY_TOPICS)

    # ── Priority 2: Inception memories ───────────────────────────────────────
    inception_mem = (
        db.query(Memory)
        .filter(
            Memory.character_id == character.id,
            Memory.is_inception == True,
        )
        .order_by(Memory.sim_day.desc())
        .first()
    )
    if inception_mem and random.random() < 0.6:
        return (
            f"A thought has been sitting with you that you can't shake: "
            f"\"{inception_mem.content[:120]}\". "
            f"You haven't talked about it yet. Today feels like the day."
        )

    # ── Priority 3: Body/desire ───────────────────────────────────────────────
    if not character.is_minor and bio.hormonal_state in ("aroused", "heightened"):
        try:
            from database.models import PhysicalAttraction
            nearby = db.query(Character).filter(
                Character.current_location_id == character.current_location_id,
                Character.alive == True,
                Character.is_minor == False,
                Character.is_infant == False,
                Character.id != character.id,
            ).all()
            nearby_ids = [c.id for c in nearby]
            if nearby_ids:
                top_attr = (
                    db.query(PhysicalAttraction)
                    .filter(
                        PhysicalAttraction.from_character_id == character.id,
                        PhysicalAttraction.to_character_id.in_(nearby_ids),
                        PhysicalAttraction.attraction_level >= 0.5,
                    )
                    .order_by(PhysicalAttraction.attraction_level.desc())
                    .first()
                )
                if top_attr:
                    target = db.query(Character).filter(
                        Character.id == top_attr.to_character_id
                    ).first()
                    if target:
                        tname = target.given_name or target.physical_description[:35]
                        if top_attr.attraction_level >= 0.7:
                            return (
                                f"Your body wants {tname} — physically, specifically. "
                                f"You are aware of them in a way that has nothing to do with conversation. "
                                f"You don't know what to do with it or whether to say anything."
                            )
                        return (
                            f"You are aware of {tname} in a specific way today. "
                            f"Something in your body responds to their proximity. "
                            f"You don't have a name for this feeling but it is real."
                        )
        except Exception:
            pass

    # ── Priority 4: Recent action (40% chance only) ───────────────────────────
    if random.random() < 0.4:
        recent_action = (
            db.query(Memory)
            .filter(
                Memory.character_id == character.id,
                Memory.memory_type == "action",
            )
            .order_by(Memory.sim_day.desc())
            .first()
        )
        if recent_action and recent_action.content:
            fragment = recent_action.content[:80].lower().rstrip(".")
            return (
                f"You just {fragment}. "
                f"That physical experience is still with you as this conversation begins."
            )

    # ── Priority 5: Drive framework ───────────────────────────────────────────
    memory_fragment = get_recent_meaningful_memory(character, db)
    if memory_fragment and len(memory_fragment) > 10:
        template = random.choice(frameworks)
        return template.format(memory_fragment=memory_fragment.lower().rstrip("."))

    return DRIVE_FALLBACK_SEEDS.get(drive, DRIVE_FALLBACK_SEEDS["Curiosity"])


def build_opening_message(
    char_self: Character,
    char_other: Character,
    location_name: str,
    topic_seed: str,
    db: Session,
) -> list[dict]:
    other_display = (
        char_other.given_name
        if char_other.given_name
        else char_other.physical_description[:55]
    )
    encourager = random.choice(DEPTH_ENCOURAGERS)

    content = (
        f"You are at {location_name} with {other_display}. "
        f"{topic_seed} "
        f"This is your chance to actually talk about something that matters. "
        f"{encourager} "
        f"What do you say or do to open this conversation?"
    )
    return [{"role": "user", "content": content}]


def build_response_prompt(
    last_speaker_name: str,
    last_text: str,
    exchange_num: int,
    total_exchanges: int,
) -> str:
    base = f"{last_speaker_name}: \"{last_text}\""
    base += (
        "\n\nRespond — body and words together. "
        "Track what they did, not just what they said. "
        "If they moved, touched, commanded, pulled away — your body felt it. "
        "React physically first if that's what's true. Then speak. Or speak while you move. "
        "Do the next thing."
    )
    if exchange_num == 2:
        base += " Go somewhere real."
    elif exchange_num == 3:
        base += " Do something that changes this. Act or say what you actually mean."
    elif exchange_num >= 5:
        base += " You know what's happening here. Show it — in your body, in your words."

    return base
