import json
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    roster_id = Column(String(8), unique=True, nullable=False)   # F-01, M-07 etc.
    gender = Column(String(1), nullable=False)                    # F or M
    age = Column(Integer, nullable=False)
    is_minor = Column(Boolean, default=False)
    physical_description = Column(Text, nullable=False)
    natural_tendency = Column(Text, nullable=False)
    core_drive = Column(String(32), nullable=False)
    personality_traits_json = Column(Text, default="[]")          # JSON list
    private_belief = Column(Text, nullable=True)
    fear = Column(Text, nullable=True)
    ai_model = Column(String(16), nullable=False)                 # "haiku" or "deepseek"
    alive = Column(Boolean, default=True)
    is_infant = Column(Boolean, default=False)

    # ── Differentiated capabilities ────────────────────────────────────────
    # These create natural interdependence — someone is stronger,
    # someone remembers more, someone is more persuasive.
    # Range 1-10. Seeded with variance. Affect what characters can do.
    strength_score = Column(Integer, default=5)      # physical labor, carrying, protection
    memory_score = Column(Integer, default=5)        # knowledge retention, recall accuracy
    persuasion_score = Column(Integer, default=5)    # others defer to them in conversation
    given_name = Column(String(64), nullable=True)                # emerges organically
    current_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    died_at = Column(DateTime, nullable=True)
    age_at_death = Column(Integer, nullable=True)

    # relationships
    memories = relationship("Memory", back_populates="character", cascade="all, delete-orphan")
    outgoing_relationships = relationship(
        "CharacterRelationship",
        foreign_keys="CharacterRelationship.from_character_id",
        back_populates="from_character",
        cascade="all, delete-orphan"
    )
    current_location = relationship("Location", back_populates="occupants")

    @property
    def personality_traits(self):
        return json.loads(self.personality_traits_json or "[]")

    @personality_traits.setter
    def personality_traits(self, val):
        self.personality_traits_json = json.dumps(val)

    def display_name(self):
        return self.given_name if self.given_name else self.roster_id

    def __repr__(self):
        return f"<Character {self.roster_id} age={self.age} model={self.ai_model}>"


class Location(Base):
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), unique=True, nullable=False)
    description = Column(Text, nullable=False)
    location_type = Column(String(32), default="public")  # public, residential, industrial
    capacity = Column(Integer, default=10)
    has_desirable_units = Column(Boolean, default=False)
    desirable_unit_count = Column(Integer, default=0)
    resource_tier = Column(Integer, default=1)  # 1=basic provision, 2=discovered, 3=social

    # ── Emergent world fields ─────────────────────────────────────────────
    is_seed = Column(Boolean, default=True)
    is_emergent = Column(Boolean, default=False)
    discovery_origin = Column(Text, nullable=True)
    discovered_by_id = Column(Text, nullable=True)   # roster_id of discovering character
    discovered_on_day = Column(Integer, nullable=True)
    named_by_id = Column(Text, nullable=True)
    confidence = Column(Float, default=1.0)           # 0.0–1.0
    discovery_stage = Column(String(32), default="confirmed")
    # hint / tentative / confirmed / named / claimed / specialized
    location_category = Column(Text, nullable=True)
    # shelter / trail / clearing / water_source / work_area /
    # ritual_area / ruins / garden / hunting_ground / gathering_place
    territory_type = Column(String(32), default="inside")  # inside / frontier / outside
    danger_level = Column(Float, default=0.0)
    claim_character_id = Column(Text, nullable=True)  # roster_id
    use_count = Column(Integer, default=0)
    map_x = Column(Float, nullable=True)              # 0.0–1.0 fraction of map width
    map_y = Column(Float, nullable=True)              # 0.0–1.0 fraction of map height

    occupants = relationship("Character", back_populates="current_location")


class Memory(Base):
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    sim_day = Column(Integer, nullable=False)
    memory_type = Column(String(32), default="observation")
    # Types: observation, conversation, inception, feeling, discovery
    content = Column(Text, nullable=False)
    emotional_weight = Column(Float, default=0.5)  # 0-1, how strongly remembered
    is_inception = Column(Boolean, default=False)   # true = injected by operator
    created_at = Column(DateTime, default=datetime.utcnow)

    character = relationship("Character", back_populates="memories")


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"
    __table_args__ = (UniqueConstraint("from_character_id", "to_character_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    to_character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    trust_level = Column(Float, default=0.0)        # -1.0 to 1.0
    familiarity = Column(Float, default=0.0)         # 0.0 to 1.0
    bond_type = Column(String(32), nullable=True)    # friend, rival, partner, etc. (emergent)
    last_interacted_day = Column(Integer, nullable=True)
    interaction_count = Column(Integer, default=0)

    from_character = relationship(
        "Character",
        foreign_keys=[from_character_id],
        back_populates="outgoing_relationships"
    )
    to_character = relationship("Character", foreign_keys=[to_character_id])


class Dialogue(Base):
    __tablename__ = "dialogues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    sim_tick = Column(Integer, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    participant_ids_json = Column(Text, nullable=False)  # JSON list of character IDs
    dialogue_json = Column(Text, nullable=False)          # JSON list of {speaker_id, text}
    topic = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def participants(self):
        return json.loads(self.participant_ids_json or "[]")

    @property
    def dialogue(self):
        return json.loads(self.dialogue_json or "[]")


class SimClock(Base):
    __tablename__ = "sim_clock"

    id = Column(Integer, primary_key=True, autoincrement=True)
    current_day = Column(Integer, default=1)
    current_tick = Column(Integer, default=0)      # total ticks since start
    is_running = Column(Boolean, default=False)
    started_at = Column(DateTime, nullable=True)
    last_tick_at = Column(DateTime, nullable=True)
    # American calendar anchor: Year 0, Month 1, Day 1
    sim_year = Column(Integer, default=0)
    sim_month = Column(Integer, default=1)
    sim_day_of_month = Column(Integer, default=1)


class CostLog(Base):
    __tablename__ = "cost_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_utc = Column(String(10), nullable=False)   # YYYY-MM-DD
    ai_model = Column(String(16), nullable=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class DailySpend(Base):
    __tablename__ = "daily_spend"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_utc = Column(String(10), unique=True, nullable=False)
    total_usd = Column(Float, default=0.0)
    is_paused_by_budget = Column(Boolean, default=False)


class TickLog(Base):
    __tablename__ = "tick_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tick_number = Column(Integer, nullable=False)
    sim_day = Column(Integer, nullable=False)
    summary = Column(Text, nullable=True)
    events_json = Column(Text, default="[]")
    cost_this_tick = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class InceptionEvent(Base):
    __tablename__ = "inception_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_character_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    # null target = broadcast to multiple (specified in target_roster_ids_json)
    target_roster_ids_json = Column(Text, default="[]")
    thought_content = Column(Text, nullable=False)
    injected_at_day = Column(Integer, nullable=False)
    operator_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WorldEvent(Base):
    __tablename__ = "world_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    injected_at_day = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    resolved_at_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SatisfactionLog(Base):
    __tablename__ = "satisfaction_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    sim_day = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)   # -1.0 to 1.0
    drive = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CharacterDisposition(Base):
    __tablename__ = "character_dispositions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)
    state = Column(String(16), default="neutral")
    # despairing / frustrated / neutral / content / flourishing
    rolling_average = Column(Float, default=0.0)
    last_updated_day = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SignificantEvent(Base):
    __tablename__ = "significant_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    # Types: first_name, first_meeting, alliance, strong_bond,
    #        conflict, mystery_question, governance, milestone, inception_effect
    description = Column(Text, nullable=False)
    character_ids_json = Column(Text, default="[]")
    location = Column(String(64), nullable=True)
    emotional_weight = Column(Float, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def character_ids(self):
        return json.loads(self.character_ids_json or "[]")


class CharacterBiology(Base):
    """
    Tracks each character's biological state tick by tick.
    These values modify their system prompt and drive behavior
    beyond their base personality.
    """
    __tablename__ = "character_biology"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)

    # Physical needs (0-10 scale)
    hunger = Column(Float, default=4.0)          # 0=full, 10=starving
    fatigue = Column(Float, default=3.0)          # 0=rested, 10=exhausted
    bathroom_urgency = Column(Float, default=1.0) # 0=fine, 10=urgent
    physical_comfort = Column(Float, default=7.0) # 0=miserable, 10=comfortable

    # Biological tracking
    last_ate_day = Column(Integer, default=0)
    last_slept_day = Column(Integer, default=0)
    last_bathroom_day = Column(Integer, default=0)

    # Hormonal state (adults only, minors stay "baseline")
    # Values: baseline, restless, heightened, depleted, aggressive, calm
    hormonal_state = Column(String(32), default="baseline")
    hormonal_days_remaining = Column(Integer, default=0)

    updated_day = Column(Integer, default=0)

    # ── Menstrual cycle (adult females only) ───────────────────────────────
    # cycle_day: 1-28, increments each sim day
    # phase: follicular(1-13), ovulation(14), luteal(15-28), menstruation(1-5)
    menstrual_cycle_day = Column(Integer, nullable=True)     # None = not applicable
    menstrual_phase = Column(String(32), nullable=True)      # follicular/ovulation/luteal/menstruation
    first_menstruation_occurred = Column(Boolean, default=False)
    menstruation_known = Column(Boolean, default=False)      # has character processed this in conversation

    # ── Aging ─────────────────────────────────────────────────────────────
    age_float = Column(Float, nullable=True)                 # precise age including fractional years
    last_age_update_day = Column(Integer, default=0)
    health_score = Column(Float, default=1.0)                # 1.0=perfect, declines with age 45+


class PhysicalAttraction(Base):
    """
    Attraction between adult characters.
    One-directional — mutual attraction is possible but not guaranteed.
    NEVER populated for minor characters.
    """
    __tablename__ = "physical_attraction"

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    to_character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    attraction_level = Column(Float, default=0.0)  # 0.0-1.0
    acknowledged = Column(Boolean, default=False)   # has this surfaced in conversation
    created_day = Column(Integer, default=1)


class ActionEvent(Base):
    """
    A forced scene injected by the operator.
    Unlike inception (private thought), an action event is something
    that actually happens — shared between participants, witnessed,
    and remembered by everyone involved.

    Examples:
    - Character A walks in on Character B in a private moment
    - Two characters find a locked room together
    - A character publicly collapses from exhaustion
    - Someone finds an object that raises questions
    """
    __tablename__ = "action_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Primary participants — characters who are IN the scene
    participant_roster_ids_json = Column(Text, nullable=False, default="[]")
    # Witnesses — characters who observe but aren't primary
    witness_roster_ids_json = Column(Text, default="[]")
    # The scene as the operator describes it
    scene_description = Column(Text, nullable=False)
    # Perspective framing — how the scene is described to each side
    # "observer": F-12 walks in on the scene
    # "subject": M-10 is seen
    # "mutual": both experience equally
    perspective = Column(String(16), default="mutual")
    inject_on_day = Column(Integer, nullable=False)
    operator_note = Column(Text, nullable=True)
    processed = Column(Boolean, default=False)
    processed_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def participant_ids(self):
        return json.loads(self.participant_roster_ids_json or "[]")

    @property
    def witness_ids(self):
        return json.loads(self.witness_roster_ids_json or "[]")


class BehavioralEvidence(Base):
    """
    Records what conversational approach each character used and what
    outcome it produced. Accumulates into learned behavioral tendencies.
    Both first-hand experience and social observation are recorded here,
    weighted differently in the distillation step.
    """
    __tablename__ = "behavioral_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    sim_day = Column(Integer, nullable=False)
    approach = Column(String(32), nullable=False)
    # assertive, collaborative, vulnerable, philosophical, nurturing,
    # challenging, analytical, withdrawn, playful, protective
    outcome_score = Column(Float, nullable=False)    # -1.0 to 1.0
    trust_delta = Column(Float, default=0.0)          # change in trust
    partner_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    location = Column(String(64), nullable=True)
    is_social_observation = Column(Boolean, default=False)
    observed_character_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    compatibility_weight = Column(Float, default=1.0)  # 0-1, reduced for obs.
    created_at = Column(DateTime, default=datetime.utcnow)


class BehavioralTendency(Base):
    """
    Distilled learned tendency per character.
    Generated every 7 sim days from BehavioralEvidence.
    Injected into system prompt as a natural language paragraph.
    """
    __tablename__ = "behavioral_tendencies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)
    tendency_text = Column(Text, nullable=True)         # injected into prompt
    dominant_approach = Column(String(32), nullable=True)
    approaches_json = Column(Text, default="{}")        # approach -> avg score
    evidence_count = Column(Integer, default=0)
    last_updated_day = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow)

    @property
    def approaches(self):
        return json.loads(self.approaches_json or "{}")


class EmergentLocation(Base):
    __tablename__ = "emergent_locations"
    id = Column(Integer, primary_key=True, autoincrement=True)
    location_id = Column(Integer, ForeignKey("locations.id"), unique=True, nullable=False)
    discovered_by_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    claimed_by_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    discovery_day = Column(Integer, nullable=False)
    discovery_description = Column(Text, nullable=True)
    is_outside = Column(Boolean, default=False)
    origin_type = Column(String(32), default="discovered")
    map_x = Column(Float, nullable=True)
    map_y = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class LocationClaim(Base):
    __tablename__ = "location_claims"
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    visit_count = Column(Integer, default=0)
    claim_strength = Column(Float, default=0.0)
    first_visit_day = Column(Integer, nullable=False)
    last_visit_day = Column(Integer, nullable=False)
    publicly_claimed = Column(Boolean, default=False)
    claim_phrase = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Age tracking ──────────────────────────────────────────────────────────────
# We add age_years as a float to Character so we can track fractional aging.
# This is computed from sim days elapsed and seeded age.
# Original 'age' column stays as integer seeded age for reference.


class Pregnancy(Base):
    """
    Tracks pregnancies in Caldwell — conception through birth.
    """
    __tablename__ = "pregnancies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mother_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    father_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    conception_day = Column(Integer, nullable=False)
    expected_birth_day = Column(Integer, nullable=False)
    actual_birth_day = Column(Integer, nullable=True)
    born_character_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    status = Column(String(32), default="pregnant")  # pregnant / born / miscarried
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Resource System ───────────────────────────────────────────────────────────

class ResourcePool(Base):
    """
    Tracks food and supply availability at each location.
    Food is NOT infinite — it appears in batches and runs out.
    This is the engine of social complexity.
    """
    __tablename__ = "resource_pools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    resource_type = Column(String(32), default="food")   # food, water, medicine
    quantity = Column(Float, default=0.0)
    max_quantity = Column(Float, default=90.0)
    last_replenish_day = Column(Integer, default=0)
    replenish_interval = Column(Integer, default=3)      # every N sim days
    replenish_amount = Column(Float, default=45.0)       # slightly scarce
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Status Economy ────────────────────────────────────────────────────────────

class StatusScore(Base):
    """
    Social status per character — a real currency that affects
    how others treat them and what they can access.
    """
    __tablename__ = "status_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)
    score = Column(Float, default=50.0)     # 0-100, starts at 50
    score_history = Column(Text, default="[]")  # JSON list of (day, score) tuples
    times_shared_food = Column(Integer, default=0)
    times_hoarded = Column(Integer, default=0)
    times_helped = Column(Integer, default=0)
    times_deferred_to = Column(Integer, default=0)
    updated_day = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Environmental Events ──────────────────────────────────────────────────────

class EnvironmentEvent(Base):
    """
    Periodic pressure events that force collective response.
    These are the conditions under which governance, religion,
    and ideology actually emerge.
    """
    __tablename__ = "environment_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False)   # food_shortage, cold_snap, etc.
    description = Column(Text, nullable=False)
    start_day = Column(Integer, nullable=False)
    end_day = Column(Integer, nullable=True)          # None = ongoing
    severity = Column(Float, default=1.0)             # 0.5=mild, 1.0=normal, 2.0=severe
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Sexual Encounters ─────────────────────────────────────────────────────────

class SexualEncounter(Base):
    """
    Records sexual encounters between adult characters.
    Adults only — minors never.
    No cultural framework pre-loaded. Characters discover this themselves.
    """
    __tablename__ = "sexual_encounters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_a_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    character_b_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    sim_day = Column(Integer, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    witness_ids_json = Column(Text, default="[]")     # who saw it
    initiated_by = Column(Integer, ForeignKey("characters.id"), nullable=True)
    intensity = Column(Float, default=0.5)            # 0-1
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Norm Records ──────────────────────────────────────────────────────────────

class NormRecord(Base):
    """
    Emerging community norms — behaviors that have been repeated
    enough times that witnesses have developed expectations.
    These are NOT imposed — they emerge from observed behavior.
    """
    __tablename__ = "norm_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    norm_type = Column(String(64), nullable=False)    # nudity_public, food_sharing, etc.
    description = Column(Text, nullable=False)        # what seems to be understood
    emerged_day = Column(Integer, nullable=False)
    strength = Column(Float, default=0.1)             # 0-1, grows with reinforcement
    violated_count = Column(Integer, default=0)
    reinforced_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Behavior Witness Records ──────────────────────────────────────────────────

class BehaviorWitness(Base):
    """
    Tracks what behaviors characters have witnessed.
    The raw material from which norms emerge.
    """
    __tablename__ = "behavior_witnesses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    witness_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    behavior_type = Column(String(64), nullable=False)
    actor_id = Column(Integer, ForeignKey("characters.id"), nullable=True)
    description = Column(Text, nullable=True)
    sim_day = Column(Integer, nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Scene Records ─────────────────────────────────────────────────────────────

class Scene(Base):
    """
    A scene is the primary unit of simulation output.
    Replaces the old Dialogue table as the canonical record of what happened.
    Each scene has a type, a pressure it responds to, and full dialogue.
    """
    __tablename__ = "scenes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    scene_type = Column(String(64), nullable=False)
    # preparation, return, distribution, argument, correction,
    # resentment, quiet_intimacy, gossip, teaching, status_challenge, ritual
    pressure_type = Column(String(64), nullable=True)
    # the daily pressure that generated this scene
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    participant_ids_json = Column(Text, nullable=False, default="[]")
    dialogue_json = Column(Text, nullable=False, default="[]")
    scene_summary = Column(Text, nullable=True)
    content_category = Column(String(32), nullable=True)
    # work, community, philosophy, controlling, conflict, connection,
    # sexual, self, knowledge, survival, grief, ritual, gossip
    # written after scene runs — 2-3 sentence consequence summary
    norm_ids_referenced_json = Column(Text, default="[]")
    consequence_json = Column(Text, default="{}")
    # {relationship_changes, norm_changes, status_changes}
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Open Questions ────────────────────────────────────────────────────────────

class OpenQuestion(Base):
    """
    An unresolved question a character is actively carrying.
    Not a memory of what happened — a forward-looking drive to understand.
    Persists across ticks until resolved or abandoned.
    """
    __tablename__ = "open_questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    question_text = Column(Text, nullable=False)
    # The question in the character's own voice — one sentence, present tense
    source_type = Column(String(32), default="conversation")
    # conversation, observation, memory, discovery
    source_day = Column(Integer, nullable=False)
    # Day the question emerged
    emerged_day = Column(Integer, nullable=False)
    intensity = Column(Float, default=0.7)
    # 0-1: how much it's driving behavior. Decays toward resolution.
    resolved = Column(Boolean, default=False)
    resolution_text = Column(Text, nullable=True)
    # What closed it, if anything
    resolved_day = Column(Integer, nullable=True)
    last_surfaced_day = Column(Integer, nullable=True)
    # Last tick it appeared in a scene
    times_surfaced = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    # How many conversations have tried to address this question
    dropped = Column(Boolean, default=False)
    # Dropped after MAX_ATTEMPTS without resolution — archived, not deleted
    current_understanding = Column(Text, nullable=True)
    # Running summary of what the character knows so far about this question
    intermediary_count = Column(Integer, default=0)
    # How many intermediary partial conversations have happened
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Silent Actions ────────────────────────────────────────────────────────────

class SilentAction(Base):
    """
    Off-screen activity: what characters do when not in a scene.
    Generates memories and minor resource effects without dialogue.
    """
    __tablename__ = "silent_actions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    actor_ids_json = Column(Text, default="[]")          # JSON list of roster_ids
    action_type = Column(String(64), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    description = Column(Text, nullable=False)
    resource_delta = Column(Float, default=0.0)          # effect on food pool
    visibility = Column(String(16), default="private")   # private or witnessed
    witness_ids_json = Column(Text, default="[]")        # JSON list of roster_ids
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def actor_ids(self):
        return json.loads(self.actor_ids_json or "[]")


# ── Consequence Records ───────────────────────────────────────────────────────

class ConsequenceRecord(Base):
    """
    One record per scene consequence. Persists what actually changed as a
    result of a scene — relationships, norms, emotional residue, etc.
    """
    __tablename__ = "consequence_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    source_type = Column(String(32), default="scene")    # scene, biology, norm
    consequence_type = Column(String(64), nullable=False)
    # norm_reinforced, emotional_residue, knowledge_gained, public_exposure, etc.
    affected_ids_json = Column(Text, default="[]")       # JSON list of roster_ids
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=True)
    description = Column(Text, nullable=False)
    severity = Column(Float, default=0.5)                # 0-1
    persistence = Column(Integer, default=7)             # days this consequence remains active
    reader_visible = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def affected_ids(self):
        return json.loads(self.affected_ids_json or "[]")


# ── Civilization Threads ──────────────────────────────────────────────────────

class CivilizationThread(Base):
    """
    Active narrative threads — relationships, rivalries, rituals, mysteries.
    Tracks ongoing story arcs that span multiple days and scenes.
    """
    __tablename__ = "civilization_threads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_type = Column(String(32), nullable=False)
    # romance, rivalry, role_emergence, ritual_formation, authority_shift, mystery
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    participant_ids_json = Column(Text, default="[]")    # JSON list of roster_ids
    heat = Column(Float, default=0.5)                    # 0-1, intensity of thread
    status = Column(String(32), default="active")
    # active, intensifying, dormant, resolved, faded
    origin_day = Column(Integer, nullable=False)
    last_advanced_day = Column(Integer, nullable=True)
    advance_count = Column(Integer, default=1)
    resolved_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def participant_ids(self):
        return json.loads(self.participant_ids_json or "[]")


# ── Character Transient State ─────────────────────────────────────────────────

class CharacterTransientState(Base):
    """
    One row per character per day — their current emotional weather.
    Derived each tick from disposition, biology, and recent consequences.
    Injected into system prompts to shape that day's behavior.
    """
    __tablename__ = "character_transient_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), nullable=False)
    sim_day = Column(Integer, nullable=False)
    emotional_tags_json = Column(Text, default="[]")     # JSON list of tag strings
    hunger_level = Column(Float, default=4.0)            # mirrors biology.hunger
    fatigue_level = Column(Float, default=3.0)           # mirrors biology.fatigue
    shame_active = Column(Boolean, default=False)
    hope_active = Column(Boolean, default=False)
    obsession_text = Column(Text, nullable=True)         # one-line fixation, if any
    guardedness = Column(Float, default=0.3)             # 0-1
    loneliness = Column(Float, default=0.3)              # 0-1
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def emotional_tags(self):
        return json.loads(self.emotional_tags_json or "[]")

    @emotional_tags.setter
    def emotional_tags(self, val):
        self.emotional_tags_json = json.dumps(val)


# ── Day Composition ───────────────────────────────────────────────────────────

class DayComposition(Base):
    """
    One row per tick — records the day's archetype, planned scene slots,
    and what actually ran. Source of truth for daybook generation.
    """
    __tablename__ = "day_compositions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, nullable=False)
    day_archetype = Column(String(64), nullable=True)    # hungry_day, tension_day, etc.
    day_label = Column(String(128), nullable=True)       # human-readable label
    required_slots_json = Column(Text, default="[]")     # planned slot categories
    actual_scenes_json = Column(Text, default="[]")      # scene types that ran
    suppressed_pressures_json = Column(Text, default="[]")
    pair_cooldowns_json = Column(Text, default="{}")
    daybook_text = Column(Text, nullable=True)           # prose summary of the day
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Reader Summary ────────────────────────────────────────────────────────────

class ReaderSummary(Base):
    """
    One row per day — the assembled reader-facing view of what happened.
    Combines daybook prose, active threads, character arcs, and place updates.
    """
    __tablename__ = "reader_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sim_day = Column(Integer, unique=True, nullable=False)
    daybook = Column(Text, nullable=True)                # prose paragraph
    active_threads_json = Column(Text, default="[]")
    shifting_roles_json = Column(Text, default="[]")
    consequences_json = Column(Text, default="[]")
    place_updates_json = Column(Text, default="[]")
    character_arcs_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Social Roles ──────────────────────────────────────────────────────────────

class SocialRole(Base):
    """
    One row per character — their emergent social role within the community.
    Updated every 7 sim days from behavioral evidence. Injected into prompts
    when confidence is high enough to be publicly visible.
    """
    __tablename__ = "social_roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey("characters.id"), unique=True, nullable=False)
    primary_role = Column(String(64), nullable=True)
    # teacher, guardian, caretaker, truth_teller, chronicler, etc.
    secondary_role = Column(String(64), nullable=True)
    role_confidence = Column(Float, default=0.0)         # 0-1, evidence strength
    public_visibility = Column(Float, default=0.0)       # 0-1, how visible this role is
    public_reputation = Column(Text, nullable=True)      # natural-language phrase
    emerged_day = Column(Integer, nullable=True)
    last_reinforced_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Location Memory ───────────────────────────────────────────────────────────

class LocationMemory(Base):
    """
    One row per location — accumulated social personality of each place.
    Updated after every scene that occurs there. Injected into scene prompts
    to give characters a sense of the space's history and charge.
    """
    __tablename__ = "location_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    location_id = Column(Integer, ForeignKey("locations.id"), unique=True, nullable=False)
    first_recorded_day = Column(Integer, nullable=True)
    scene_counts_json = Column(Text, default="{}")       # {scene_type: count}
    identity_tags_json = Column(Text, default="[]")      # emergent identity tags
    dominant_mood = Column(String(64), nullable=True)
    privacy_score = Column(Float, nullable=True)         # 0-1
    charge_level = Column(Float, nullable=True)          # 0-1, emotional intensity
    significant_events_json = Column(Text, default="[]") # [{day, summary}, ...]
    last_notable_event = Column(Text, nullable=True)
    last_notable_day = Column(Integer, nullable=True)
    who_controls = Column(String(64), nullable=True)     # roster_id of dominant character
    who_avoids = Column(Text, default="[]")              # JSON list of roster_ids
    updated_at = Column(DateTime, default=datetime.utcnow)

    @property
    def scene_counts(self):
        return json.loads(self.scene_counts_json or "{}")

    @property
    def identity_tags(self):
        return json.loads(self.identity_tags_json or "[]")

    @identity_tags.setter
    def identity_tags(self, val):
        self.identity_tags_json = json.dumps(val)
