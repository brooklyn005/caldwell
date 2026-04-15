"""
biology.py — manages the full biological layer of each character.

Systems:
- Hunger, fatigue, bathroom urgency (all characters)
- Hormonal states including arousal (adults only)
- Physical attraction (adults only, minors excluded)
- Menstrual cycles (adult females 18+, and adolescent females approaching puberty)
- Aging (all characters, 1 sim day = 3 real days)
- Age-related health decline (45+)
- Death from age-related causes (probability increases significantly 70+)

HARD GUARDRAILS:
- Physical attraction system: adults only, minors never
- Sexual content: never generated for any character
- Minor characters get age-appropriate biological psychology only
"""
import logging
import random
import math
from sqlalchemy.orm import Session
from database.models import Character, CharacterBiology, PhysicalAttraction, Location

logger = logging.getLogger("caldwell.biology")

# ── Constants ─────────────────────────────────────────────────────────────────

EATING_LOCATIONS = {"Bayou Market", "Community Center"}
SLEEP_LOCATIONS  = {"The Meridian", "Lakeview Flats"}
BATHROOM_LOCATIONS = {
    "Lakeview Flats", "The Meridian", "Riverside Park",
    "Rooftop Garden", "Warehouse Row"
}

# Sim day to real day ratio
SIM_TO_REAL_DAYS = 3.0  # 1 sim day = 3 real days

HORMONAL_STATES = {
    "baseline":   {"weight": 35, "duration": (3, 7)},
    "restless":   {"weight": 15, "duration": (2, 4)},
    "heightened": {"weight": 15, "duration": (2, 5)},
    "depleted":   {"weight": 10, "duration": (2, 3)},
    "aggressive": {"weight": 12, "duration": (1, 3)},
    "calm":       {"weight": 8,  "duration": (3, 6)},
    "aroused":    {"weight": 5,  "duration": (1, 2)},
}

# Menstrual phases and their day ranges
MENSTRUAL_PHASES = {
    "menstruation": (1, 5),
    "follicular":   (6, 13),
    "ovulation":    (14, 14),
    "luteal":       (15, 28),
}

HORMONAL_PROMPTS = {
    "baseline": None,
    "restless": (
        "Your body is restless — an energy with nowhere to go. "
        "You want to move, touch something, change something. "
        "Sitting still feels wrong."
    ),
    "heightened": (
        "Everything is landing harder today — other people's voices, "
        "their proximity, the way they look at you or don't. "
        "Your skin feels more present than usual. "
        "You are aware of bodies — your own and others."
    ),
    "depleted": (
        "You feel wrung out. Your body wants warmth and closeness "
        "more than anything else right now. "
        "You are less guarded than you usually are. "
        "You want to be held or to hold someone."
    ),
    "aggressive": (
        "There is a tension in you sitting just below the surface. "
        "You feel territorial. Small things feel like provocations. "
        "You are not looking for a fight but you won't back down."
    ),
    "calm": (
        "You feel unusually settled — a physical ease that makes you "
        "patient and generous. The things that normally irritate you "
        "feel far away."
    ),
    "aroused": (
        "Your body wants something specific. "
        "You are aware of yourself as a physical being and of other people as physical beings. "
        "Desire is real and present in you right now — not abstract, not distant. "
        "You may not have words for what you want but your body knows."
    ),
}

# Menstrual phase prompts — honest, direct, no cultural scaffolding
MENSTRUAL_PROMPTS = {
    "menstruation_first": (
        "Something is happening to your body that you do not understand. "
        "There is blood. There is pain in your lower belly that comes and goes. "
        "You are frightened and confused. You do not know if this is normal "
        "or if something is wrong with you. You have never felt this before "
        "and you have no words for it."
    ),
    "menstruation_known": (
        "The bleeding has come again — the thing that happens in your body "
        "every month now. The cramping is real and uncomfortable. "
        "You feel heavier than usual, more tired, more raw."
    ),
    "follicular": (
        "Your body feels cleaner, lighter than it did. "
        "An energy returning that you had forgotten was yours."
    ),
    "ovulation": (
        "You feel unusually alert, unusually present. "
        "Something in you is open in a way that is hard to name."
    ),
    "luteal": (
        "Your body is changing again in small ways — "
        "tender, heavier, slightly on edge. "
        "Things feel closer to the surface than usual."
    ),
    "luteal_late": (
        "The tension in your body has been building for days. "
        "You feel irritable, bloated, uncomfortable in your own skin. "
        "Something is about to change again."
    ),
}

ATTRACTION_PROMPTS = {
    "low": (
        "You notice {name} and feel something — not fully formed yet, "
        "but it pulls your attention back to them."
    ),
    "medium": (
        "You want to be near {name}. When they are close you feel it in your body "
        "before your mind catches up. You think about them when they are not there."
    ),
    "high": (
        "You want {name} — physically, specifically. "
        "You think about touching them. You don't know if that is something "
        "that can happen here or what it would mean if it did, "
        "but the wanting is real and it does not go away."
    ),
    "very_high": (
        "You have been with {name} or you want to be. "
        "Your body knows exactly what it wants. "
        "This is the most specific and urgent feeling you carry right now."
    ),
}

# Age-related health prompts
AGE_HEALTH_PROMPTS = {
    "early_decline": (  # 45-54
        "Your body reminds you of its age in small ways today — "
        "a stiffness that wasn't there before, a tiredness that comes faster."
    ),
    "moderate_decline": (  # 55-64
        "The effort of things costs more than it used to. "
        "Your body has its own pace now and it is slower than your mind."
    ),
    "significant_decline": (  # 65-74
        "Your body is working harder just to do ordinary things. "
        "The aches are familiar now. You have learned to move around them."
    ),
    "severe_decline": (  # 75+
        "Every day your body asks more of you and gives back less. "
        "You are aware of your own fragility in a way you cannot ignore."
    ),
}


# ── Core functions ─────────────────────────────────────────────────────────────

def get_or_create_biology(character: Character, db: Session) -> CharacterBiology:
    bio = (
        db.query(CharacterBiology)
        .filter(CharacterBiology.character_id == character.id)
        .first()
    )
    if not bio:
        bio = CharacterBiology(
            character_id=character.id,
            hunger=random.uniform(2.0, 6.0),
            fatigue=random.uniform(1.0, 5.0),
            bathroom_urgency=random.uniform(0.0, 3.0),
            physical_comfort=random.uniform(5.0, 9.0),
            hormonal_state="baseline",
            hormonal_days_remaining=random.randint(2, 5),
            health_score=1.0,
        )
        # Initialize menstrual cycle for eligible females
        if character.gender == 'F' and not character.is_minor:
            bio.menstrual_cycle_day = random.randint(1, 28)
            bio.menstrual_phase = _get_phase_for_day(bio.menstrual_cycle_day)
            bio.first_menstruation_occurred = True
            bio.menstruation_known = True
        elif character.gender == 'F' and character.is_minor and character.age >= 11:
            # Adolescent — may be approaching first period
            bio.menstrual_cycle_day = None
            bio.menstrual_phase = None
            bio.first_menstruation_occurred = False
            bio.menstruation_known = False

        db.add(bio)
        db.flush()
    return bio


def _get_phase_for_day(cycle_day: int) -> str:
    for phase, (start, end) in MENSTRUAL_PHASES.items():
        if start <= cycle_day <= end:
            return phase
    return "follicular"


def _compute_current_age(character: Character, sim_day: int) -> float:
    """
    Compute character's current age based on sim days elapsed.
    1 sim day = SIM_TO_REAL_DAYS real days.
    """
    # We don't store birth_sim_day so we use age at seeding (day 1)
    # and add elapsed time
    real_days_elapsed = (sim_day - 1) * SIM_TO_REAL_DAYS
    years_elapsed = real_days_elapsed / 365.25
    return character.age + years_elapsed


def _compute_health_score(age_float: float) -> float:
    """
    Health score 1.0 at 45, declining curve to ~0.3 at 80.
    """
    if age_float < 45:
        return 1.0
    # Sigmoid-ish decline
    decline = (age_float - 45) / 40.0  # 0 at 45, 1 at 85
    return max(0.1, 1.0 - (decline ** 1.5) * 0.7)


def _death_probability(age_float: float, health_score: float) -> float:
    """
    Probability of age-related death per sim tick.
    Near zero before 65, meaningful after 75.
    """
    if age_float < 65:
        return 0.0
    # Increases exponentially after 65
    base = math.exp((age_float - 75) / 8) * 0.002
    return min(0.15, base * (1.1 - health_score))


def tick_aging(character: Character, bio: CharacterBiology, sim_day: int, db: Session) -> bool:
    """
    Update character's age and health. Returns True if character died.
    """
    age_float = _compute_current_age(character, sim_day)
    bio.age_float = round(age_float, 4)
    bio.health_score = round(_compute_health_score(age_float), 3)
    bio.last_age_update_day = sim_day

    # Update is_minor status as characters age
    if character.is_minor and age_float >= 18.0:
        character.is_minor = False
        logger.info(
            f"  COMING OF AGE: {character.roster_id} "
            f"({character.given_name or 'unnamed'}) is now an adult at {age_float:.1f} years"
        )

    # Check for age-related death
    death_prob = _death_probability(age_float, bio.health_score)
    if death_prob > 0 and random.random() < death_prob:
        logger.info(
            f"  DEATH: {character.roster_id} "
            f"({character.given_name or 'unnamed'}) died at age {age_float:.1f}"
        )
        return True  # character died

    return False


def tick_menstrual_cycle(
    character: Character,
    bio: CharacterBiology,
    sim_day: int,
    db: Session,
) -> dict | None:
    """
    Advance menstrual cycle by one sim day.
    Returns an event dict if something significant happened (first period, etc.)
    """
    age_float = bio.age_float or _compute_current_age(character, sim_day)
    event = None

    # Adult females
    if character.gender == 'F' and not character.is_minor:
        if bio.menstrual_cycle_day is None:
            # Initialize
            bio.menstrual_cycle_day = random.randint(1, 28)
            bio.menstrual_phase = _get_phase_for_day(bio.menstrual_cycle_day)
            bio.first_menstruation_occurred = True
            bio.menstruation_known = True
        else:
            # Advance cycle
            bio.menstrual_cycle_day = (bio.menstrual_cycle_day % 28) + 1
            bio.menstrual_phase = _get_phase_for_day(bio.menstrual_cycle_day)

    # Adolescent females approaching puberty
    elif character.gender == 'F' and character.is_minor and age_float >= 11.0:
        if not bio.first_menstruation_occurred:
            # Probability of first period increases with age
            # Realistic onset: 11-14 years old
            onset_prob = max(0, (age_float - 11.0) / 3.0) * 0.008
            if random.random() < onset_prob:
                bio.menstrual_cycle_day = 1
                bio.menstrual_phase = "menstruation"
                bio.first_menstruation_occurred = True
                bio.menstruation_known = False  # doesn't know what this is yet
                logger.info(
                    f"  FIRST MENSTRUATION: {character.roster_id} "
                    f"({character.given_name or 'unnamed'}) age {age_float:.1f}"
                )
                event = {
                    "type": "first_menstruation",
                    "roster_id": character.roster_id,
                    "given_name": character.given_name,
                    "age": age_float,
                }
        else:
            # Already started, advance normally
            if bio.menstrual_cycle_day:
                bio.menstrual_cycle_day = (bio.menstrual_cycle_day % 28) + 1
                bio.menstrual_phase = _get_phase_for_day(bio.menstrual_cycle_day)

    return event


def tick_biology(character: Character, sim_day: int, db: Session) -> dict | None:
    """
    Full biology tick for one character.
    Returns event dict if something notable happened (death, first period).
    """
    bio = get_or_create_biology(character, db)

    loc = db.query(Location).filter(
        Location.id == character.current_location_id
    ).first()
    loc_name = loc.name if loc else ""

    # ── Aging ────────────────────────────────────────────────────────────────
    died = tick_aging(character, bio, sim_day, db)
    if died:
        character.alive = False
        db.commit()
        return {"type": "death", "roster_id": character.roster_id,
                "given_name": character.given_name}

    age_float = bio.age_float or _compute_current_age(character, sim_day)

    # ── Hunger ───────────────────────────────────────────────────────────────
    if loc_name in EATING_LOCATIONS:
        # Try to consume from resource pool — food is NOT guaranteed
        try:
            from simulation.resource_manager import consume_food
            ate = consume_food(character, loc, sim_day, db)
        except Exception:
            ate = True  # fallback if resource system not initialized
        if ate:
            bio.hunger = max(0.0, bio.hunger - 5.0)
            bio.last_ate_day = sim_day
        else:
            # At the market but no food — hunger still builds
            hunger_rate = 1.8 if age_float < 40 else 1.4
            bio.hunger = min(10.0, bio.hunger + hunger_rate * 0.5)
    else:
        hunger_rate = 1.8 if age_float < 40 else 1.4
        bio.hunger = min(10.0, bio.hunger + hunger_rate)

    # ── Fatigue ───────────────────────────────────────────────────────────────
    if loc_name in SLEEP_LOCATIONS and sim_day > bio.last_slept_day:
        recovery = 4.0 * bio.health_score  # elderly recover less from sleep
        bio.fatigue = max(0.0, bio.fatigue - recovery)
        bio.last_slept_day = sim_day
    else:
        fatigue_rate = 0.9 + max(0, (age_float - 45) / 100)  # fatigue builds faster with age
        bio.fatigue = min(10.0, bio.fatigue + fatigue_rate)

    # ── Bathroom ─────────────────────────────────────────────────────────────
    if loc_name in BATHROOM_LOCATIONS:
        bio.bathroom_urgency = max(0.0, bio.bathroom_urgency - 5.0)
        bio.last_bathroom_day = sim_day
    else:
        bio.bathroom_urgency = min(10.0, bio.bathroom_urgency + 1.4)

    # ── Physical comfort ──────────────────────────────────────────────────────
    discomfort = bio.hunger * 0.4 + bio.fatigue * 0.3 + bio.bathroom_urgency * 0.3
    bio.physical_comfort = max(0.0, 10.0 - discomfort)

    # ── Hormonal cycling (adults) ─────────────────────────────────────────────
    if not character.is_minor:
        bio.hormonal_days_remaining = max(0, bio.hormonal_days_remaining - 1)
        if bio.hormonal_days_remaining <= 0:
            _cycle_hormonal_state(bio, age_float)

    # ── Menstrual cycle ───────────────────────────────────────────────────────
    cycle_event = tick_menstrual_cycle(character, bio, sim_day, db)

    bio.updated_day = sim_day
    db.commit()

    return cycle_event


def _cycle_hormonal_state(bio: CharacterBiology, age_float: float):
    states = list(HORMONAL_STATES.keys())
    weights = [HORMONAL_STATES[s]["weight"] for s in states]
    # Arousal less likely in older characters
    if age_float > 50:
        aroused_idx = states.index("aroused")
        weights[aroused_idx] = max(1, weights[aroused_idx] - 3)
    new_state = random.choices(states, weights=weights)[0]
    min_d, max_d = HORMONAL_STATES[new_state]["duration"]
    bio.hormonal_state = new_state
    bio.hormonal_days_remaining = random.randint(min_d, max_d)


def initialize_attraction(db: Session, sim_day: int = 1):
    existing = db.query(PhysicalAttraction).count()
    if existing > 0:
        return
    adults = (
        db.query(Character)
        .filter(Character.alive == True, Character.is_minor == False)
        .all()
    )
    for char in adults:
        eligible = [a for a in adults if a.id != char.id]
        n = random.randint(1, min(4, len(eligible)))
        targets = random.sample(eligible, n)
        for target in targets:
            level = random.triangular(0.25, 1.0, 0.55)
            db.add(PhysicalAttraction(
                from_character_id=char.id,
                to_character_id=target.id,
                attraction_level=round(level, 2),
                acknowledged=False,
                created_day=sim_day,
            ))
    db.commit()
    logger.info(f"Attraction matrix initialized for {len(adults)} adult characters.")


def get_biology_prompt(
    character: Character,
    nearby_characters: list,
    sim_day: int,
    db: Session,
) -> str:
    bio = get_or_create_biology(character, db)
    age_float = bio.age_float or _compute_current_age(character, sim_day)
    lines = ["YOUR BODY RIGHT NOW:"]

    # ── Hunger ────────────────────────────────────────────────────────────────
    if bio.hunger <= 1.5:
        lines.append("You feel full and at ease.")
    elif bio.hunger <= 3.5:
        lines.append("A mild hunger at the edge of your awareness.")
    elif bio.hunger <= 5.5:
        lines.append("You are hungry. Your stomach is making itself known.")
    elif bio.hunger <= 7.5:
        lines.append("You are genuinely hungry and it is affecting your patience. You need food.")
    elif bio.hunger <= 9.0:
        lines.append("You are very hungry. It is difficult to think about much else. Your body wants food now.")
    else:
        lines.append("You are starving. Every instinct is pointed at finding something to eat.")

    # ── Fatigue ───────────────────────────────────────────────────────────────
    if bio.fatigue <= 2.0:
        lines.append("You are well-rested. Your body feels capable.")
    elif bio.fatigue <= 4.5:
        lines.append("Mild tiredness — manageable.")
    elif bio.fatigue <= 6.5:
        lines.append("You are tired. Your body wants to lie down.")
    elif bio.fatigue <= 8.5:
        lines.append("You are exhausted. Everything takes more effort than it should.")
    else:
        lines.append("You can barely stay upright. Sleep is no longer optional.")

    # ── Bathroom urgency ──────────────────────────────────────────────────────
    if bio.bathroom_urgency >= 8.0:
        lines.append("Your body has a pressing physical need. You need to be somewhere private, alone, immediately.")
    elif bio.bathroom_urgency >= 5.0:
        lines.append("You need to find somewhere private soon — a physical discomfort becoming difficult to ignore.")

    # ── Age-related health (45+) ───────────────────────────────────────────────
    if not character.is_minor:
        if age_float >= 75:
            lines.append(AGE_HEALTH_PROMPTS["severe_decline"])
        elif age_float >= 65:
            lines.append(AGE_HEALTH_PROMPTS["significant_decline"])
        elif age_float >= 55:
            lines.append(AGE_HEALTH_PROMPTS["moderate_decline"])
        elif age_float >= 45:
            lines.append(AGE_HEALTH_PROMPTS["early_decline"])

    # ── Hormonal state (adults) ────────────────────────────────────────────────
    if not character.is_minor:
        mod = HORMONAL_PROMPTS.get(bio.hormonal_state)
        if mod:
            lines.append(mod)

    # ── Menstrual cycle ────────────────────────────────────────────────────────
    if character.gender == 'F' and bio.menstrual_phase:
        phase = bio.menstrual_phase
        cycle_day = bio.menstrual_cycle_day or 1

        if phase == "menstruation":
            if not bio.first_menstruation_occurred or not bio.menstruation_known:
                lines.append(MENSTRUAL_PROMPTS["menstruation_first"])
            else:
                lines.append(MENSTRUAL_PROMPTS["menstruation_known"])
        elif phase == "luteal" and cycle_day >= 24:
            lines.append(MENSTRUAL_PROMPTS["luteal_late"])
        elif phase == "luteal":
            lines.append(MENSTRUAL_PROMPTS["luteal"])
        elif phase == "ovulation":
            lines.append(MENSTRUAL_PROMPTS["ovulation"])
        elif phase == "follicular":
            lines.append(MENSTRUAL_PROMPTS["follicular"])

    # ── Physical attraction (adults, nearby) ──────────────────────────────────
    if not character.is_minor and nearby_characters:
        nearby_ids = {
            c.id for c in nearby_characters
            if c and not c.is_minor and c.id != character.id
        }
        if nearby_ids:
            attractions = (
                db.query(PhysicalAttraction)
                .filter(
                    PhysicalAttraction.from_character_id == character.id,
                    PhysicalAttraction.to_character_id.in_(nearby_ids),
                    PhysicalAttraction.attraction_level >= 0.25,
                )
                .order_by(PhysicalAttraction.attraction_level.desc())
                .limit(1)
                .all()
            )
            for attr in attractions:
                target = db.query(Character).filter(
                    Character.id == attr.to_character_id
                ).first()
                if not target:
                    continue
                name = target.given_name or target.physical_description[:45]
                if attr.attraction_level >= 0.82:
                    level = "very_high"
                elif attr.attraction_level >= 0.65:
                    level = "high"
                elif attr.attraction_level >= 0.42:
                    level = "medium"
                else:
                    level = "low"
                lines.append(ATTRACTION_PROMPTS[level].format(name=name))

    # ── Minor psychology ───────────────────────────────────────────────────────
    if character.is_minor and nearby_characters:
        nearby_peers = [c for c in nearby_characters if c and c.is_minor and c.id != character.id]
        nearby_adults = [c for c in nearby_characters if c and not c.is_minor]

        if nearby_peers:
            peer = nearby_peers[0]
            peer_name = peer.given_name or peer.physical_description[:30]
            if age_float <= 13:
                lines.append(
                    f"You notice {peer_name} nearby. There is something easy "
                    f"about being near someone your own age."
                )
            else:
                lines.append(
                    f"You are aware of {peer_name} in a way that is harder to name. "
                    f"Not just that they are nearby. Something about them pulls your attention."
                )
        elif nearby_adults:
            if age_float <= 13:
                lines.append(
                    "Everyone here is older. You watch more than you speak. "
                    "There are things happening between the adults that you can feel but cannot fully read."
                )
            else:
                lines.append(
                    "You are old enough to know you are being treated as younger than you feel. "
                    "There is something in the air between the adults that you understand more than they think."
                )

        # Identity formation
        if age_float <= 13:
            lines.append(
                "You are still figuring out who you are in relation to everyone else here. "
                "Some days you feel very small. Other days you feel like you understand "
                "something the older ones have forgotten."
            )
        elif age_float <= 17:
            lines.append(
                "You are aware of yourself in a way that is new and sometimes uncomfortable. "
                "Your body, your feelings, the way people look at you or don't — "
                "all of it is louder than it used to be."
            )

    # ── Pregnancy / infant awareness ─────────────────────────────────────────
    try:
        from simulation.procreation import get_pregnancy_status
        preg = get_pregnancy_status(character, db)
        if preg:
            if preg.get("pregnant"):
                t = preg["trimester"]
                trimester_desc = {
                    1: "early — you may not fully understand what is happening yet",
                    2: "middle — the changes in your body are undeniable now",
                    3: "late — your body is heavy and the birth is coming soon",
                }
                lines.append(
                    f"Something is growing inside you. Your body is changing in ways "
                    f"you do not fully understand. The pregnancy is {trimester_desc.get(t, '')}."
                )
            elif preg.get("has_infant"):
                count = preg["infant_count"]
                names = preg["infant_names"]
                if count == 1:
                    lines.append(
                        f"You have a young child — {names[0]}. "
                        f"The infant depends entirely on you. "
                        f"This changes how you move through the day and what you think about."
                    )
                else:
                    lines.append(
                        f"You have {count} young children. "
                        f"They depend on you and this shapes everything."
                    )
    except Exception:
        pass

    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def get_biological_urgency(character: Character, db: Session) -> dict:
    """
    Returns urgency scores for pair selection prioritization.
    Higher score = more biologically urgent this tick.
    """
    bio = get_or_create_biology(character, db)
    urgency = 0.0
    needs = []

    if bio.hunger > 6.5:
        urgency += bio.hunger / 10.0
        needs.append("hunger")
    if bio.fatigue > 7.0:
        urgency += bio.fatigue / 10.0
        needs.append("fatigue")
    if bio.bathroom_urgency > 6.0:
        urgency += bio.bathroom_urgency / 10.0
        needs.append("bathroom")
    if bio.menstrual_phase == "menstruation" and not bio.menstruation_known:
        urgency += 0.8
        needs.append("first_menstruation")

    return {"urgency": urgency, "needs": needs, "roster_id": character.roster_id}


def get_pending_inception(character, sim_day, db):
    """Import compatibility shim."""
    from database.models import InceptionEvent
    import json
    events = db.query(InceptionEvent).filter(InceptionEvent.injected_at_day == sim_day).all()
    for ev in events:
        targets = json.loads(ev.target_roster_ids_json or "[]")
        if character.roster_id in targets:
            return ev.thought_content
    return None
