from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str = ""
    deepseek_api_key: str = ""

    database_url: str = "sqlite:///./caldwell.db"
    daily_budget_usd: float = 2.80

    # ── Simulation density ─────────────────────────────────────────────────
    tick_interval_minutes: int = 20       # real-time minutes between ticks
    conversations_per_tick: int = 3       # scenes per tick (was 6 pairs — now 3 meaningful scenes)
    group_conversations_per_tick: int = 0 # disabled — scenes replace groups
    exchanges_per_conversation: int = 5   # back-and-forth for normal conversations
    monologue_enabled: bool = False       # disabled — scenes are the output now

    # ── AI backend mode ────────────────────────────────────────────────────
    # "api"   = Anthropic + DeepSeek (costs money, higher quality)
    # "local" = Ollama running on localhost (free, runs on Mac Mini)
    # "mixed" = local for most, api for key characters (best of both)
    ai_mode: str = "api"

    # ── Local model settings (Ollama) ──────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"     # change to gemma2:9b, mistral:7b etc.
    ollama_model_b: str = "mistral:7b"    # second local model for variety

    # ── API model settings ─────────────────────────────────────────────────
    haiku_model: str = "claude-haiku-4-5-20251001"
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # ── Cost tracking (API mode only) ──────────────────────────────────────
    haiku_input_cost: float = 0.80
    haiku_output_cost: float = 4.00
    deepseek_input_cost: float = 0.14
    deepseek_output_cost: float = 0.28


settings = Settings()
