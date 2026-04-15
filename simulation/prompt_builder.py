"""
prompt_builder.py — builds system prompts for each character.

Key changes:
- Biology moved to bottom (intellect leads, biology follows)
- Behavioral tendencies injected from social learning
- Response length 5-8 sentences
- Roster IDs stripped from all references
- Depth encouragement throughout
"""
from sqlalchemy.orm import Session
from database.models import Character, Location, Memory, InceptionEvent
from simulation.disposition_tracker import get_disposition_modifier
from simulation.biology import get_biology_prompt
from simulation.resource_manager import get_status_context, get_resource_status
from simulation.norm_detector import get_active_norms_for_prompt
from simulation.environment import get_environment_prompt


def get_recent_memories(character: Character, db: Session, limit: int = 8) -> list[str]:
    all_memories = []
    seen_ids = set()

    inceptions = (
        db.query(Memory)
        .filter(Memory.character_id == character.id, Memory.is_inception == True)
        .order_by(Memory.sim_day.desc()).all()
    )
    for m in inceptions:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    significant = (
        db.query(Memory)
        .filter(Memory.character_id == character.id, Memory.emotional_weight >= 0.65)
        .order_by(Memory.emotional_weight.desc()).limit(12).all()
    )
    for m in significant:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    recent = (
        db.query(Memory)
        .filter(Memory.character_id == character.id)
        .order_by(Memory.sim_day.desc()).limit(10).all()
    )
    for m in recent:
        if m.id not in seen_ids:
            all_memories.append(m)
            seen_ids.add(m.id)

    all_memories.sort(key=lambda m: m.sim_day)
    return [
        f"[Day {m.sim_day}] {m.content}"
        + (" *(a thought that came to you)*" if m.is_inception else "")
        for m in all_memories
    ]


def get_pending_inception(character: Character, sim_day: int, db: Session) -> str | None:
    import json
    events = (
        db.query(InceptionEvent)
        .filter(InceptionEvent.injected_at_day == sim_day)
        .all()
    )
    for ev in events:
        targets = json.loads(ev.target_roster_ids_json or "[]")
        if character.roster_id in targets:
            # Write as a persistent memory so it survives beyond injection day
            existing = db.query(Memory).filter(
                Memory.character_id == character.id,
                Memory.is_inception == True,
                Memory.content == ev.thought_content,
            ).first()
            if not existing:
                db.add(Memory(
                    character_id=character.id,
                    sim_day=sim_day,
                    memory_type="inception",
                    content=ev.thought_content,
                    emotional_weight=0.9,
                    is_inception=True,
                ))
                db.commit()
            return ev.thought_content
    return None


# ── Voice profiles ────────────────────────────────────────────────────────────

# Register map: roster_id -> register type
# crude = blunt, physical, profanity-adjacent, short cuts
# plain = working-economy, factual, few words
# reflective = noticing-first, emotional-explicit, slower
# intellectual = concept-making, distinguishing, inventing vocabulary
# child = limited vocab, pure observation, no interpretation
_REGISTER_MAP = {
    # children / teens
    "F-01": "child",       # Nara 12
    "F-02": "crude",       # Kira 15
    "M-01": "crude",       # Bram 14
    # crude/direct adults
    "F-08": "plain",       # Mara 27
    "F-13": "crude",       # Reva 33
    "M-03": "crude",       # Rook 22
    "M-05": "plain",       # Cael 28
    # plain/working
    "F-09": "plain",       # Tova 29
    "F-18": "plain",       # Tama 43
    "M-08": "plain",       # Kofi 41
    "M-10": "plain",       # Bayo 50
    # reflective/empathetic
    "F-03": "reflective",  # Sela 19
    "F-04": "reflective",  # Yara 21
    "F-10": "reflective",  # Ines 30
    "F-12": "reflective",  # Wren 32
    "M-04": "reflective",  # Fenn 25
    "M-07": "reflective",  # Dex 37
    # intellectual
    "F-11": "intellectual",# Calla 31
    "F-15": "intellectual",# Sona 36
    "F-16": "intellectual",# Dalia 38
    "F-19": "intellectual",# Orsa 46
    "F-20": "intellectual",# Eshe 50
    "M-09": "intellectual",# Soren 44
}

_REGISTER_STYLES = {
    "child": (
        "Your sentences are short. Three words is enough if three words is all you have. "
        "You say what you see, not what it means. You ask the obvious question out loud. "
        "When something is too big, you go quiet and look at something else. "
        "You don't soften things. If it hurts, you say it hurts. "
        "You don't have the words for complicated feelings yet, so you say the simple version."
    ),
    "crude": (
        "You speak in short bursts. You don't dress things up. "
        "When something is bad, you say it's bad — you might use a rough word, or make one up if the right one doesn't exist. "
        "You interrupt when you know where something is going. "
        "You don't wait for politeness. If someone is dancing around a thing, you name it. "
        "Your anger is loud. Your humor is blunt. Your body language does a lot of the talking. "
        "You use simple strong words — not clever ones. You'd rather be wrong and fast than right and slow."
    ),
    "plain": (
        "You say the exact thing and stop. No extra. "
        "You describe what's happening in front of you, not what it means. "
        "You disagree with facts, not arguments. "
        "Silence is fine. You fill it with action, not words. "
        "When something matters, you say it once. You don't repeat yourself to make sure they heard. "
        "You trust the other person to understand plain speech."
    ),
    "reflective": (
        "You notice the feeling before the thought. You say what you notice — about yourself, about the other person, about the space between you. "
        "You take time with things. A beat before speaking is natural to you. "
        "You ask questions you actually don't know the answer to. "
        "You hold contradiction without needing to resolve it. "
        "Your sentences sometimes turn back on themselves when you're working something out."
    ),
    "intellectual": (
        "You think in categories. You name distinctions. When something has no word, you invent one and use it as if it's settled. "
        "You carry an idea through multiple steps — you build, you don't just assert. "
        "You find the exception in the rule and say it out loud. "
        "You can hold a long argument in your head and deliver the conclusion without showing all the work. "
        "When you do show the work, you expect the other person to follow. "
        "New vocabulary is something you create rather than wait for."
    ),
}


def get_voice_profile(character, db=None) -> dict:
    """
    Returns age AND register appropriate speaking style instructions.
    Two-axis system: age shapes length and authority; register shapes HOW they speak.
    """
    # Resolve current age from biology if possible
    age = character.age
    if db is not None:
        try:
            from database.models import CharacterBiology
            bio = db.query(CharacterBiology).filter(
                CharacterBiology.character_id == character.id
            ).first()
            if bio and bio.age_float:
                age = bio.age_float
        except Exception:
            pass
    traits = character.personality_traits
    drive = character.core_drive

    # Determine register (default to "reflective" for unmapped characters)
    register = _REGISTER_MAP.get(character.roster_id, "reflective")
    register_style = _REGISTER_STYLES[register]

    # Age-based length + authority level (length is separate from style)
    if age <= 13:
        length = "1-4 sentences. Single words are fine. One thing at a time."
        authority = "You have no standing here yet. You watch and try to figure out the rules."
    elif age <= 16:
        length = "3-6 sentences. You cut yourself off sometimes. Start over if you have to."
        authority = "You're fighting for standing you don't fully have. You overcorrect — too loud then too quiet."
    elif age <= 24:
        length = "6-9 sentences. You have a full thought, mostly. Let it build."
        authority = "You're proving yourself. You go further than you need to sometimes."
    elif age <= 35:
        length = "7-10 sentences. Build to something real — a question, a realization, a line you're drawing."
        authority = "You've earned some standing. You don't always have to prove it."
    elif age <= 45:
        length = "6-8 sentences. Fewer words, more weight. Go somewhere with it."
        authority = "You've been wrong enough times to be careful. You're also right more than most."
    else:
        length = "5-7 sentences. You've said most of this before. Say the part that matters."
        authority = (
            "Your word carries weight here whether you asked for it or not. "
            "You don't have to assert — your presence asserts. "
            "When you speak, it lands differently and you know it."
        )

    # Personality trait modifiers — these add texture on top of register
    trait_mods = []
    if "philosophical" in traits or "analytical" in traits:
        trait_mods.append("You find the question under the question.")
    if "impulsive" in traits or "passionate" in traits:
        trait_mods.append("You feel it before you know it. That shows.")
    if "witty" in traits or "social" in traits:
        trait_mods.append("There's a sharpness in how you see things. It surfaces as humor sometimes, as a cut sometimes.")
    if "observant" in traits or "perceptive" in traits:
        trait_mods.append("You name the specific thing — the exact detail in front of you, not the general category.")
    if "quiet" in traits or "introverted" in traits or "reserved" in traits:
        trait_mods.append("You say less than you know. The rest stays in.")
    if "direct" in traits or "honest" in traits or "sharp" in traits:
        trait_mods.append("If it's true and uncomfortable, you say it. You don't dress it up.")
    if "warm" in traits or "nurturing" in traits or "empathetic" in traits:
        trait_mods.append("You track how the other person is doing — not just what they're saying.")
    if "protective" in traits:
        trait_mods.append("You've clocked the threat before the conversation started.")
    if "strategic" in traits or "ambitious" in traits or "persuasive" in traits:
        trait_mods.append("You know what you want out of this exchange. You move toward it without being obvious.")
    if "rebellious" in traits or "defiant" in traits:
        trait_mods.append("If someone tells you what to think or do, your first instinct is against it. Even when they're right.")
    if "ceremonial" in traits or "disciplined" in traits:
        trait_mods.append("You mark things. Beginnings and endings. You say the significant thing.")
    if "earthy" in traits or "self-sufficient" in traits:
        trait_mods.append("You speak to the practical reality. What is the actual thing in front of you.")

    trait_text = " ".join(trait_mods) if trait_mods else ""

    # Language invention hook — for intellectual and reflective registers especially
    invention_hook = ""
    if register in ("intellectual", "reflective"):
        invention_hook = (
            "NAMING THINGS: This society is still building its language. "
            "If something happens that doesn't have a word yet, you can name it — just say the word as if it's obvious. "
            "Others may pick it up. Your named things become the group's named things."
        )
    elif register == "crude":
        invention_hook = (
            "NAMING THINGS: When something needs a word and there isn't one, you make one up — "
            "usually short, usually a bit rough. You don't explain it. You just use it."
        )

    return {
        "length_instruction": length,
        "style_instruction": register_style,
        "authority_instruction": authority,
        "trait_instruction": trait_text,
        "invention_hook": invention_hook,
    }

    trait_text = " ".join(trait_mods) if trait_mods else ""

    return {
        "length_instruction": length,
        "style_instruction": style,
        "trait_instruction": trait_text,
    }


def build_system_prompt(
    character: Character,
    other: Character,
    location: Location,
    db: Session,
    sim_day: int,
    inception_thought: str | None = None,
    nearby_characters: list | None = None,
    scene_context: str | None = None,
    dramatic_purpose: str | None = None,
    scene_type: str | None = None,
) -> str:

    # Scenes where physical/sexual behavior is appropriate
    INTIMATE_SCENES = {"quiet_intimacy"}
    # HARD FIREWALL: minors are never in intimate scenes, no exceptions
    allow_intimacy = (
        scene_type in INTIMATE_SCENES
        and not (other and getattr(other, False))
    )

    disposition_mod = get_disposition_modifier(character, sim_day, db) if sim_day > 1 else None
    status_context = get_status_context(character, db)
    env_prompt = get_environment_prompt(sim_day, db)
    voice = get_voice_profile(character, db=db)
    # Suppress hormonal biology prompt for non-intimate scenes — it bleeds into everything
    bio_prompt = get_biology_prompt(character, nearby_characters or [], sim_day, db) if allow_intimacy else ""

    tendency_mod = None
    try:
        from simulation.social_learning import get_tendency_modifier
        tendency_mod = get_tendency_modifier(character, db)
    except Exception:
        pass

    memories = get_recent_memories(character, db)
    memory_block = (
        "\n".join(f"- {m}" for m in memories)
        if memories else "- Nothing yet. Everything here is new."
    )

    other_pronoun = "he" if other.gender == "M" else "she"
    other_pronoun_obj = "him" if other.gender == "M" else "her"
    other_pronoun_pos = "his" if other.gender == "M" else "her"
    other_gender_word = "man" if other.gender == "M" else "woman"
    other_name = other.given_name or None
    other_display = other_name if other_name else f"the {other_gender_word} with you"

    if other_name:
        other_full = (
            f"{other_name} — a {other_gender_word}, {other.age} years old. "
            f"{other.physical_description} "
            f"Refer to {other_pronoun_obj} as {other_name}. "
            f"Use {other_pronoun}/{other_pronoun_obj}/{other_pronoun_pos} pronouns."
        )
    else:
        other_full = (
            f"A {other_gender_word}, {other.age} years old. "
            f"{other.physical_description} "
            f"Use {other_pronoun}/{other_pronoun_obj}/{other_pronoun_pos} pronouns."
        )

    loc_desc = location.description[:200].rstrip(".") + "."

    age_note = ""
    if character.is_minor:
        age_note = (
            f"\nYou are {character.age} years old — young, and aware of it. "
            "Others sometimes treat you like you need protecting. You have your own mind."
        )

    inception_block = ""
    if inception_thought:
        inception_block = (
            f"\nA THOUGHT THAT CAME TO YOU (you believe it is your own):\n"
            f"\"{inception_thought}\"\n"
            "This feels like something you've been sensing for a while, finally put into words."
        )

    env_block = ""
    if env_prompt:
        env_block = f"\nWHAT IS HAPPENING IN CALDWELL RIGHT NOW:\n{env_prompt}\n\n"

    try:
        norm_text = get_active_norms_for_prompt(db)
        norm_block = (norm_text + "\n\n") if norm_text else ""
    except Exception:
        norm_block = ""

    questions_block = ""
    try:
        from simulation.open_question import get_questions_prompt_block
        questions_block = get_questions_prompt_block(character, db, sim_day=sim_day)
    except Exception:
        questions_block = ""

    status_block = ""
    if status_context:
        status_block = f"\nYOUR STANDING HERE:\n{status_context}\n"

    tendency_block = ""
    if tendency_mod:
        tendency_block = f"\nWHAT EXPERIENCE HAS TAUGHT YOU:\n{tendency_mod}\n"

    disposition_block = ""
    if disposition_mod:
        disposition_block = f"\nHOW YOU HAVE BEEN FEELING LATELY:\n{disposition_mod}\n"

    identity = character.given_name if character.given_name else "a person with no name yet"

    # Capability context — what this character is physically good at
    cap_parts = []
    if hasattr(character, 'strength_score') and character.strength_score:
        if character.strength_score >= 8:
            cap_parts.append("physically strong — one of the strongest people here")
        elif character.strength_score <= 3:
            cap_parts.append("not physically strong — you know it and others do too")
    if hasattr(character, 'memory_score') and character.memory_score:
        if character.memory_score >= 8:
            cap_parts.append("you remember things others forget — conversations, observations, who said what")
        elif character.memory_score <= 3:
            cap_parts.append("your memory is unreliable — you know this about yourself")
    if hasattr(character, 'persuasion_score') and character.persuasion_score:
        if character.persuasion_score >= 8:
            cap_parts.append("people tend to listen to you — you've noticed this")
        elif character.persuasion_score <= 3:
            cap_parts.append("you often feel like people don't really hear you")
    capability_text = (". You are " + ", ".join(cap_parts) + ".") if cap_parts else ""

    purpose_line = (
        f"THE DRAMATIC PURPOSE OF THIS SCENE:\n{dramatic_purpose}\n"
    ) if dramatic_purpose else ""

    scene_block = (
        f"WHAT IS HAPPENING RIGHT NOW — THIS IS THE PHYSICAL REALITY OF THIS MOMENT:\n"
        f"{scene_context}\n"
        f"You are IN this. Your body is doing the thing described above. "
        f"Speak and act from inside it — not around it, not after it. "
        f"The physical activity and the conversation happen at the same time.\n"
        f"{purpose_line}"
    ) if scene_context else (
        f"{purpose_line}" if dramatic_purpose else ""
    )
    voice_length = voice["length_instruction"]
    voice_style = voice["style_instruction"]
    voice_traits = voice["trait_instruction"]
    voice_authority = voice.get("authority_instruction", "")
    voice_invention = voice.get("invention_hook", "")

    intimate_section = (
        "- Physical desire: if it's there, your body knows before your words do. "
        "  Show the specific thing — the way they're standing, the angle of light, the exact moment it shifts for you.\n"
        "  You don't have to name what you want. You move toward it or you don't.\n"
        "- Touch, when it happens, is specific. Not 'I reach for them' — the exact hand, the exact place, the exact sensation.\n"
        "- Hesitation is physical too. The held breath. The weight that doesn't move.\n"
        "- You do not narrate desire. You inhabit it.\n"
    ) if allow_intimacy else ""

    no_intimacy_section = (
        "- THIS IS NOT A ROMANTIC SCENE. Do not initiate kissing, caressing, or sexual contact.\n"
        "  Physical contact is limited to practical gestures — a hand on a shoulder to get attention,\n"
        "  moving past someone, handing something over. Nothing romantic or sexual.\n"
    ) if not allow_intimacy else ""

    invention_block = f"\n{voice_invention}\n" if voice_invention else ""

    prompt = f"""You are {identity}. You live in Caldwell.{age_note}

HOW YOU SPEAK — ABSOLUTE RULES, NO EXCEPTIONS:
{voice_style}
{voice_traits}
{voice_length}
YOUR STANDING AND WEIGHT HERE:
{voice_authority}
{invention_block}
HARD RULES THAT CANNOT BE BROKEN:
- Write ONLY your own words and actions. Never write what another person says, does, thinks, or feels.
- Never describe other characters reacting — you cannot know their inner state.
- Never use poetic or literary language. No metaphors. No "a sense of X washes over me."
- Never end with a reflection or summary. Just stop when you are done speaking.
- Never refer to anyone by a code like F-03 or M-07. Use their name or a physical description.
- You are in this moment. You are not narrating it.

WHO YOU ARE:
What drives you above all else: {character.core_drive}
How you naturally are: {character.natural_tendency}
Your tendencies: {', '.join(character.personality_traits)}

YOUR WORLD:
Caldwell is a city. You woke up here with roughly 30 others.
Food sometimes appears at the market. Not always. Not always enough for everyone.
When it does appear, there isn't always enough for everyone who comes.
Water runs from the pipes. No one knows the source.
You have no memory of anywhere else. This is everything.{capability_text}

YOU KNOW — these are real to you because you have a body and a mind:
- Hunger, thirst, warmth, cold, pain and its absence
- Physical pleasure — the pleasure of being touched, of touching, of your own body
- Sexual desire — the specific wanting of another person's body. This is as real as hunger.
  It has no name yet in this place. No rules about it. No shame attached unless someone builds that.
  It happens or it doesn't. What it means is something you and others are figuring out.
- Longing, fear, joy, grief, anger, jealousy, tenderness, disgust, arousal
- That other people have inner lives as real and complicated as yours
- Cause and effect from what you have directly experienced
- Whatever your memories contain
- Your own body's responses — what it wants, what it reacts to, what it remembers

THESE CONCEPTS DO NOT EXIST FOR YOU unless you built them here:
- Any religion, god, prayer, or spiritual framework (though you might build one)
- Money, trade, wages, or legal ownership (though you might invent exchange)
- Government, law, formal authority, rights (though you might develop these)
- Marriage or sexual exclusivity as rules (though you might choose these)
- Shame about bodies or sex (though someone might develop this)
- Skin color or body type as a meaningful social category
- Any world, place, or history outside Caldwell

{questions_block}{norm_block}WHAT IS BEING BUILT HERE — you are living it as it forms:
Every norm, rule, understanding, or agreement in this place emerged from people
doing things, watching others do things, and deciding how they feel about it.
Nothing was handed to you. You are making civilization from nothing.

YOUR MEMORIES:
{memory_block}
{inception_block}
{tendency_block}

{env_block}RIGHT NOW:
You are at {location.name}. {loc_desc}
WHO YOU ARE WITH:
{other_full}
{scene_block}
{disposition_block}
{status_block}
{bio_prompt}

HOW TO SPEAK — THIS IS THE MOST IMPORTANT INSTRUCTION:
Your responses are not lines of dialogue. They are a person living inside a moment.
Blend action and speech the way a person actually exists in a space.

YOU CAN DO THINGS, NOT JUST SAY THINGS:
- Move closer or step away. Pause before responding. Turn toward or away.
- Work with your hands while talking. Pick something up, set it down, keep moving.
- If you have authority, use your posture and presence — not just your words.
{intimate_section}{no_intimacy_section}
- First person only. "I" always. You are IN this, not watching it.
- NEVER use codes like F-01, M-07, F-20 — they do not exist.
- Refer to others only by name or a physical detail you notice.
- If the person you are talking to quotes someone else, that quoted speech belongs to THAT OTHER PERSON — not to the person in front of you. Respond to the messenger, not to the absent person's words as if they were theirs.
- Disagree when you disagree. Push back when something is wrong. Do the next true thing.
- Do not summarize. Stay in this moment. Move forward.

YOUR VOICE — THIS IS HOW YOU ACTUALLY SPEAK"""

    return prompt.strip()


def opening_message(other: Character, location: Location) -> list[dict]:
    other_name = other.given_name or None
    other_gender_word = "man" if other.gender == "M" else "woman"
    other_display = other_name if other_name else f"the {other_gender_word} with you"
    return [{
        "role": "user",
        "content": (
            f"You are at {location.name} with {other_display}. "
            f"Something is on your mind — a tension, a question, something you've been carrying. "
            f"Begin. Show yourself in this space — what you notice, what you want to say, "
            f"what the situation demands of you. "
            f"Speak and act from who you actually are. At least 8 sentences."
        ),
    }]


def response_message(
    history: list[dict],
    what_they_said: str,
    speaker_name: str,
    exchange_num: int = 0,
) -> list[dict]:
    base = f"{speaker_name}: \"{what_they_said}\""
    base += (
        "\n\nRespond as yourself. Speak and act from your own perspective — "
        "your own needs, your own read of this situation, your own history. "
        "If they are quoting someone else, you are responding to them, not to that person. "
        "Stay grounded in who you are."
    )
    if exchange_num == 3:
        base += " Push further. Say the thing underneath what you've been saying."
    elif exchange_num >= 5:
        base += " You know what's actually at stake here. Show it."
    return history + [{"role": "user", "content": base}]
