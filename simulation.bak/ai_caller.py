"""
ai_caller.py — unified AI caller.

Modes (set AI_MODE in .env):
  api    = Anthropic Haiku + DeepSeek (costs money)
  local  = Ollama on localhost (free, runs on Mac Mini)
  mixed  = Ollama for dialogue, Haiku for scoring

Python 3.14 fix: uses native ollama library instead of openai
for local mode, avoiding the httpx proxies compatibility issue.
"""
import asyncio
import logging
import anthropic
import openai

logger = logging.getLogger("caldwell.ai")

# Lazy-loaded clients
_anthropic_client = None
_deepseek_client = None

# Rate limiter: max concurrent Haiku calls to stay under 50 req/min
_api_semaphore = asyncio.Semaphore(6)


def _get_anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        from config import settings
        _anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key
        )
    return _anthropic_client


def _get_deepseek():
    global _deepseek_client
    if _deepseek_client is None:
        from config import settings
        _deepseek_client = openai.AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _deepseek_client


# ── Individual callers ────────────────────────────────────────────────────────

async def call_haiku(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 150,
) -> tuple[str, int, int]:
    async with _api_semaphore:
        try:
            client = _get_anthropic()
            response = await client.messages.create(
                model=__import__('config').settings.haiku_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
            text = response.content[0].text.strip() if response.content else "..."
            return text, response.usage.input_tokens, response.usage.output_tokens
        except Exception as e:
            logger.error(f"Haiku call failed: {e}")
            return "...", 0, 0


async def call_deepseek(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 150,
) -> tuple[str, int, int]:
    try:
        client = _get_deepseek()
        oai_messages = [{"role": "system", "content": system_prompt}] + messages
        response = await client.chat.completions.create(
            model=__import__('config').settings.deepseek_model,
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        text = ""
        if response.choices:
            text = (response.choices[0].message.content or "").strip()
        usage = response.usage
        return text, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0
    except Exception as e:
        logger.error(f"DeepSeek call failed: {e}")
        return "...", 0, 0


async def call_ollama(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 150,
    model: str | None = None,
) -> tuple[str, int, int]:
    """
    Call a local Ollama model using the native ollama library.
    Avoids the httpx proxies issue with the openai compatibility shim.
    """
    import ollama as _ollama
    from config import settings

    chosen_model = model or settings.ollama_model
    ollama_messages = [{"role": "system", "content": system_prompt}] + messages

    try:
        client = _ollama.AsyncClient(host=settings.ollama_base_url)
        response = await client.chat(
            model=chosen_model,
            messages=ollama_messages,
            options={"num_predict": max_tokens, "temperature": 0.85},
        )
        text = ""
        if hasattr(response, "message"):
            text = (response.message.content or "").strip()
        elif isinstance(response, dict):
            text = response.get("message", {}).get("content", "").strip()

        # Ollama doesn't always return token counts — estimate
        in_tok = len(system_prompt.split()) * 2
        out_tok = len(text.split()) * 2
        return text, in_tok, out_tok

    except Exception as e:
        logger.error(f"Ollama call failed ({chosen_model}): {e}")
        return "...", 0, 0


# ── Unified dispatcher ────────────────────────────────────────────────────────

async def call_ai(
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 150,
) -> tuple[str, int, int]:
    from config import settings
    mode = settings.ai_mode

    if mode in ("local", "mixed"):
        chosen = settings.ollama_model if model == "haiku" else settings.ollama_model_b
        return await call_ollama(system_prompt, messages, max_tokens, model=chosen)
    else:  # api mode — all characters use DeepSeek
        return await call_deepseek(system_prompt, messages, max_tokens)


async def call_scoring_model(
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 8,
) -> tuple[str, int, int]:
    """Cheapest available model for scoring — local if enabled, Haiku otherwise."""
    from config import settings
    if settings.ai_mode in ("local", "mixed"):
        return await call_ollama(system_prompt, messages, max_tokens,
                                 model=settings.ollama_model)
    else:
        return await call_deepseek(system_prompt, messages, max_tokens)
