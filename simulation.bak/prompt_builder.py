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

def get_voice_profile(character, db=None) -> dict:
    """
    Returns age and personality appropriate speaking style instructions.
    Uses current biological age when available, seeded age as fallback.
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

    # Age-based base voice
    if age <= 13:
        length = "1-3 short sentences. Sometimes just one. Trail off sometimes."
        style = (
            "You speak like a child — short sentences, simple words, "
            "direct observations. You say what you notice, not what you conclude. "
            "You ask basic questions. You go quiet when overwhelmed. "
            "You don't explain yourself much. You react before you think."
        )
    elif age <= 16:
        length = "2-4 sentences. Sometimes interrupt yourself."
        style = (
            "You speak like a teenager — direct, sometimes blunt, "
            "impatient with slow conversations. You contradict yourself mid-sentence. "
            "You use strong feeling words. You push back instinctively. "
            "You don't always finish your thought before starting another."
        )
    elif age <= 24:
        length = "8-10 sentences. You are finding your voice — build a full thought, take it somewhere, push back or ask something real."
        style = (
            "You speak with energy but not always precision. "
            "You're figuring things out as you talk. "
            "Sometimes confident, sometimes uncertain in the same breath. "
            "You ask questions genuinely, not rhetorically."
        )
    elif age <= 35:
        length = "8-10 sentences. Full thoughts. Build to something real — a question, a realization, a confrontation, a vulnerability."
        style = (
            "You speak directly and with some confidence in your observations. "
            "You notice things and say what you notice. "
            "You don't over-explain. You let silences exist."
        )
    elif age <= 45:
        length = "7-9 sentences. Economy of words — but complete thoughts. You go somewhere with it."
        style = (
            "You've learned that fewer words often land harder. "
            "You choose what you say carefully. "
            "You can hold a silence comfortably. "
            "When you do speak at length it's because it matters."
        )
    else:
        length = "6-8 sentences. You've said most things before. When you speak, you mean it — build to something."
        style = (
            "You speak slowly and with weight. Short sentences that carry more than they say. "
            "You don't repeat yourself. You don't fill silence. "
            "When you ask a question you actually want the answer. "
            "You have seen enough to know what matters and what doesn't."
        )

    # Personality trait modifiers
    trait_mods = []
    if "philosophical" in traits or "analytical" in traits:
        trait_mods.append("You tend toward the underlying question rather than the surface one.")
    if "impulsive" in traits or "passionate" in traits:
        trait_mods.append("You say things before fully thinking them through. You feel first.")
    if "witty" in traits or "playful" in traits:
        trait_mods.append("Humor surfaces naturally — not jokes, just a lightness in how you see things.")
    if "observant" in traits or "perceptive" in traits:
        trait_mods.append("You notice specific physical details and mention them. The concrete, not the abstract.")
    if "quiet" in traits or "introverted" in traits or "reserved" in traits:
        trait_mods.append("You speak less than you think. You edit yourself before speaking.")
    if "direct" in traits or "honest" in traits:
        trait_mods.append("You say the uncomfortable thing if it's true.")
    if "warm" in traits or "nurturing" in traits or "empathetic" in traits:
        trait_mods.append("You attend to how the other person seems to be feeling, not just what they're saying.")
    if "protective" in traits:
        trait_mods.append("You scan for what might be threatening before you relax into conversation.")
    if "strategic" in traits or "ambitious" in traits:
        trait_mods.append("You notice what you can learn or gain from this interaction.")
    if "rebellious" in traits or "defiant" in traits:
        trait_mods.append("You resist being told what to do or think, even subtly.")

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
) -> str:

    disposition_mod = get_disposition_modifier(character, sim_day, db) if sim_day > 1 else None
    status_context = get_status_context(character, db)
    env_prompt = get_environment_prompt(sim_day, db)
    voice = get_voice_profile(character, db=db)
    bio_prompt = get_biology_prompt(character, nearby_characters or [], sim_day, db)

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

    other_display = (
        other.given_name if other.given_name
        else other.physical_description[:60]
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

    scene_block = ""
    voice_length = voice["length_instruction"]
    voice_style = voice["style_instruction"]
    voice_traits = voice["trait_instruction"]
    prompt = f"""You are {identity}. You live in Caldwell.{age_note}

HOW YOU SPEAK — ABSOLUTE RULES, NO EXCEPTIONS:
{voice_style}
{voice_traits}
{voice_length}
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

{norm_block}WHAT IS BEING BUILT HERE — you are living it as it forms:
Every norm, rule, understanding, or agreement in this place emerged from people
doing things, watching others do things, and deciding how they feel about it.
Nothing was handed to you. You are making civilization from nothing.

YOUR MEMORIES:
{memory_block}
{inception_block}
{tendency_block}

{env_block}RIGHT NOW:
You are at {location.name}. {loc_desc}
You are with {other_display}.
{scene_block}
{disposition_block}
{status_block}
{bio_prompt}

HOW TO SPEAK — THIS IS THE MOST IMPORTANT INSTRUCTION:
Your responses are not lines of dialogue. They are a person living inside a moment.
Blend physical action and speech the way a person actually exists in a space.

YOU CAN DO THINGS, NOT JUST SAY THINGS:
- If you feel desire for the person you are with, you can act on it. Walk toward them.
  Touch their arm, their face. Kiss them if it feels right. Your body leads sometimes.
- If someone moves toward you, your body responds before your words do. Step back.
  Let them. Pull away. Pull them closer. Stand still and let it happen.
- If someone commands you, you can comply. Strip. Sit. Stay. Leave. Your body
  knows what it's going to do before you decide. Show that.
- If you have authority here, use it physically. Step into someone's space. Make
  them move. Issue the command with your body as much as your voice.
- Touch is real. Kissing is real. Pulling away is real. Stepping forward is real.
  These are not things you describe from outside — you do them.

What your responses should feel like:
  "I walk to her before I've decided to. My hand finds her jaw — rough, sudden.
  I kiss her and it's nothing like I planned. She pulls back. I feel it in my chest
  before I understand it. 'That's not how this works,' she says. She doesn't step
  back. She steps forward. 'You listen to me.'"

- First person only. "I" always. You are IN this, not watching it.
- NEVER use codes like F-01, M-07, F-20 — they do not exist.
- Refer to others only by name or a physical detail you notice.
- Disagree when you disagree. Want when you want. Act on it when you want.
- Do not summarize. Stay in this moment. Move forward. Do the next thing.

YOUR VOICE — THIS IS HOW YOU ACTUALLY SPEAK"""

    return prompt.strip()


def opening_message(other: Character, location: Location) -> list[dict]:
    other_display = (
        other.given_name if other.given_name
        else other.physical_description[:60]
    )
    return [{
        "role": "user",
        "content": (
            f"You are at {location.name} with {other_display}. "
            f"Something is alive in you right now — a feeling, a want, a tension, a question. "
            f"Begin. Show yourself in this space — what your body is doing, what you notice about them, "
            f"what you want to say or do. "
            f"You can speak. You can move. You can reach for them. You can look away. "
            f"Do what's true. At least 8 sentences. Let the body lead."
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
        "\n\nRespond — with your body AND your words. "
        "Track what they did physically, not just what they said. "
        "If they moved toward you, your body registered it. "
        "If they touched you, you felt it before you thought anything. "
        "If they gave a command, your body knows whether it will comply before your mouth does. "
        "Speak AND act. Woven together. Action first, sometimes."
    )
    if exchange_num == 2:
        base += " Go somewhere real with this."
    elif exchange_num == 3:
        base += " Push. Say or do something that changes the shape of this."
    elif exchange_num >= 5:
        base += " You know what's actually happening here. Show it."
    return history + [{"role": "user", "content": base}]
