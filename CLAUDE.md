# CLAUDE.md — Caldwell Simulation Project

---

## Mission

This project is not a chatbot playground. It is a living civilization simulator.

The goal is to create a world where:
- characters survive, adapt, and form culture
- the map expands as they discover or build new places
- discoveries become real simulation objects, not decorative logs
- the UI reflects world growth in real time
- characters evolve behaviorally and socially over time based on experience

The simulation must feel like a society unfolding, not a loop of disconnected conversations.

---

## What this project is

A civilization simulator written in Python. A small community of 17 characters lives in a named location (Caldwell). Every 20 minutes a "tick" runs: the engine composes a day, selects scenes, runs AI-generated conversations between characters, writes memories, tracks relationships, and accumulates social history.

The AI backbone is **DeepSeek** (via OpenAI-compatible API) for all character dialogue. **Anthropic Haiku** is used only for lightweight scoring calls. Do not swap these unless explicitly asked.

---

## Stack

- **Python 3.14** on macOS (M2 Mac Mini)
- **FastAPI + Uvicorn** — HTTP server (`main.py`)
- **SQLAlchemy 2.0** — ORM, SQLite database (`caldwell.db`)
- **APScheduler** — runs `engine.run_tick()` every 20 minutes
- **Anthropic SDK** — Haiku calls only (scoring)
- **OpenAI SDK** — used for DeepSeek calls (same interface, different base URL)
- **Ollama** — local model fallback, not currently active (`ai_mode = "api"` in `.env`)
- **Pydantic-settings** — config via `.env` file

---

## Project layout

```
caldwell/
├── main.py                    # FastAPI app, APScheduler, WebSocket broadcast
├── config.py                  # Settings (pydantic-settings, reads .env)
├── .env                       # API keys, budget, mode — never commit
├── caldwell.db                # SQLite database
├── database/
│   ├── db.py                  # get_db() session factory
│   └── models.py              # ALL SQLAlchemy models (source of truth)
├── simulation/
│   ├── engine.py              # Tick orchestrator — the main loop
│   ├── ai_caller.py           # Unified AI dispatcher (DeepSeek / Haiku / Ollama)
│   ├── cost_tracker.py        # Daily spend tracking, budget hard-stop
│   ├── daily_composer.py      # Day Composition Engine
│   ├── pressure_selector.py   # Identifies active social pressures each tick
│   ├── scene_selector.py      # ScenePlan dataclass, casting, location selection
│   ├── scene_builder.py       # Physical scene context templates (sensory framing)
│   ├── scene_categorizer.py   # Classifies scene content after generation
│   ├── conversation_runner.py # Runs two-character scenes (9 or 16 exchanges)
│   ├── group_conversation.py  # Runs 3+ character scenes (disabled in config)
│   ├── prompt_builder.py      # Builds per-character system prompts
│   ├── memory_writer.py       # Extracts and writes memories after scenes
│   ├── consequence_engine.py  # Rule-based consequences after every scene
│   ├── silent_actions.py      # Off-screen activity layer (no dialogue)
│   ├── transient_state.py     # Per-character daily emotional state
│   ├── social_roles.py        # Role emergence and inference (runs every 7 days)
│   ├── location_memory.py     # Location personality accumulation
│   ├── daybook.py             # Reader-facing daily summary generator
│   ├── open_question.py       # Persistent unresolved questions driving character behavior
│   ├── drives.py              # Core drive definitions and weights
│   ├── biology.py             # Age, reproduction, death, attraction
│   ├── disposition_tracker.py # Long-term character mood tracking
│   ├── satisfaction_scorer.py # Scores conversation outcomes
│   ├── norm_detector.py       # Detects emerging behavioral norms
│   ├── norm_executor.py       # Executes norm-based scheduled actions
│   ├── social_learning.py     # Records and distills behavioral approaches
│   ├── event_detector.py      # Scans for significant events, population milestones
│   ├── action_generator.py    # Generates action memories for silent activities
│   ├── action_processor.py    # Processes operator-injected action events
│   ├── resource_manager.py    # Food/resource pools and status scores
│   ├── environment.py         # Weather and environmental events
│   ├── world_expansion.py     # Location discovery and territory claims
│   ├── procreation.py         # Conception, birth, maturation
│   ├── clock.py               # Simulation calendar
│   ├── topic_seeds.py         # Conversation topic generation
│   ├── rhythms.py             # Recurring community rhythms
│   └── social_spread.py       # Witness memories and secondhand rumors
├── api/
│   ├── routes.py              # Player-facing endpoints
│   └── websocket_manager.py   # WebSocket broadcast manager
├── static/
│   ├── game.html              # Phaser map client
│   └── dashboard.html         # Operator/reader dashboard
└── simulation.bak/            # OLD versions — DO NOT USE OR REFERENCE
```

---

## Characters (17 alive)

| Roster ID | Name | Core Drive |
|-----------|------|------------|
| F-01 | Nara | Curiosity |
| F-02 | Kira | Belonging |
| F-03 | Sela | Meaning |
| F-08 | Mara | Grief |
| F-11 | Calla | Envy |
| F-12 | Wren | Connection |
| F-13 | Reva | Power |
| F-18 | Tama | Survival |
| F-19 | Orsa | Power |
| F-20 | Eshe | Status |
| M-01 | Bram | Status |
| M-03 | Rook | Meaning |
| M-04 | Fenn | Connection |
| M-05 | Cael | Dominance |
| M-07 | Dex | Envy |
| M-08 | Kofi | Order |
| M-10 | Bayo | Meaning |

All characters use `ai_model = "deepseek"` in the database.

---

## Seed locations (12)

Bayou Market, Caldwell Public Library, Central Square, Community Center, Lakeview Flats, Riverside Park, Rooftop Garden, The Chapel, The Meridian, The Schoolhouse, The Workshop, Warehouse Row

These are the seed map only. The world must be able to expand beyond them.

---

## Key config values (from .env / config.py)

```
ai_mode = "api"                    # DeepSeek + Haiku, not local Ollama
conversations_per_tick = 3         # Scenes per tick
exchanges_per_conversation = 9     # Normal scenes (16 for injected scenes)
MAX_TOKENS_PER_TURN = 450          # Per character turn
daily_budget_usd = 2.80            # Hard stop at this spend per real day
tick_interval_minutes = 20
```

---

## Critical architecture notes

### AI caller convention
- `call_ai(model, system_prompt, messages, max_tokens)` — use for all character dialogue
- `call_scoring_model(system_prompt, messages, max_tokens=8)` — use for lightweight scoring only
- `cost_tracker.record(model_name, input_tokens, output_tokens)` — must be called after every API call
- Model string in the database is `"deepseek"` or `"haiku"` — not the full model ID

### Engine tick order (do not change without updating engine.py header comment)
1. Advance clock, resources, biology, environment
2. Update transient emotional states
3. Execute norm actions
4. Process injected operator events
5. Daily Composition Engine → produces `scene_plans`
6. Run scenes concurrently (asyncio.gather)
7. Generate consequences after each scene
8. Silent actions for unused characters
9. Social role updates (every 7 days)
10. Event detection, social learning, world expansion, procreation
11. Reader summary (daybook, threads, arcs)
12. Tick log

### Scene pipeline
`pressure_selector` → `daily_composer` → `scene_selector` (ScenePlan) → `conversation_runner` or `group_conversation` → `consequence_engine` → `memory_writer`

### Pair cooldowns and caps (enforced in daily_composer.py)
- `open_question` scenes: max 1 per day
- `argument` scenes: max 1 per day (2 if nothing else fires)
- Same pair: 3-day cooldown
- Central Square: max 2 scenes per day
- `status_challenge`: max 1 per day

---

## What is already implemented — do not rebuild

- **Schema:** All 8 extended tables verified: `ConsequenceRecord`, `CivilizationThread`, `CharacterTransientState`, `DayComposition`, `ReaderSummary`, `SocialRole`, `LocationMemory`, `SilentAction`.
- **prompt_builder.py:** Transient emotional state injected into system prompts. `hope_active` and `obsession_text` on `CharacterTransientState`.
- **social_spread.py:** Witness memories and secondhand rumors after high-intensity scenes. Wired into engine.py after consequence generation.
- **open_question.py:** Three pruning mechanisms: semantic redundancy merging, age-based abandonment, forgotten-resolution memory write.
- **rhythms.py:** 7 recurring community rhythms. `daily_composer.py` calls `get_due_rhythms()` and fills slot 2 with a rhythm-driven scene when one is due.
- **api/routes.py:** `GET /api/summary/today`, `GET /api/summary/{sim_day}`, enhanced `GET /api/characters` (with `social_role` and `transient_state`), enhanced `GET /api/locations` (with `location_memory`). All null-safe.
- **scene_builder.py:** `build_embodied_scene_frame()` — 12 scene types × 12 locations produce a physical narrator paragraph injected as the first exchange in every conversation.
- **Location model (database/models.py):** 15 emergent-world columns added: `is_seed`, `is_emergent`, `discovery_origin`, `discovered_by_id`, `discovered_on_day`, `named_by_id`, `confidence`, `discovery_stage`, `location_category`, `territory_type`, `danger_level`, `claim_character_id`, `use_count`, `map_x`, `map_y`. All 12 seed locations seeded with `is_seed=True`, `discovery_stage="confirmed"`, `territory_type="inside"`, `confidence=1.0`, and coordinates matching `LOCATION_LAYOUT` in `game.html` (stored as 0.0–1.0 fractions). `data/locations_data.py` refactored with `_loc()` helper and `_SEED_COORDS` dict. Schema verified clean via `reset_and_seed.py --confirm`.
- **daily_composer.py**, **consequence_engine.py**, **silent_actions.py**, **transient_state.py**, **social_roles.py**, **location_memory.py**, **daybook.py**, **scene_categorizer.py**, **scene_selector.py**, **pressure_selector.py** — all implemented.

---

## Implementation priorities

Execute these one at a time in order. Do not begin the next priority until the current one is verified working. Each priority describes exactly what to do, which files to change, and what done looks like.

---

### PRIORITY 1 — Create a unified /api/world_map endpoint

**Files to change:** `api/routes.py`

Add `GET /api/world_map` returning a JSON array of all `Location` rows. Each item must include: `id`, `name`, `is_seed`, `is_emergent`, `territory_type`, `discovery_stage`, `location_category`, `map_x`, `map_y`, `danger_level`, `claim_character_id`, `use_count`, `discovered_by_id`, `discovered_on_day`, `confidence`. All emergent fields must be null-safe. Do not remove or break the existing `/api/locations` endpoint.

**Done when:** `GET /api/world_map` returns all 12 seed locations with coordinates and `is_seed=true`.

---

### PRIORITY 2 — Replace hardcoded LOCATION_LAYOUT in game.html with live /api/world_map data

**Files to change:** `static/game.html`

On scene load, fetch `/api/world_map` and build a `locationNodes` object keyed by location name (with id as a fallback). Each node must carry: `x`, `y`, `name`, `id`, `is_emergent`, `territory_type`. Replace all references to the hardcoded `LOCATION_LAYOUT` with lookups against `locationNodes`. Update `spawnCharacter()` and `moveCharacter()` to use `locationNodes`. If a location is not found in `locationNodes`, log a browser console warning and skip the move — do not silently fall back to Central Square. The visual rendering of seed locations must be identical to the current behavior after this change.

**Done when:** The map renders correctly from live data, character movement works for all 12 seed locations, and no `LOCATION_LAYOUT` references remain in `game.html`.

---

### PRIORITY 3 — Handle location_discovered websocket events in game.html

**Files to change:** `static/game.html`

Add a websocket message handler for event type `location_discovered`. The payload must match the `/api/world_map` entry shape. On receipt, add the new location to `locationNodes` and render it as a new Phaser map node. Visually distinguish emergent locations from seed locations (different color, marker shape, or label style is acceptable). Log the discovery to the browser console with the location name and coordinates.

**Done when:** A manually injected `location_discovered` websocket event causes a new node to appear on the map without a page reload.

---

### PRIORITY 4 — Add collision-aware coordinate assignment for new locations

**Files to change:** `simulation/world_expansion.py`

Write a function `assign_map_coordinates(db, territory_type) -> (float, float)` that:
- Queries all existing locations with assigned `map_x` / `map_y`
- Places inside locations within current seed map bounds, frontier locations in a ring outside those bounds, outside locations further out
- Attempts up to 50 random placements within the appropriate zone
- Rejects any placement closer than 80 units to any existing location
- Falls back to an expanding spiral search if 50 attempts all collide
- Returns the chosen `(x, y)`

Call this function whenever a new `Location` row is created and assign `map_x` / `map_y` before committing.

**Done when:** Creating 20 simulated emergent locations programmatically results in no two locations closer than 80 units, with inside/frontier/outside placement respected.

---

### PRIORITY 5 — Broadcast location_discovered from world_expansion.py

**Files to change:** `simulation/world_expansion.py`, `api/websocket_manager.py`

After committing a new confirmed `Location` row (with coordinates assigned), broadcast a websocket event with type `location_discovered` and a payload containing all `/api/world_map` fields for that location. Confirm `websocket_manager.py` has a broadcast method usable from async simulation code — add one if it does not.

**Done when:** Running a tick that produces a confirmed discovery causes a `location_discovered` websocket message to appear in the browser console.

---

### PRIORITY 6 — Expand discovery detection to multiple signal sources

**Files to change:** `simulation/world_expansion.py`

Audit the current detection logic and document which signal sources it currently scans. Then add scanning of: action memories, scene dialogue content, silent action descriptions, and monologue content if stored.

Add a keyword/phrase list covering at minimum: "forest", "trail", "clearing", "ruin", "shelter", "creek", "ridge", "hollow", "grove", "edge of", "past the", "beyond the", "found a", "discovered", "stumbled upon", "hidden", "old building", "abandoned".

For each candidate signal, produce a `(location_hint, confidence, source_type)` tuple rather than immediately creating a location. Apply these confidence rules:
- Single mention = 0.3 (hint)
- Two mentions from different sources = 0.6 (tentative)
- Three or more mentions = 0.9 (confirmed)

Do not create a `Location` row until confidence >= 0.6. At confidence >= 0.9, set `discovery_stage = "confirmed"`.

Store hints and tentative discoveries in a new `DiscoveryCandidate` table with fields: `id`, `name_hint`, `confidence`, `source_ids_json`, `territory_type`, `created_at`, `sim_day`, `promoted_to_location_id`. Add this table to `database/models.py` and `reset_and_seed.py`.

Phrases like "beyond my reach" or "lost in thought" must not trigger discovery — distinguish literal exploration language from metaphorical.

**Done when:** A manually inserted action memory containing "found a clearing past the warehouse" produces a `DiscoveryCandidate` row with confidence 0.3. A second inserted memory referencing the same clearing raises confidence to 0.6 and creates a tentative `Location` row.

---

### PRIORITY 7 — Tie discovery to discoverer character state

**Files to change:** `simulation/world_expansion.py`, `simulation/memory_writer.py`, `simulation/social_roles.py`, `database/models.py`

When a `Location` is promoted to `discovery_stage = "confirmed"`, write a first-person memory for the discovering character in the format: "[Character name] found [place name] — [brief description of discovery context]".

Add a `discovery_count` integer field (default 0) to the `Character` model if not already present. Increment it when a discovery is confirmed for that character.

In `social_roles.py`, add "pathfinder" and "explorer" as valid role types if not already present. Add a role-inference rule: if `discovery_count >= 2`, the character is eligible for the "pathfinder" role.

Log each discovery event with character name, location name, and sim_day.

**Done when:** A character with 2 or more confirmed discoveries is inferred to have the "pathfinder" role on the next role-inference pass.

---

### PRIORITY 8 — Increment use_count on Location when used in a scene

**Files to change:** `simulation/conversation_runner.py` or `simulation/engine.py`

After each scene completes, look up the scene's location by name in the `Location` table and increment `use_count` by 1. Commit the change. Log a warning (not an error) if the location name is not found — do not crash the tick.

**Done when:** Running 3 ticks results in `use_count > 0` on locations that hosted scenes.

---

### PRIORITY 9 — Expose discovery history and evolution indicators on the dashboard

**Files to change:** `static/dashboard.html`, `api/routes.py`

Add `GET /api/discoveries` returning all `Location` rows where `is_emergent = True`, ordered by `discovered_on_day` descending. Include: `name`, `discovered_by_id`, `discovered_on_day`, `discovery_stage`, `territory_type`, `location_category`, `use_count`, `confidence`.

Add a "World Expansion" section to `dashboard.html` that:
- Lists discovered locations by sim_day with discoverer name
- Shows current discovery stage and territory type
- Shows use_count as a number or simple bar
- Highlights locations with `territory_type` of frontier or outside

Add a "Pathfinders" section listing characters with `discovery_count >= 1` and their total discovery count.

Both sections update on page refresh — no websocket requirement for this priority.

**Done when:** After seeding one or two manually created emergent locations, the dashboard "World Expansion" section displays them correctly with discoverer name and discovery stage.

---

## What NOT to do

- Do not modify `simulation.bak/` — it is the old version, kept for reference only
- Do not add API calls to `consequence_engine.py`, `daily_composer.py`, `silent_actions.py`, `transient_state.py`, `daybook.py`, `social_roles.py`, or `location_memory.py` — these are intentionally rule-based
- Do not change `ai_mode` or model routing in `ai_caller.py` unless explicitly asked
- Do not touch `reset_and_seed.py` except to add `db.create_all()` calls for new tables
- Do not enable group conversations — `group_conversations_per_tick` stays 0
- Do not write long scaffold comments — short docstrings and inline comments only
- Do not rename roster IDs — they are primary keys referenced across tables
- Central Square is intentionally throttled to 2 scenes/day — do not remove that cap
- Do not treat emergent locations as second-class objects
- Do not hardcode behavior around the original 12 seed locations
- Do not silently fall back to Central Square for unknown locations — surface the bug
- Do not solve world expansion only in the frontend

---

## Coding conventions

- SQLAlchemy 2.0 declarative style — see existing models in `database/models.py` for pattern
- All new simulation modules go in `simulation/` with `logger = logging.getLogger("caldwell.<module>")`
- Async functions use `async def` and `await` — the engine tick is async throughout
- Database sessions are passed as `db: Session` parameters — never create new sessions inside modules
- JSON columns stored as `Text` with `_json` suffix in field names
- Foreign keys always reference `"table_name.id"` string form
- `created_at = Column(DateTime, default=datetime.utcnow)` on every new table
- Error handling: wrap risky calls in try/except with `logger.warning()` — never let a subsystem crash the tick
- Prefer modifying existing modules over introducing parallel versions
- When in doubt, make state explicit rather than inferred from fragile frontend assumptions

---

## Running the simulation

```bash
# Install dependencies
pip install -r requirements.txt

# Reset and reseed the database
python reset_and_seed.py

# Start the server
uvicorn main:app --reload

# Tick runs automatically every 20 minutes, or trigger via POST /tick
```

---

## Database current state

- 17 characters seeded, 2 ticks run (verified clean)
- 12 seed locations seeded
- All tables exist and are current: `consequence_records`, `civilization_threads`, `character_transient_states`, `day_compositions`, `reader_summaries`, `social_roles`, `location_memories`, `silent_actions`
- `caldwell.db` can be wiped and reseeded at any time via `reset_and_seed.py`
