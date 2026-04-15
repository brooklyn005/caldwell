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

## What is already implemented — do not rebuild

- `database/models.py` — all 8 schema tables added and verified: `ConsequenceRecord`, `CivilizationThread`, `CharacterTransientState`, `DayComposition`, `ReaderSummary`, `SocialRole`, `LocationMemory`, `SilentAction`. Schema rebuilt via `reset_and_seed.py`. Two ticks ran clean.
- `prompt_builder.py` — transient emotional state injected into character system prompts via `get_transient_state_for_prompt()`. Block appears after memories, before current-moment section. Also added `hope_active` and `obsession_text` to `CharacterTransientState` (were referenced in the function but missing from the model).
- `social_spread.py` — witness memories and secondhand rumors after high-intensity scenes (`argument`, `status_challenge`, `quiet_intimacy`). Distortion weighted by trust relationship. Rumors seeded for next sim_day at 70% witness weight. Wired into engine.py after consequence generation. Verified with day 4 argument producing 2 witness memories + 2 next-day rumors.
- `open_question.py` — three pruning mechanisms added via `prune_open_questions()` (called each tick from engine.py): (1) semantic redundancy merging via Jaccard overlap on content keywords; (2) age-based abandonment for low-intensity questions unsurfaced 10+ days; (3) forgotten resolution path writes a first-person observation memory when any question fades. Verified: merge fired at tick 5.
- `rhythms.py` — recurring community rhythms module. 7 rhythms defined (hunt day every 4 days, washing day every 3, storytelling night every 5, food sorting every 2, teaching circle every 7, rooftop gathering every 6, workshop day every 5). Each rhythm has cadence, offset, scene type, location affinity, preferred drives, and norm reinforced. `daily_composer.py` calls `get_due_rhythms()` before the slot loop and preferentially fills slot 2 with a rhythm-driven scene via `build_rhythm_scene_plan()` when one is due. Daybook notes rhythm days.
- `api/routes.py` — player-facing endpoints added: `GET /api/summary/today` and `GET /api/summary/{sim_day}` return `ReaderSummary` rows as JSON (daybook, threads, roles, consequences, place updates, arcs). `GET /api/characters` enhanced with `social_role` (from `SocialRole`) and `transient_state` (most recent `CharacterTransientState`) per character. `GET /api/locations` enhanced with `location_memory` (from `LocationMemory`) per location. All new fields are null-safe.
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

### Priority 1 — Embodied scene directive (deeper prompt wiring)
`scene_builder.py` generates good physical context. `prompt_builder.py` needs to use it more aggressively. The scene frame should establish: who is standing where, what their hands are doing, what the light and smell is. Characters should not speak until after a physical action beat. This means the first message in a scene should come from the engine (not the character) as a brief scene-setting paragraph, then char_a speaks into that environment.

---

## What NOT to do

- Do not modify `simulation.bak/` — it is the old version, kept for reference only
- Do not add API calls to `consequence_engine.py`, `daily_composer.py`, `silent_actions.py`, `transient_state.py`, `daybook.py`, `social_roles.py`, or `location_memory.py` — these are intentionally rule-based and must stay free of API cost
- Do not change the `ai_mode` or model routing in `ai_caller.py` unless explicitly asked
- Do not touch `reset_and_seed.py` logic except to add `db.create_all()` calls for new tables
- Do not enable group conversations (`group_conversations_per_tick` stays 0) — keep disabled until explicitly prioritized
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

- 17 characters seeded, 2 ticks run (verified clean)
- 12 locations seeded
- All tables exist and are current — full schema including `consequence_records`, `civilization_threads`, `character_transient_states`, `day_compositions`, `reader_summaries`, `social_roles`, `location_memories`, `silent_actions`
- `caldwell.db` can be wiped and reseeded at any time via `reset_and_seed.py`
