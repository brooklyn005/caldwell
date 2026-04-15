# CLAUDE.md — Caldwell Simulation Project

## What this project is

A civilization simulator written in Python. A small community of 17 characters lives in a named location (Caldwell). Every 20 minutes a "tick" runs: the engine composes a day, selects scenes, runs AI-generated conversations between characters, writes memories, tracks relationships, and accumulates social history. The goal is for the world to feel like a living society, not a sequence of chatbot exchanges.

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
│   ├── daily_composer.py      # Day Composition Engine — replaces old pressure→scene chain
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
│   └── ...
├── api/                       # FastAPI route modules (mostly stubs — needs work)
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

## Locations (12)

Bayou Market, Caldwell Public Library, Central Square, Community Center, Lakeview Flats, Riverside Park, Rooftop Garden, The Chapel, The Meridian, The Schoolhouse, The Workshop, Warehouse Row

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
`pressure_selector` → `daily_composer` (replaces direct pressure→scene) → `scene_selector` (ScenePlan) → `conversation_runner` or `group_conversation` → `consequence_engine` → `memory_writer`

### Pair cooldowns and caps (enforced in daily_composer.py)
- `open_question` scenes: max 1 per day
- `argument` scenes: max 1 per day (2 if nothing else fires)
- Same pair: 3-day cooldown
- Central Square: max 2 scenes per day
- `status_challenge`: max 1 per day

---

## BLOCKING ISSUE — must fix before anything else will run

**The simulation will crash on tick 1.** Seven new model classes are imported by the simulation code but do not exist in `database/models.py` and therefore the tables do not exist in `caldwell.db`.

### Missing classes (add to `database/models.py`)

| Class name | Table name | Referenced by |
|------------|------------|---------------|
| `ConsequenceRecord` | `consequence_records` | consequence_engine.py, transient_state.py, daybook.py |
| `CivilizationThread` | `civilization_threads` | consequence_engine.py, daybook.py |
| `CharacterTransientState` | `character_transient_states` | transient_state.py, daybook.py |
| `DayComposition` | `day_compositions` | daily_composer.py, daybook.py |
| `ReaderSummary` | `reader_summaries` | daybook.py |
| `SocialRole` | `social_roles` | social_roles.py, daybook.py, engine.py |
| `LocationMemory` | `location_memories` | location_memory.py, daybook.py |

After adding models, run `reset_and_seed.py` to recreate the database with the new schema (it drops and rebuilds). The DB has 0 ticks of history so no data will be lost.

### How each new table is used (infer fields from the modules that import them)

- **ConsequenceRecord**: stores one record per scene consequence. Fields: `sim_day`, `scene_id`, `consequence_type` (physical/social/personal), `affected_entity_ids_json`, `description`, `severity` (0-1), `persistence` (days), `reader_visible` (bool), `created_at`
- **CivilizationThread**: tracks active narrative threads. Fields: `theme`, `participant_ids_json`, `origin_day`, `status` (active/resolved/faded), `heat` (0-1 intensity), `last_advanced_day`, `resolved_day`, `description`
- **CharacterTransientState**: one row per character per day. Fields: `character_id` (FK), `sim_day`, `emotional_tags_json` (list of tag strings), `guardedness` (0-1), `fatigue` (0-1), `hunger` (0-1), `stress_load` (0-1), `notes`
- **DayComposition**: one row per tick. Fields: `sim_day`, `day_archetype` (string), `day_label` (string), `slots_json` (list of slot assignments), `actual_scenes_json`, `daybook_text` (paragraph), `created_at`
- **ReaderSummary**: one row per day. Fields: `sim_day`, `daybook` (text paragraph), `active_threads_json`, `shifting_roles_json`, `consequences_json`, `place_updates_json`, `character_arcs_json`, `created_at`
- **SocialRole**: one row per character-role pair. Fields: `character_id` (FK), `role_type` (string), `confidence` (0-1), `start_day`, `last_reinforced_day`, `public_visibility` (bool), `source_evidence_json`
- **LocationMemory**: one row per location. Fields: `location_id` (FK, unique), `mood` (string), `typical_activities_json`, `control_character_id` (nullable FK), `avoided_by_ids_json`, `emotional_residue_json`, `charge` (0-1), `last_event` (text), `last_updated_day`

---

## What is already implemented — do not rebuild

- `daily_composer.py` — Day Composition Engine with slot categories, day archetypes, pair cooldowns, caps
- `consequence_engine.py` — rule-based consequence generation, no API calls
- `silent_actions.py` — off-screen activity layer
- `transient_state.py` — per-character daily emotional state derivation
- `social_roles.py` — role inference from behavioral evidence
- `location_memory.py` — location personality accumulation
- `daybook.py` — reader summary assembly
- `scene_builder.py` — physical scene context templates (rich sensory framing)
- `scene_categorizer.py`, `scene_selector.py`, `pressure_selector.py`

---

## Implementation priorities (in order)

### Priority 1 — Fix the schema (BLOCKING)
Add the 7 missing model classes to `database/models.py`. Follow the existing SQLAlchemy 2.0 declarative style exactly. Then run `reset_and_seed.py`.

### Priority 2 — Wire transient state into prompt_builder.py
`transient_state.py` generates emotional state data each tick, but `prompt_builder.py` does not yet inject it into character system prompts. The transient state for a character should appear as a concrete paragraph near the top of the system prompt, after character description and before memories. Pull from the `CharacterTransientState` table for the current `sim_day`.

### Priority 3 — Open question pruning and decay
`open_question.py` has per-tick decay but no pruning for:
- Semantically redundant questions (merge similar ones)
- Age-based abandonment (drop after N days unsurfaced, convert to rumor memory)
- "Forgotten" resolution path (question fades without explicit answer — just write a memory that the character stopped wondering)

### Priority 4 — Recurring rhythms module
No file exists for this. Create `simulation/rhythms.py`. The module should maintain a small table of scheduled recurring community activities (hunt day, washing day, storytelling night, food-sorting day, etc.) that cycle on predictable intervals. Each rhythm: a name, a cadence (every N days), a location affinity, which characters typically participate, and a note on what norm it reinforces. The daily_composer should check the rhythms table when composing slot 2 (connection/care/labor/ambient) and prefer a rhythm-driven scene if one is due.

### Priority 5 — Social spread and witness mechanics
After a high-intensity scene (argument, status_challenge, quiet_intimacy), witnesses and bystanders should get memory fragments of distorted versions. Create a `propagate_scene_aftermath(scene, participants, location, sim_day, db)` function in a new `simulation/social_spread.py`. It should:
- Identify characters present at that location who were NOT scene participants
- Write a short distorted memory to each witness (1-2 sentences, first-person observation)
- Optionally escalate to a "rumor" memory for characters who hear secondhand the next day
- Weight distortion by relationship tension between witness and participants

### Priority 6 — Player-facing API endpoints
`daybook.py` generates `ReaderSummary` records but nothing serves them to a frontend. Add routes to `api/` (FastAPI):
- `GET /summary/today` — return today's ReaderSummary as JSON
- `GET /summary/{sim_day}` — return a specific day's summary
- `GET /characters` — return all living characters with current role and transient state
- `GET /locations` — return all locations with their LocationMemory

### Priority 7 — Embodied scene directive (deeper prompt wiring)
`scene_builder.py` generates good physical context. `prompt_builder.py` needs to use it more aggressively. The scene frame should establish: who is standing where, what their hands are doing, what the light and smell is. Characters should not speak until after a physical action beat. This means the first message in a scene should come from the engine (not the character) as a brief scene-setting paragraph, then char_a speaks into that environment.

---

## What NOT to do

- Do not modify `simulation.bak/` — it is the old version, kept for reference only
- Do not add API calls to `consequence_engine.py`, `daily_composer.py`, `silent_actions.py`, `transient_state.py`, `daybook.py`, `social_roles.py`, or `location_memory.py` — these are intentionally rule-based and must stay free of API cost
- Do not change the `ai_mode` or model routing in `ai_caller.py` unless explicitly asked
- Do not touch `reset_and_seed.py` logic except to add `db.create_all()` calls for new tables
- Do not enable group conversations (`group_conversations_per_tick` stays 0) until the schema issue is resolved and tested
- Do not write long scaffold comments — this codebase uses short docstrings and inline comments only
- Do not rename roster IDs — they are primary keys referenced across tables
- Central Square is intentionally throttled to 2 scenes/day — do not remove that cap

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

---

## Running the simulation

```bash
# Install dependencies (from caldwell/ directory)
pip install -r requirements.txt

# Reset and reseed the database (drops and rebuilds schema + initial data)
python reset_and_seed.py

# Start the server
uvicorn main:app --reload

# The tick runs automatically every 20 minutes via APScheduler
# Or trigger manually via POST /tick
```

---

## Database current state

- 17 characters seeded, 0 ticks run
- 12 locations seeded
- All legacy tables exist (`characters`, `locations`, `memories`, `dialogues`, `relationships`, etc.)
- The 7 new tables listed in the BLOCKING ISSUE section do not exist yet
- `caldwell.db` can be wiped and reseeded at any time — no history to preserve
