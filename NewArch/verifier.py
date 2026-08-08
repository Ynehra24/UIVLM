"""Post-execution verification against document state."""
from __future__ import annotations

from typing import Any

from . import content_actions
from .property_actions import read_properties, values_match
from .word_schema import WordSchema


def verify_step(
    schema: WordSchema,
    step: dict[str, Any],
    step_result: dict[str, Any],
    content_before: dict[str, Any],
    property_before: dict[str, Any],
) -> dict[str, Any]:
    if not step_result.get("success"):
        return {
            "verified": False,
            "reason": step_result.get("error") or "Step execution failed.",
        }

    mode = step.get("verification", "command_invocation")
    command = step.get("command")
    arguments = step.get("arguments", {})

    if mode == "command_invocation":
        return _verify_command_invocation(command, arguments, content_before)

    if mode == "property_write":
        path = arguments.get("path") or step.get("target")
        expected = arguments.get("value")
        if not path:
            return {"verified": False, "reason": "Missing property path for verification."}
        actual_state = read_properties(schema, [path])
        actual = actual_state.get(path)
        if not values_match(expected, actual):
            return {
                "verified": False,
                "reason": "Post-change property value did not match.",
                "expected": expected,
                "actual": actual,
            }
        return {"verified": True}

    if mode == "content_delta":
        return _verify_content_delta(arguments, content_before)

    if mode == "completion":
        return {"verified": True, "note": "Completion flagged by planner."}

    return _verify_command_invocation(command, arguments, content_before)


def _verify_command_invocation(
    command: str | None,
    arguments: dict[str, Any],
    content_before: dict[str, Any],
) -> dict[str, Any]:
    content_after = content_actions.get_content_state()
    if "error" in content_after:
        return {"verified": False, "reason": content_after["error"]}

    if command == "insert_text":
        text = str(arguments.get("text", ""))
        if text and not _document_contains(content_after, text):
            return {"verified": False, "reason": f"Expected text not found after insert: {text!r}"}
        return {"verified": True}

    if command == "replace_paragraph":
        text = str(arguments.get("text", ""))
        paragraph = int(arguments.get("paragraph", 1))
        actual = _paragraph_text(content_after, paragraph)
        if actual.strip() != text.strip():
            return {
                "verified": False,
                "reason": "Paragraph text does not match expected replacement.",
                "expected": text,
                "actual": actual,
            }
        return {"verified": True}

    if command == "delete_paragraph":
        paragraph = int(arguments.get("paragraph", 1))
        actual = _paragraph_text(content_after, paragraph)
        if actual.strip():
            return {"verified": False, "reason": "Paragraph still contains text after deletion."}
        return {"verified": True}

    if command == "format_paragraph":
        paragraph = arguments.get("paragraph")
        if paragraph is None:
            return {"verified": True}
        para = _paragraph_entry(content_after, int(paragraph))
        if not para:
            return {"verified": False, "reason": "Paragraph not found for formatting verification."}
        for key in ("bold", "italic", "font_name", "font_size"):
            if key in arguments:
                expected = arguments[key]
                actual = para.get(key if key != "font_name" else "font_name")
                if key == "bold" or key == "italic":
                    if bool(actual) != bool(expected):
                        return {"verified": False, "reason": f"{key} does not match.", "expected": expected, "actual": actual}
                elif str(actual) != str(expected):
                    return {"verified": False, "reason": f"{key} does not match.", "expected": expected, "actual": actual}
        return {"verified": True}

    if command == "insert_table":
        before_count = int(content_before.get("table_count", 0))
        after_count = int(content_after.get("table_count", 0))
        if after_count <= before_count:
            return {"verified": False, "reason": "Table count did not increase after insert_table."}
        return {"verified": True}

    if command == "find_replace":
        find_text = str(arguments.get("find", ""))
        replace_text = str(arguments.get("replace", ""))
        if find_text and _document_contains(content_after, find_text):
            return {"verified": False, "reason": f"Find text still present after replace: {find_text!r}"}
        if replace_text and not _document_contains(content_after, replace_text):
            return {"verified": False, "reason": f"Replace text not found: {replace_text!r}"}
        return {"verified": True}

    if command == "save_document":
        if not content_after.get("saved", False):
            return {"verified": False, "reason": "Document is still unsaved after save_document."}
        return {"verified": True}

    # SDEF or unknown commands: execution success is the best signal we have.
    return {"verified": True, "note": "Verified by successful invocation only."}


def _verify_content_delta(arguments: dict[str, Any], content_before: dict[str, Any]) -> dict[str, Any]:
    content_after = content_actions.get_content_state()
    if "error" in content_after:
        return {"verified": False, "reason": content_after["error"]}

    expected_text = arguments.get("expected_contains")
    if expected_text and not _document_contains(content_after, str(expected_text)):
        return {"verified": False, "reason": f"Expected content not found: {expected_text!r}"}

    if "paragraph_count_delta" in arguments:
        before = int(content_before.get("paragraph_count", 0))
        after = int(content_after.get("paragraph_count", 0))
        delta = int(arguments["paragraph_count_delta"])
        if after - before != delta:
            return {
                "verified": False,
                "reason": "Paragraph count delta mismatch.",
                "expected_delta": delta,
                "actual_delta": after - before,
            }
    return {"verified": True}


def verify_execution_plan(
    schema: WordSchema,
    plan: dict[str, Any],
    execution_result: dict[str, Any],
    content_before: dict[str, Any],
    property_before: dict[str, Any],
) -> dict[str, Any]:
    steps = plan.get("steps", [])
    step_results = execution_result.get("steps", [])
    for step, result in zip(steps, step_results):
        verification = verify_step(schema, step, result, content_before, property_before)
        if not verification.get("verified"):
            return {"verified": False, "failed_step": step, "result": result, **verification}
        content_before = content_actions.get_content_state()
        if "error" in content_before:
            return {"verified": False, "reason": content_before["error"]}
    return {"verified": True}


def _document_contains(state: dict[str, Any], text: str) -> bool:
    haystack = " ".join(p.get("text", "") for p in state.get("paragraphs", []))
    haystack += " " + str(state.get("selection_text", ""))
    return text in haystack


def _paragraph_text(state: dict[str, Any], index: int) -> str:
    entry = _paragraph_entry(state, index)
    return entry.get("text", "") if entry else ""


def _paragraph_entry(state: dict[str, Any], index: int) -> dict[str, Any] | None:
    for paragraph in state.get("paragraphs", []):
        if int(paragraph.get("index", 0)) == index:
            return paragraph
    if 1 <= index <= len(state.get("paragraphs", [])):
        return state["paragraphs"][index - 1]
    return None
