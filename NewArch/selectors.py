"""LLM-driven capability and field selection with schema validation."""
from __future__ import annotations

import json
from typing import Any

from .gemini_client import call_llm_json
from .prompts import CAPABILITY_SELECTOR_PROMPT, FIELD_SELECTOR_PROMPT
from .word_schema import WordSchema


def _path_is_supported(schema: WordSchema, path: str, allowed_paths: set[str]) -> bool:
    return path in allowed_paths


def select_capabilities(schema: WordSchema, task: str) -> dict[str, Any]:
    catalog_lines = []
    for class_item in schema.classes[:120]:
        props = ", ".join(p["name"] for p in class_item.get("properties", [])[:6])
        catalog_lines.append(f"- {class_item['name']}: {class_item.get('description', '')} | {props}")

    prompt = f"""
{CAPABILITY_SELECTOR_PROMPT}

USER TASK:
{task}

WORD CLASS CATALOG:
{chr(10).join(catalog_lines[:80])}
"""
    response = call_llm_json(prompt)
    valid_names = {name.lower(): name for name in schema.class_index}
    selected = []
    for class_name in response.get("classes", []):
        canonical = valid_names.get(str(class_name).lower())
        if canonical:
            selected.append(canonical)

    if not selected:
        # Sensible defaults when the model returns nothing usable.
        defaults = ["document", "paragraph", "font", "table", "section"]
        selected = [name for name in defaults if name.lower() in valid_names][:5]
        if not selected:
            selected = [next(iter(valid_names.values()))]

    return {
        "classes": selected[:10],
        "inspect_properties": bool(response.get("inspect_properties", True)),
        "reasoning": response.get("reasoning", ""),
    }


def select_fields(schema: WordSchema, task: str, class_names: list[str]) -> dict[str, Any]:
    field_schema = schema.reachable_field_schema(class_names)
    allowed_paths = {item["path"] for item in field_schema}
    if not field_schema:
        return {"paths": [], "field_schema": []}

    path_lines = []
    for item in field_schema[:80]:
        path_lines.append(f"- {item['path']} ({item['type']}, access={item.get('access', '')})")

    prompt = f"""
{FIELD_SELECTOR_PROMPT}

USER TASK:
{task}

AVAILABLE CANONICAL PATHS:
{chr(10).join(path_lines)}
"""
    response = call_llm_json(prompt)
    selected_paths = []
    for path in response.get("paths", []):
        path_text = str(path).strip()
        if _path_is_supported(schema, path_text, allowed_paths):
            selected_paths.append(path_text)

    return {
        "paths": selected_paths[:30],
        "field_schema": [item for item in field_schema if item["path"] in selected_paths],
    }
