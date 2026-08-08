"""Read, write, and verify Word property paths."""
from __future__ import annotations

import json
import time
from typing import Any

from .config import LOGGER, SLOW_OPERATION_SECONDS
from .property_path import PropertyPath
from .word_schema import WordSchema


def compact_property_state(values: dict[str, Any], max_items: int = 30) -> dict[str, Any]:
    items = list(values.items())[:max_items]
    return {key: value for key, value in items}


def read_properties(schema: WordSchema, paths: list[str]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for path in paths:
        started = time.monotonic()
        try:
            property_path = PropertyPath.parse(schema, path)
            values = property_path.read()
            results[path] = values[0] if len(values) == 1 else values
            elapsed = time.monotonic() - started
            if elapsed >= SLOW_OPERATION_SECONDS:
                LOGGER.info("Property read | %s | %.2fs", path, elapsed)
        except Exception as exc:
            LOGGER.warning("Property read failed | %s", path)
            results[path] = {"error": str(exc)}
    return results


def execute_property_changes(schema: WordSchema, changes: list[dict[str, Any]]) -> dict[str, Any]:
    results = []
    for change in changes:
        path = change.get("path")
        value = change.get("value")
        if not path:
            results.append({"success": False, "error": "Missing property path."})
            continue
        try:
            property_path = PropertyPath.parse(schema, path)
            script = property_path.compile(value)
            from .applescript import run_applescript_safe

            stdout, stderr, code = run_applescript_safe(script, timeout=30)
            if code != 0:
                results.append({"success": False, "error": stderr or "Property write failed.", "path": path})
                continue
            post_values = property_path.read()
            post_value = post_values[0] if post_values else None
            if not values_match(value, post_value):
                results.append(
                    {
                        "success": False,
                        "error": "Post-change value did not match.",
                        "path": path,
                        "expected": value,
                        "actual": post_value,
                    }
                )
                continue
            results.append({"success": True, "path": path, "value": post_value, "stdout": stdout})
        except Exception as exc:
            results.append({"success": False, "error": str(exc), "path": path})
    success = all(item.get("success") for item in results) if results else False
    return {"success": success, "results": results}


def values_match(expected: Any, actual: Any) -> bool:
    if actual is None or str(actual).startswith("__ERROR__"):
        return False
    if isinstance(expected, bool):
        return str(actual).lower() == ("true" if expected else "false")
    if isinstance(expected, (int, float)):
        try:
            return abs(float(actual) - float(expected)) < 0.01
        except (TypeError, ValueError):
            return False
    if isinstance(expected, list):
        actual_parts = [part.strip() for part in str(actual).split(",")]
        expected_parts = [str(item) for item in expected]
        return actual_parts == expected_parts
    return str(expected).strip() == str(actual).strip()
