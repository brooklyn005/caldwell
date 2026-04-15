"""
engine.py — simulation tick orchestrator.

Each tick runs:
  - conversations_per_tick pair conversations (default 10)
  - group_conversations_per_tick group conversations (default 3)
  - monologue for each character who didn't participate
"""
import asyncio
import json
import logging
import random
from typing import Callable

from sqlalchemy.orm import Session

from config import settings
from database.models import Character, Location, TickLog
from simulation.clock import SimulationClock
from simulation.cost_tracker import CostTracker
from simulation.conversation_runner import run_conversation
from simulation.group_conversation import run_group_conversation
from simulation.monologue import generate_monologue
from simulation.event_detector import scan_dialogues, detect_population_milestones
from simulation.action_processor import process_action_events
from simulation.norm_executor import execute_norm_actions
from simulation.directive_detector import detect_directives
from simulation.scene_builder import build_scene_from_activity
from simulation.action_generator import (
    generate_action, record_action_memory,
    record_biological_satisfaction, get_biological_destination
)
from simulation.social_learning import record_social_observations, maybe_distill_all
from simulation.world_expansion import scan_for_discoveries, update_location_claims
from simulation.procreation import check_conception, check_births, check_infant_maturation
from simulation.biology import tick_biology, initialize_attraction, get_biological_urgency
from simulation.resource_manager import (
    initialize_resources, initialize_status_scores,
    tick_resources, get_resource_status
)
from simulation.environment import check_and_fire_events, get_active_events
from simulation.sexual_encounters import check_sexual_encounters

logger = logging.getLogger("caldwell.engine")

DRIVE_LOCATION_AFFINITY: dict[str, list[str]] = {
    "Curiosity":   ["Caldwell Public Library", "Warehouse Row", "Rooftop Garden", "Riverside Park"],
    "Connection":  ["Central Square", "Community Center", "Bayou Market", "Riverside Park"],
    "Order":       ["The Workshop", "The Schoolhouse", "Community Center", "The Meridian"],
    "Power":       ["Central Square", "Community Center", "The Schoolhouse"],
    "Knowledge":   ["Caldwell Public Library", "The Schoolhouse", "Rooftop Garden"],
    "Comfort":     ["The Meridian", "Lakeview Flats", "Riverside Park", "Bayou Market"],
    "Survival":    ["Warehouse Row", "Lakeview Flats", "The Workshop"],
}


class SimulationEngine:
    def __init__(self, db: Session):
        self.db = db
        self.clock = SimulationClock(db)
        self.cost = CostTracker(db)

    # ── Location movement ─────────────────────────────────────────────────

    def assign_starting_locations(self):
        square = self.db.query(Location).filter(
            Location.name == "Central Square"
        ).first()
        if not square:
            return
        for char in self.db.query(Character).filter(Character.alive == True).all():
            if char.current_location_id is None:
                char.current_location_id = square.id
        self.db.commit()

    def _ensure_initialized(self, sim_day: int):
        """Initialize new systems on first tick."""
        if sim_day == 1:
            initialize_resources(self.db)
            initialize_status_scores(self.db)

    def _personality_wander(self):
        locations = self.db.query(Location).all()
        loc_by_name = {loc.name: loc for loc in locations}
        alive = self.db.query(Character).filter(Character.alive == True).all()

        for char in alive:
            # Biology overrides personality — urgent needs force movement
            bio_dest = get_biological_destination(char, self.db)
            if bio_dest:
                char.current_location_id = bio_dest.id
                continue

            # Otherwise personality-driven movement for ~1/3 of characters
            if random.random() < 0.33:
                preferred = DRIVE_LOCATION_AFFINITY.get(char.core_drive, [])
                preferred_locs = [loc_by_name[n] for n in preferred if n in loc_by_name]
                if preferred_locs and random.random() < 0.65:
                    char.current_location_id = random.choice(preferred_locs).id
                else:
                    char.current_location_id = random.choice(locations).id
        self.db.commit()

    # ── Pair selection ────────────────────────────────────────────────────

    def select_pairs(
        self, n: int, exclude_ids: set,
        forced_pairs: list[tuple[str,str]] | None = None,
        urgent_roster_ids: list[str] | None = None,
    ) -> list[tuple]:
        alive = [
            c for c in self.db.query(Character).filter(Character.alive == True, Character.is_infant == False).all()
            if c.id not in exclude_ids
        ]
        if len(alive) < 2:
            return []

        locations = self.db.query(Location).all()
        pairs = []
        used: set[int] = set()
        char_by_rid = {c.roster_id: c for c in alive}

        # Priority 1: Forced pairs from action events
        if forced_pairs:
            for rid_a, rid_b in forced_pairs:
                ca = char_by_rid.get(rid_a)
                cb = char_by_rid.get(rid_b)
                if not ca or not cb:
                    continue
                if ca.id in used or cb.id in used:
                    continue
                loc = self.db.query(Location).filter(
                    Location.id == ca.current_location_id
                ).first() or random.choice(locations)
                pairs.append((ca, cb, loc))
                used.update([ca.id, cb.id])
                logger.info(f"  Forced pair: {rid_a} ↔ {rid_b}")

        # Priority 2: Biologically urgent characters (up to 2 slots)
        if urgent_roster_ids and len(pairs) < n:
            for rid in urgent_roster_ids[:2]:
                if len(pairs) >= n:
                    break
                ca = char_by_rid.get(rid)
                if not ca or ca.id in used:
                    continue
                # Find a nearby partner
                loc_map: dict[int, list] = {}
                for c in alive:
                    lid = c.current_location_id or 0
                    loc_map.setdefault(lid, []).append(c)
                # Move urgent character to biological destination first
                from simulation.action_generator import get_biological_destination
                bio_dest = get_biological_destination(ca, self.db)
                if bio_dest:
                    ca.current_location_id = bio_dest.id
                    self.db.commit()
                occupants = [
                    c for c in loc_map.get(ca.current_location_id or 0, [])
                    if c.id != ca.id and c.id not in used
                ]
                if not occupants:
                    occupants = [c for c in alive if c.id != ca.id and c.id not in used]
                if not occupants:
                    continue
                cb = random.choice(occupants)
                loc = self.db.query(Location).filter(
                    Location.id == ca.current_location_id
                ).first() or random.choice(locations)
                pairs.append((ca, cb, loc))
                used.update([ca.id, cb.id])
                logger.info(f"  Bio-urgent pair: {rid} ↔ {cb.roster_id}")

        loc_map: dict[int, list[Character]] = {}
        for char in alive:
            lid = char.current_location_id or 0
            loc_map.setdefault(lid, []).append(char)

        for loc in random.sample(locations, len(locations)):
            occupants = [c for c in loc_map.get(loc.id, []) if c.id not in used]
            if len(occupants) >= 2:
                random.shuffle(occupants)
                a, b = occupants[0], occupants[1]
                pairs.append((a, b, loc))
                used.update([a.id, b.id])
            if len(pairs) >= n:
                break

        # Fill remainder with any available pair
        remaining = [c for c in alive if c.id not in used]
        random.shuffle(remaining)
        fallback = random.choice(locations)
        while len(pairs) < n and len(remaining) >= 2:
            pairs.append((remaining.pop(), remaining.pop(), fallback))

        return pairs[:n]

    # ── Group selection ───────────────────────────────────────────────────

    def select_groups(self, n: int, exclude_ids: set) -> list[tuple]:
        """Select n groups of 3-4 characters sharing or near a location."""
        alive = [
            c for c in self.db.query(Character).filter(Character.alive == True).all()
            if c.id not in exclude_ids
        ]
        if len(alive) < 3:
            return []

        locations = self.db.query(Location).all()
        groups = []
        used: set[int] = set()

        loc_map: dict[int, list[Character]] = {}
        for char in alive:
            lid = char.current_location_id or 0
            loc_map.setdefault(lid, []).append(char)

        for loc in random.sample(locations, len(locations)):
            occupants = [c for c in loc_map.get(loc.id, []) if c.id not in used]
            if len(occupants) >= 3:
                random.shuffle(occupants)
                size = random.choice([3, 3, 4])  # mostly 3, sometimes 4
                group = occupants[:size]
                groups.append((group, loc))
                used.update(c.id for c in group)
            if len(groups) >= n:
                break

        # Fill from any available characters
        remaining = [c for c in alive if c.id not in used]
        random.shuffle(remaining)
        fallback = random.choice(locations)
        while len(groups) < n and len(remaining) >= 3:
            size = min(random.choice([3, 4]), len(remaining))
            group = [remaining.pop() for _ in range(size)]
            groups.append((group, fallback))

        return groups[:n]

    # ── The tick ──────────────────────────────────────────────────────────

    async def run_tick(self, broadcast_fn: Callable | None = None) -> dict:
        if self.cost.is_budget_exhausted():
            self.cost.mark_paused()
            logger.warning("Daily budget exhausted — tick skipped.")
            return {"skipped": True, "reason": "budget_exhausted"}

        date = self.clock.advance_one_day()
        sim_day = date["total_days"]
        sim_tick = date["total_ticks"]
        logger.info(f"Tick {sim_tick} — {date['display']}")

        if broadcast_fn:
            await broadcast_fn({"type": "new_day", "data": date})

        self._ensure_initialized(sim_day)
        self._personality_wander()

        # ── Resource tick — food depletion and replenishment ─────────────────────
        resource_state = tick_resources(sim_day, self.db)
        if resource_state.get("shortages"):
            logger.info(f"  FOOD SHORTAGE at: {resource_state['shortages']}")

        # ── Environmental events ──────────────────────────────────────────────────
        env_events = check_and_fire_events(sim_day, self.db)
        for ev in env_events:
            logger.info(f"  ENV EVENT [{ev['type']}]: {ev['description'][:60]}")
            if broadcast_fn:
                await broadcast_fn({"type": "environment_event", "data": ev})

        # ── Biology tick for all characters ──────────────────────────────────────
        alive_for_bio = self.db.query(Character).filter(Character.alive == True, Character.is_infant == False).all()
        bio_events = []
        for char in alive_for_bio:
            event = tick_biology(char, sim_day, self.db)
            if event:
                bio_events.append(event)
        initialize_attraction(self.db, sim_day)

        # Broadcast biology events (deaths, first menstruation)
        for bio_ev in bio_events:
            if bio_ev["type"] == "death":
                logger.info(
                    f"  DEATH: {bio_ev['roster_id']} "
                    f"({bio_ev.get('given_name', 'unnamed')}) has died"
                )
                if broadcast_fn:
                    await broadcast_fn({"type": "character_death", "data": bio_ev})
                # Record significant event
                from simulation.event_detector import log_significant_event
                log_significant_event(
                    self.db, sim_day,
                    "death",
                    f"{bio_ev.get('given_name') or bio_ev['roster_id']} has died",
                    [bio_ev['roster_id']],
                )
            elif bio_ev["type"] == "first_menstruation":
                logger.info(
                    f"  FIRST MENSTRUATION: {bio_ev['roster_id']} "
                    f"({bio_ev.get('given_name', 'unnamed')})"
                )
                if broadcast_fn:
                    await broadcast_fn({"type": "first_menstruation", "data": bio_ev})

        # ── Norm-driven actions — execute before conversations ───────────────────
        norm_scene_pairs = []
        try:
            norm_scene_pairs = execute_norm_actions(sim_day, self.db) or []
        except Exception as e:
            logger.error(f"Norm execution failed: {e}")

        # ── Physical action generation for all characters ───────────────────────
        async def do_action(roster_id: str):
            # Re-fetch character fresh to avoid DetachedInstanceError
            char = self.db.query(Character).filter(
                Character.roster_id == roster_id
            ).first()
            if not char:
                return
            try:
                record_biological_satisfaction(char, sim_day, self.db)
                action_text = await generate_action(char, sim_day, self.db)
                if action_text:
                    record_action_memory(char, action_text, sim_day, self.db)
                    loc = self.db.query(Location).filter(
                        Location.id == char.current_location_id
                    ).first()
                    if broadcast_fn:
                        await broadcast_fn({
                            "type": "character_action",
                            "data": {
                                "roster_id": char.roster_id,
                                "given_name": char.given_name,
                                "gender": char.gender,
                                "action": action_text,
                                "location": loc.name if loc else "Caldwell",
                                "sim_day": sim_day,
                            },
                        })
            except Exception as e:
                logger.error(f"Action generation {roster_id} failed: {e}")

        # Run all character actions concurrently — pass IDs not objects
        alive_all = self.db.query(Character).filter(Character.alive == True, Character.is_infant == False).all()
        alive_roster_ids = [c.roster_id for c in alive_all]
        await asyncio.gather(*[do_action(rid) for rid in alive_roster_ids])

        # ── Sexual encounters ─────────────────────────────────────────────────────
        sexual_events = check_sexual_encounters(sim_day, self.db)
        encounter_forced_pairs = []
        for ev in sexual_events:
            logger.info(
                f"  ENCOUNTER: {ev['character_a']} + {ev['character_b']} "
                f"at {ev['location']} (intensity={ev['intensity']})"
            )
            if broadcast_fn:
                await broadcast_fn({"type": "sexual_encounter", "data": ev})
                if ev.get("scene"):
                    await broadcast_fn({
                        "type": "scene",
                        "data": {
                            "scene": ev["scene"],
                            "location": ev["location"],
                            "sim_day": sim_day,
                            "type": "intimate",
                        }
                    })
            # Force participants into follow-up conversation
            if ev.get("forced_pair"):
                encounter_forced_pairs.append(ev["forced_pair"][:2])
            for wp in ev.get("witness_pairs", []):
                encounter_forced_pairs.append(wp[:2])


        # ── Action events — run BEFORE pair selection ──────────────────────────
        action_results = await process_action_events(
            sim_day, self.db, self.cost, broadcast_fn
        )
        # Collect forced pairs from action events
        forced_pairs = list(encounter_forced_pairs)
        for r in action_results:
            for pair in r.get("forced_pairs", []):
                forced_pairs.append(pair)
        # Add norm-driven scene conversations
        for sp in norm_scene_pairs:
            forced_pairs.append((sp[0], sp[1]))
            if broadcast_fn:
                await broadcast_fn({
                    "type": "action_complete",
                    "data": {
                        "sim_day": sim_day,
                        "scene": "",
                        "characters": list(sp[:2]),
                        "forced_pairs": [list(sp[:2])],
                    },
                })

        n_pairs = settings.conversations_per_tick
        n_groups = settings.group_conversations_per_tick

        # Track who is active this tick
        active_ids: set[int] = set()

        # ── Pair conversations (forced pairs + biological urgency get priority) ──
        # Get biologically urgent characters for priority pairing
        bio_urgent = []
        for c in self.db.query(Character).filter(Character.alive == True, Character.is_infant == False).all():
            urgency = get_biological_urgency(c, self.db)
            if urgency["urgency"] > 0.4:
                bio_urgent.append((c, urgency))
        bio_urgent.sort(key=lambda x: x[1]["urgency"], reverse=True)
        urgent_roster_ids = [c.roster_id for c, _ in bio_urgent[:2]]

        # Build scene context map for norm-driven conversations
        scene_context_map = {}
        for sp in norm_scene_pairs:
            rid_a, rid_b, action_verb = sp
            char_x = self.db.query(Character).filter(Character.roster_id == rid_a).first()
            char_y = self.db.query(Character).filter(Character.roster_id == rid_b).first()
            if char_x and char_y:
                loc = self.db.query(Location).filter(
                    Location.id == char_x.current_location_id
                ).first()
                scene = build_scene_from_activity(char_x, char_y, action_verb, loc, "together")
                if scene:
                    key = frozenset([rid_a, rid_b])
                    scene_context_map[key] = scene

        pairs = self.select_pairs(
            n_pairs, active_ids,
            forced_pairs=forced_pairs,
            urgent_roster_ids=urgent_roster_ids,
        )
        for a, b, _ in pairs:
            active_ids.update([a.id, b.id])

        async def run_pair(a, b, loc):
            try:
                scene = scene_context_map.get(frozenset([a.roster_id, b.roster_id]))
                return await run_conversation(
                    char_a=a, char_b=b, location=loc,
                    sim_day=sim_day, sim_tick=sim_tick,
                    cost_tracker=self.cost, db=self.db,
                    broadcast_fn=broadcast_fn,
                    scene_context=scene,
                )
            except Exception as e:
                logger.error(f"Pair {a.roster_id}↔{b.roster_id} failed: {e}", exc_info=True)
                return []

        # ── Group conversations ───────────────────────────────────────────
        groups = self.select_groups(n_groups, active_ids)
        for group, _ in groups:
            for c in group:
                active_ids.add(c.id)

        async def run_group(group, loc):
            try:
                return await run_group_conversation(
                    characters=group, location=loc,
                    sim_day=sim_day, sim_tick=sim_tick,
                    cost_tracker=self.cost, db=self.db,
                    broadcast_fn=broadcast_fn,
                )
            except Exception as e:
                logger.error(f"Group conversation failed: {e}", exc_info=True)
                return []

        # Run all conversations concurrently
        pair_tasks = [run_pair(a, b, loc) for a, b, loc in pairs]
        group_tasks = [run_group(group, loc) for group, loc in groups]
        all_results = await asyncio.gather(*(pair_tasks + group_tasks))

        pair_results = all_results[:len(pairs)]
        group_results = all_results[len(pairs):]
        total_exchanges = sum(len(r) for r in all_results)

        # ── Internal monologues for idle characters ───────────────────────
        idle = []
        if settings.monologue_enabled:
            alive_all = self.db.query(Character).filter(Character.alive == True).all()
            idle = [c for c in alive_all if c.id not in active_ids]

            async def do_monologue(char):
                try:
                    thought = await generate_monologue(char, sim_day, self.db)
                    if thought and broadcast_fn:
                        await broadcast_fn({
                            "type": "monologue",
                            "data": {
                                "roster_id": char.roster_id,
                                "given_name": char.given_name,
                                "text": thought,
                                "location": (
                                    self.db.query(Location)
                                    .filter(Location.id == char.current_location_id)
                                    .first()
                                    .name if char.current_location_id else "Caldwell"
                                ),
                                "sim_day": sim_day,
                            },
                        })
                except Exception as e:
                    logger.error(f"Monologue {char.roster_id} failed: {e}")

            await asyncio.gather(*[do_monologue(c) for c in idle])

        # ── Event detection ────────────────────────────────────────────────
        scan_dialogues(sim_day, self.db)
        detect_population_milestones(sim_day, self.db)

        # ── Social learning distillation (every 7 days) ───────────────────────
        maybe_distill_all(sim_day, self.db)

        # ── World expansion ──────────────────────────────────────────────────────
        discoveries = scan_for_discoveries(sim_day, self.db)
        update_location_claims(sim_day, self.db)
        for disc in discoveries:
            logger.info(f"  DISCOVERY: {disc['character']} found '{disc['location']}'")
            if broadcast_fn:
                await broadcast_fn({"type": "location_discovered", "data": disc})

        # ── Procreation — conception, births, infant maturation ───────────────
        conception_events = check_conception(sim_day, self.db)
        birth_events = check_births(sim_day, self.db)
        maturation_events = check_infant_maturation(sim_day, self.db)

        for ev in conception_events:
            if broadcast_fn:
                await broadcast_fn({"type": "conception", "data": ev})

        for ev in birth_events:
            logger.info(
                f"  BIRTH: {ev['infant_roster_id']} born to "
                f"{ev['mother']} and {ev.get('father', 'unknown')}"
            )
            if broadcast_fn:
                await broadcast_fn({"type": "birth", "data": ev})
            from simulation.event_detector import log_significant_event
            log_significant_event(
                self.db, sim_day, "birth",
                f"A child ({ev['infant_roster_id']}) was born to "
                f"{ev['mother_name']} and {ev['father_name']}.",
                [ev['mother'], ev.get('father', ev['mother'])],
            )

        for ev in maturation_events:
            if broadcast_fn:
                await broadcast_fn({"type": "infant_maturation", "data": ev})

        # ── Tick log ─────────────────────────────────────────────────────
        summary = (
            f"{date['display']} — "
            f"{len(pairs)} pairs, {len(groups)} groups, "
            f"{total_exchanges} exchanges. "
            f"${self.cost.today_spend():.4f} today."
        )

        self.db.add(TickLog(
            tick_number=sim_tick,
            sim_day=sim_day,
            summary=summary,
            events_json=json.dumps({
                "pairs": [{"a": a.roster_id, "b": b.roster_id, "loc": loc.name} for a, b, loc in pairs],
                "groups": [{"members": [c.roster_id for c in g], "loc": loc.name} for g, loc in groups],
                "idle_count": len(idle) if settings.monologue_enabled else 0,
            }),
            cost_this_tick=self.cost.today_spend(),
        ))
        self.db.commit()

        result = {
            "sim_day": sim_day,
            "sim_tick": sim_tick,
            "date_display": date["display"],
            "pair_conversations": len(pairs),
            "group_conversations": len(groups),
            "total_exchanges": total_exchanges,
            "active_characters": len(active_ids),
            "idle_characters": len(idle) if settings.monologue_enabled else 0,
            "cost_today": self.cost.today_spend(),
            "budget_remaining": self.cost.budget_remaining(),
            "ai_mode": settings.ai_mode,
        }

        if broadcast_fn:
            await broadcast_fn({"type": "tick_complete", "data": result})

        logger.info(summary)
        return result

    # ── Status ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        date = self.clock.current_date_dict()
        cost = self.cost.status_dict()
        alive = self.db.query(Character).filter(Character.alive == True).count()
        total = self.db.query(Character).count()
        recent = (
            self.db.query(TickLog).order_by(TickLog.id.desc()).limit(10).all()
        )
        return {
            "clock": date,
            "cost": cost,
            "population": {"alive": alive, "total": total},
            "ai_mode": settings.ai_mode,
            "ollama_model": settings.ollama_model,
            "recent_ticks": [
                {"day": t.sim_day, "summary": t.summary,
                 "cost": t.cost_this_tick,
                 "at": t.created_at.isoformat() if t.created_at else None}
                for t in recent
            ],
        }
