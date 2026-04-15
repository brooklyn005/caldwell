"""
engine.py — scene-driven simulation tick orchestrator.

Phase 1 upgrade: All new systems wired in.

Each day now:
  1. Advance clock, resources, biology, environment
  2. Update transient emotional states
  3. Execute norm actions
  4. Process injected operator events
  5. COMPOSE the day (Daily Composition Engine — replaces pressure→scene chain)
  6. Run scenes concurrently
  7. Generate consequences after each scene
  8. Run silent actions for all unused characters
  9. Update social roles (every 7 days)
  10. Events, social learning, world expansion, procreation
  11. Generate reader summary (daybook, threads, arcs)
  12. Tick log
"""
import asyncio
import json
import logging
import random
from typing import Callable

from sqlalchemy.orm import Session

from config import settings
from database.models import Character, Location, TickLog, Scene as SceneRecord
from simulation.clock import SimulationClock
from simulation.cost_tracker import CostTracker
from simulation.conversation_runner import run_conversation
from simulation.group_conversation import run_group_conversation
from simulation.event_detector import scan_dialogues, detect_population_milestones
from simulation.action_processor import process_action_events
from simulation.norm_executor import execute_norm_actions
from simulation.departure import check_departures
from simulation.social_learning import maybe_distill_all
from simulation.world_expansion import scan_for_discoveries, update_location_claims
from simulation.scene_categorizer import categorize_scene
from simulation.procreation import check_conception, check_births, check_infant_maturation
from simulation.biology import tick_biology, initialize_attraction
from simulation.resource_manager import (
    initialize_resources, initialize_status_scores, tick_resources,
)
from simulation.environment import check_and_fire_events

# ── New Phase 1 systems ───────────────────────────────────────────────────────
from simulation.daily_composer import compose_day
from simulation.consequence_engine import generate_consequences_from_scene
from simulation.silent_actions import generate_daily_silent_actions
from simulation.transient_state import update_all_transient_states
from simulation.daybook import generate_reader_summary
from simulation.social_spread import propagate_scene_aftermath
from simulation.open_question import prune_open_questions

logger = logging.getLogger("caldwell.engine")


class SimulationEngine:
    def __init__(self, db: Session):
        self.db = db
        self.clock = SimulationClock(db)
        self.cost = CostTracker(db)

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
        if sim_day == 1:
            initialize_resources(self.db)
            initialize_status_scores(self.db)

    def _personality_wander(self):
        """Move characters toward locations that match their drives."""
        locations = self.db.query(Location).all()
        loc_by_name = {loc.name: loc for loc in locations}
        alive = self.db.query(Character).filter(Character.alive == True).all()

        DRIVE_AFFINITY = {
            "Curiosity":   ["Caldwell Public Library", "Warehouse Row", "Riverside Park"],
            "Connection":  ["Central Square", "Community Center", "Bayou Market"],
            "Order":       ["The Workshop", "Community Center", "The Meridian"],
            "Power":       ["Central Square", "Community Center"],
            "Knowledge":   ["Caldwell Public Library", "The Schoolhouse"],
            "Comfort":     ["The Meridian", "Lakeview Flats", "Riverside Park"],
            "Survival":    ["Warehouse Row", "Lakeview Flats"],
        }

        for char in alive:
            if random.random() > 0.25:
                continue
            preferred = DRIVE_AFFINITY.get(char.core_drive, [])
            preferred_locs = [loc_by_name[n] for n in preferred if n in loc_by_name]
            if preferred_locs and random.random() < 0.7:
                char.current_location_id = random.choice(preferred_locs).id
            else:
                char.current_location_id = random.choice(locations).id
        self.db.commit()

    # ── The tick ──────────────────────────────────────────────────────────────

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

        # ── Resource tick ─────────────────────────────────────────────────────
        resource_state = tick_resources(sim_day, self.db)
        if resource_state.get("shortages"):
            logger.info(f"  FOOD SHORTAGE at: {resource_state['shortages']}")

        # ── Environmental events ──────────────────────────────────────────────
        env_events = check_and_fire_events(sim_day, self.db)
        for ev in env_events:
            logger.info(f"  ENV EVENT [{ev['type']}]: {ev['description'][:60]}")
            if broadcast_fn:
                await broadcast_fn({"type": "environment_event", "data": ev})

        # ── Biology tick ──────────────────────────────────────────────────────
        alive_for_bio = self.db.query(Character).filter(
            Character.alive == True, Character.is_infant == False
        ).all()
        bio_events = []
        for char in alive_for_bio:
            event = tick_biology(char, sim_day, self.db)
            if event:
                bio_events.append(event)
        initialize_attraction(self.db, sim_day)

        for bio_ev in bio_events:
            if bio_ev["type"] == "death":
                logger.info(
                    f"  DEATH: {bio_ev['roster_id']} "
                    f"({bio_ev.get('given_name', 'unnamed')}) has died"
                )
                if broadcast_fn:
                    await broadcast_fn({"type": "character_death", "data": bio_ev})
                from simulation.event_detector import log_significant_event
                log_significant_event(
                    self.db, sim_day, "death",
                    f"{bio_ev.get('given_name') or bio_ev['roster_id']} has died",
                    [bio_ev["roster_id"]],
                )
            elif bio_ev["type"] == "first_menstruation":
                if broadcast_fn:
                    await broadcast_fn({"type": "first_menstruation", "data": bio_ev})

        # ── Transient emotional states ─────────────────────────────────────────
        try:
            update_all_transient_states(sim_day, self.db)
        except Exception as e:
            logger.warning(f"Transient state update failed: {e}")

        # ── Norm executor — writes action memories ────────────────────────────
        try:
            execute_norm_actions(sim_day, self.db)
        except Exception as e:
            logger.error(f"Norm execution failed: {e}")

        # ── Operator-injected action events ───────────────────────────────────
        injected_plans = []
        try:
            injected_plans = await process_action_events(
                sim_day, self.db, self.cost, broadcast_fn
            ) or []
        except Exception as e:
            logger.error(f"Action events failed: {e}")

        # ── DAILY COMPOSITION ENGINE ──────────────────────────────────────────
        # This replaces the old pressure → question → scene selection chain.
        # compose_day() picks the archetype, fills required slots, enforces
        # pair/location cooldowns, caps argument and open_question to 1/day.
        try:
            exclude_ids = {c.id for plan in injected_plans for c in plan.characters}
            remaining_slots = max(0, settings.conversations_per_tick - len(injected_plans))
            pressure_scene_plans, day_comp = compose_day(
                sim_day=sim_day,
                db=self.db,
                max_scenes=remaining_slots,
                exclude_ids=exclude_ids,
                injected_plans=injected_plans,
            )
            if broadcast_fn and day_comp:
                await broadcast_fn({
                    "type": "day_composed",
                    "data": {
                        "archetype": day_comp.day_archetype,
                        "label": day_comp.day_label,
                        "scenes": day_comp.actual_scenes_json,
                    }
                })
        except Exception as e:
            logger.error(f"Daily composition failed: {e}", exc_info=True)
            pressure_scene_plans = []
            day_comp = None

        # Final scene list: injected first, composed second
        scene_plans = injected_plans + pressure_scene_plans

        # ── Run scenes concurrently ───────────────────────────────────────────
        async def run_scene(plan):
            if len(plan.characters) < 2:
                return []
            char_a, char_b = plan.characters[0], plan.characters[1]

            # Move participants to scene location
            if plan.location:
                for char in plan.characters:
                    char.current_location_id = plan.location.id
                self.db.commit()

            if broadcast_fn:
                await broadcast_fn({
                    "type": "scene_start",
                    "data": {
                        "scene_type": plan.scene_type,
                        "pressure_type": plan.pressure_type,
                        "is_group": plan.is_group,
                        "is_injected": plan.is_injected,
                        "char_a": char_a.roster_id,
                        "char_b": char_b.roster_id,
                        "char_a_name": char_a.given_name,
                        "char_b_name": char_b.given_name,
                        "location": plan.location.name if plan.location else "",
                        "sim_day": sim_day,
                        "dramatic_purpose": plan.dramatic_purpose,
                    },
                })

            try:
                if plan.is_group:
                    exchanges = await run_group_conversation(
                        characters=plan.characters,
                        location=plan.location,
                        sim_day=sim_day,
                        sim_tick=sim_tick,
                        cost_tracker=self.cost,
                        db=self.db,
                        broadcast_fn=broadcast_fn,
                        scene_context=plan.scene_context,
                        dramatic_purpose=plan.dramatic_purpose,
                    )
                else:
                    exchanges = await run_conversation(
                        char_a=char_a,
                        char_b=char_b,
                        location=plan.location,
                        sim_day=sim_day,
                        sim_tick=sim_tick,
                        cost_tracker=self.cost,
                        db=self.db,
                        broadcast_fn=broadcast_fn,
                        scene_context=plan.scene_context,
                        scene_type=plan.scene_type,
                        dramatic_purpose=plan.dramatic_purpose,
                    )

                # Write Scene record
                try:
                    category = categorize_scene(exchanges, plan.scene_type)
                    self.db.add(SceneRecord(
                        sim_day=sim_day,
                        scene_type=plan.scene_type,
                        pressure_type=plan.pressure_type,
                        location_id=plan.location.id if plan.location else None,
                        participant_ids_json=json.dumps([c.id for c in plan.characters]),
                        dialogue_json=json.dumps(exchanges),
                        content_category=category,
                    ))
                    self.db.commit()
                except Exception as write_err:
                    logger.warning(f"Scene record write failed: {write_err}")

                # ── Consequence generation ─────────────────────────────────────
                # Every scene that runs leaves marks on the world.
                try:
                    await generate_consequences_from_scene(
                        scene_type=plan.scene_type,
                        exchanges=exchanges,
                        participants=plan.characters,
                        location=plan.location,
                        sim_day=sim_day,
                        cost_tracker=self.cost,
                        db=self.db,
                    )
                except Exception as cons_err:
                    logger.warning(f"Consequence generation failed: {cons_err}")

                # ── Witness memories and secondhand rumors ─────────────────────
                try:
                    propagate_scene_aftermath(
                        scene_type=plan.scene_type,
                        exchanges=exchanges,
                        participants=plan.characters,
                        location=plan.location,
                        sim_day=sim_day,
                        db=self.db,
                    )
                except Exception as spread_err:
                    logger.warning(f"Social spread failed: {spread_err}")

                return exchanges

            except Exception as e:
                a_id = getattr(char_a, "roster_id", "?")
                b_id = getattr(char_b, "roster_id", "?")
                logger.error(
                    f"Scene {plan.scene_type} ({a_id}↔{b_id}) failed: {e}",
                    exc_info=True,
                )
                return []

        scene_results = await asyncio.gather(*[run_scene(p) for p in scene_plans])
        total_exchanges = sum(len(r) for r in scene_results)

        # ── Silent actions — off-screen life ──────────────────────────────────
        # Characters not in a featured scene still do things: gather food,
        # visit each other privately, repair tools, mourn, avoid people.
        # These update resource pools, relationships, and location state
        # without generating dialogue.
        try:
            used_in_scenes = {c.id for plan in scene_plans for c in plan.characters}
            generate_daily_silent_actions(sim_day, self.db)
        except Exception as e:
            logger.warning(f"Silent actions failed: {e}")

        # ── Open question pruning ─────────────────────────────────────────────
        try:
            prune_open_questions(sim_day, self.db)
        except Exception as e:
            logger.warning(f"Open question pruning failed: {e}")

        # ── Social role updates (every 7 days) ───────────────────────────────
        if sim_day % 7 == 0:
            try:
                from simulation.social_roles import update_social_roles
                update_social_roles(sim_day, self.db)
            except Exception as e:
                logger.warning(f"Social role update failed: {e}")

        # ── Organic departures ────────────────────────────────────────────────
        departures = check_departures(sim_day, self.db)
        for dep in departures:
            logger.info(f"  DEPARTURE: {dep.get('given_name') or dep['roster_id']}")
            if broadcast_fn:
                await broadcast_fn({"type": "character_departure", "data": dep})

        # ── Event detection ───────────────────────────────────────────────────
        scan_dialogues(sim_day, self.db)
        detect_population_milestones(sim_day, self.db)

        # ── Social learning (every 7 days) ────────────────────────────────────
        maybe_distill_all(sim_day, self.db)

        # ── World expansion ───────────────────────────────────────────────────
        discoveries = scan_for_discoveries(sim_day, self.db)
        update_location_claims(sim_day, self.db)
        for disc in discoveries:
            if broadcast_fn:
                await broadcast_fn({"type": "location_discovered", "data": disc})

        # ── Procreation ───────────────────────────────────────────────────────
        conception_events = check_conception(sim_day, self.db)
        birth_events = check_births(sim_day, self.db)
        maturation_events = check_infant_maturation(sim_day, self.db)

        for ev in conception_events:
            if broadcast_fn:
                await broadcast_fn({"type": "conception", "data": ev})
        for ev in birth_events:
            logger.info(f"  BIRTH: {ev['infant_roster_id']}")
            if broadcast_fn:
                await broadcast_fn({"type": "birth", "data": ev})
            from simulation.event_detector import log_significant_event
            log_significant_event(
                self.db, sim_day, "birth",
                f"A child ({ev['infant_roster_id']}) was born to "
                f"{ev['mother_name']} and {ev['father_name']}.",
                [ev["mother"], ev.get("father", ev["mother"])],
            )
        for ev in maturation_events:
            if broadcast_fn:
                await broadcast_fn({"type": "infant_maturation", "data": ev})

        # ── Reader summary — daybook, threads, arcs ───────────────────────────
        reader_summary = None
        try:
            reader_summary = generate_reader_summary(sim_day, self.db)
            if broadcast_fn and reader_summary:
                from simulation.daybook import format_summary_for_api
                summary_data = format_summary_for_api(reader_summary, self.db)
                await broadcast_fn({"type": "reader_summary", "data": summary_data})
        except Exception as e:
            logger.warning(f"Reader summary generation failed: {e}")

        # ── Tick log ──────────────────────────────────────────────────────────
        alive_count = self.db.query(Character).filter(Character.alive == True).count()
        scene_types_list = [s.scene_type for s in scene_plans]

        archetype_label = day_comp.day_archetype if day_comp else "unknown"
        summary = (
            f"{date['display']} [{archetype_label}] — "
            f"scenes: {', '.join(scene_types_list)} | "
            f"{total_exchanges} exchanges | "
            f"{alive_count} alive | "
            f"${self.cost.today_spend():.4f}"
        )

        self.db.add(TickLog(
            tick_number=sim_tick,
            sim_day=sim_day,
            summary=summary,
            events_json=json.dumps({
                "archetype": archetype_label,
                "scenes": [
                    {
                        "type": s.scene_type,
                        "chars": [c.roster_id for c in s.characters],
                        "loc": s.location.name if s.location else "",
                    }
                    for s in scene_plans
                ],
                "departures": [d["roster_id"] for d in departures],
            }),
            cost_this_tick=self.cost.today_spend(),
        ))
        self.db.commit()

        result = {
            "sim_day": sim_day,
            "sim_tick": sim_tick,
            "date_display": date["display"],
            "day_archetype": archetype_label,
            "day_label": day_comp.day_label if day_comp else "",
            "scenes": len(scene_plans),
            "total_exchanges": total_exchanges,
            "alive_count": alive_count,
            "departures": len(departures),
            "cost_today": self.cost.today_spend(),
            "budget_remaining": self.cost.budget_remaining(),
            "ai_mode": settings.ai_mode,
        }

        if broadcast_fn:
            await broadcast_fn({"type": "tick_complete", "data": result})

        logger.info(summary)
        return result

    def status(self) -> dict:
        date = self.clock.current_date_dict()
        cost = self.cost.status_dict()
        alive = self.db.query(Character).filter(Character.alive == True).count()
        total = self.db.query(Character).count()
        recent = self.db.query(TickLog).order_by(TickLog.id.desc()).limit(10).all()
        return {
            "clock": date,
            "cost": cost,
            "population": {"alive": alive, "total": total},
            "ai_mode": settings.ai_mode,
            "recent_ticks": [
                {
                    "day": t.sim_day,
                    "summary": t.summary,
                    "cost": t.cost_this_tick,
                    "at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in recent
            ],
        }
