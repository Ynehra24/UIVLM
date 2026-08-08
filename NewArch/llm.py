"""Call LLM and return raw text (not JSON-parsed)."""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import (
    GEMINI_KEY,
    GEMINI_MODEL,
    LOGGER,
    OPENROUTER_CHAT_URL,
    OPENROUTER_TIMEOUT_SECONDS,
    get_openrouter_key,
    get_openrouter_models,
)


def _error_is_retryable(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(exc, (TimeoutError, requests.RequestException))


def _is_rate_limit(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429
    return False


def _request_openrouter(prompt: str, model: str) -> str:
    response = requests.post(
        OPENROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {get_openrouter_key()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/scriptagent",
            "X-Title": "ScriptAgent",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4000,
        },
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _request_gemini(prompt: str, model: str) -> str:
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_KEY is not set.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4000},
        },
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"]


def call_llm(prompt: str) -> str:
    """Call an LLM and return raw text response. Tries Gemini first, then OpenRouter."""
    providers: list[tuple[str, str]] = []

    # Gemini: only enable if key looks like a real API key (starts with AIza), not an OAuth token
    if GEMINI_KEY and GEMINI_KEY.startswith("AIza"):
        providers.append(("gemini", GEMINI_MODEL))
    elif GEMINI_KEY:
        LOGGER.warning("GEMINI_KEY appears to be an OAuth token (starts with '%s...'), not an API key. "
                       "Get a real API key from https://aistudio.google.com/apikey", GEMINI_KEY[:4])

    for model in get_openrouter_models():
        providers.append(("openrouter", model))

    if not providers:
        raise RuntimeError("No LLM providers configured.")

    last_error: Exception | None = None

    for provider, model in providers:
        max_attempts = 3 if provider == "gemini" else 1  # Retry Gemini on 429s
        for attempt in range(1, max_attempts + 1):
            started = time.monotonic()
            try:
                LOGGER.info(
                    "LLM request | provider=%s | model=%s | attempt=%s | prompt_len=%s",
                    provider, model, attempt, len(prompt),
                )
                if provider == "gemini":
                    raw = _request_gemini(prompt, model)
                else:
                    raw = _request_openrouter(prompt, model)

                elapsed = time.monotonic() - started
                LOGGER.info(
                    "LLM response | provider=%s | model=%s | %.2fs | response_len=%s",
                    provider, model, elapsed, len(raw),
                )
                return raw

            except Exception as exc:
                last_error = exc
                elapsed = time.monotonic() - started
                LOGGER.warning("LLM failure | provider=%s | model=%s | %.2fs | %s", provider, model, elapsed, exc)

                if _is_rate_limit(exc):
                    if provider == "gemini":
                        LOGGER.warning("Gemini 429 rate limit hit — failing over immediately to OpenRouter models.")
                        break  # Failover immediately to next provider instead of burning Gemini quota
                    time.sleep(min(attempt * 6, 24))
                else:
                    time.sleep(min(attempt * 2, 8))

    raise RuntimeError(f"All LLM providers failed: {last_error}")
