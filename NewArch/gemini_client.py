"""Unified LLM client with JSON response parsing."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

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
    if isinstance(exc, error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(exc, (error.URLError, TimeoutError, requests.RequestException))


def _is_rate_limit(exc: Exception) -> bool:
    if isinstance(exc, error.HTTPError):
        return exc.code == 429
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429
    return False


def _request_openrouter(prompt: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    response = requests.post(
        OPENROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {get_openrouter_key()}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost/scriptagent",
            "X-Title": "ScriptAgent",
        },
        json=payload,
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"OpenRouter response did not contain message content: {body}") from exc


def _request_gemini(prompt: str, model: str = "gemini-2.5-flash") -> str:
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
    body = response.json()
    try:
        return body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Gemini response did not contain message content: {body}") from exc


def call_llm_json(prompt: str, models: list[str] | None = None) -> dict[str, Any]:
    """Call an LLM and parse a single JSON object from the response."""
    providers: list[tuple[str, str, str]] = []
    
    # Prioritize Gemini first
    if GEMINI_KEY:
        providers.append(("gemini", GEMINI_MODEL, prompt))

    # Then try OpenRouter models
    for model in models or get_openrouter_models():
        providers.append(("openrouter", model, prompt))

    if not providers:
        raise RuntimeError("No LLM providers configured.")

    last_error: Exception | None = None
    current_prompt = prompt
    raw = ""

    for provider, model, base_prompt in providers:
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                LOGGER.info(
                    "LLM request | provider=%s | model=%s | attempt=%s | prompt_chars=%s",
                    provider,
                    model,
                    attempt,
                    len(current_prompt),
                )
                if provider == "openrouter":
                    raw = _request_openrouter(current_prompt, model)
                else:
                    raw = _request_gemini(current_prompt, model)
                elapsed = time.monotonic() - started
                LOGGER.info(
                    "LLM response | provider=%s | model=%s | %.2fs | response_chars=%s",
                    provider,
                    model,
                    elapsed,
                    len(raw),
                )
                return _parse_json_object(raw)
            except json.JSONDecodeError as exc:
                last_error = exc
                current_prompt = (
                    f"{base_prompt}\n\nThe previous response was not valid JSON:\n{raw}\n\n"
                    "Return exactly one valid JSON object only."
                )
            except Exception as exc:
                last_error = exc
                elapsed = time.monotonic() - started
                LOGGER.warning(
                    "LLM failure | provider=%s | model=%s | %.2fs | %s",
                    provider,
                    model,
                    elapsed,
                    exc,
                )
                if not _error_is_retryable(exc):
                    break
                
                # If it's a rate limit (429), back off longer
                if _is_rate_limit(exc):
                    sleep_time = min(attempt * 6, 24)
                    LOGGER.info("Rate limit hit. Backing off for %s seconds...", sleep_time)
                else:
                    sleep_time = min(attempt * 2, 8)
                    
                time.sleep(sleep_time)

    raise RuntimeError(f"All LLM providers failed: {last_error}")


def call_gemini(prompt: str) -> dict[str, Any]:
    return call_llm_json(prompt)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, dict):
            return parsed

    raise json.JSONDecodeError("LLM did not return valid JSON.", raw, 0)
