"""
LLM client with fallback.

Model choice (see docs/assumptions.md):
- Primary  : Gemini 2.5 Flash (free tier, fast, good tool/JSON following, 1M context)
- Fallback : Ollama local model (same as my SupermarketAI project) - works with no internet/quota
- Last line: every agent has a non-LLM fallback (extractive answer / rule routing),
             so the app degrades instead of crashing when both are down.
"""
import asyncio
import json
import re
from typing import AsyncIterator

from langchain_core.messages import BaseMessage

from config.settings import settings
from src.utils.errors import LLMError
from src.utils.logger import get_logger

log = get_logger("llm")

_models: dict[str, object] = {}


def _build(provider: str):
    if provider == "gemini":
        if not settings.GOOGLE_API_KEY:
            raise LLMError("GOOGLE_API_KEY not set")
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL, google_api_key=settings.GOOGLE_API_KEY,
                                      temperature=settings.LLM_TEMPERATURE, max_retries=1)
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_BASE_URL,
                          temperature=settings.LLM_TEMPERATURE)
    raise LLMError(f"unknown provider {provider}")


def get_model(provider: str):
    if provider not in _models:
        _models[provider] = _build(provider)
    return _models[provider]


def _providers() -> list[str]:
    order = [settings.LLM_PROVIDER, settings.LLM_FALLBACK_PROVIDER]
    return [p for i, p in enumerate(order) if p and p != "none" and p not in order[:i]]


async def ainvoke(messages: list[BaseMessage], purpose: str, run_name: str | None = None) -> str:
    """Call the LLM, try the fallback provider if the first one fails. Returns text."""
    errors = []
    for provider in _providers():
        try:
            model = get_model(provider)
            res = await asyncio.wait_for(
                model.ainvoke(messages, config={"run_name": run_name or purpose, "tags": [purpose, provider]}),
                timeout=settings.LLM_TIMEOUT_SECONDS)
            return _text(res.content)
        except Exception as e:
            errors.append(f"{provider}: {type(e).__name__}: {str(e)[:200]}")
            log.warning("llm call failed", extra={"provider": provider, "purpose": purpose, "error": str(e)[:300]})
    raise LLMError("all LLM providers failed", errors=errors)


async def astream(messages: list[BaseMessage], purpose: str) -> AsyncIterator[str]:
    """Stream tokens. Falls back to the next provider only if nothing was streamed yet."""
    errors = []
    for provider in _providers():
        started = False
        try:
            model = get_model(provider)
            async for chunk in model.astream(messages, config={"run_name": purpose, "tags": [purpose, provider]}):
                text = _text(chunk.content)
                if text:
                    started = True
                    yield text
            return
        except Exception as e:
            errors.append(f"{provider}: {type(e).__name__}")
            log.warning("llm stream failed", extra={"provider": provider, "error": str(e)[:300]})
            if started:
                raise LLMError("stream broke in the middle", errors=errors) from e
    raise LLMError("all LLM providers failed", errors=errors)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # gemini can return a list of parts
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def parse_json(text: str) -> dict | list:
    """LLMs like to wrap JSON in ```json fences or add a sentence. Take the first JSON object/array."""
    text = re.sub(r"```(?:json|python)?", "", text).strip()
    match = re.search(r"[\[{].*[\]}]", text, re.S)
    if not match:
        raise ValueError("no JSON found in LLM output")
    return json.loads(match.group(0))
