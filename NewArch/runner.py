"""Orchestrate capability discovery, planning, execution, verification, and repair."""
from __future__ import annotations

import json
from typing import Any

from . import content_actions
from .config import LOGGER, LOG_PATH
from .execution_runtime import execute_planner_output
from .planner import plan_action
from .property_actions import read_properties
from .selectors import select_capabilities, select_fields
from .verifier import verify_execution_plan
from .word_schema import WordSchema


def _summarize_content_state(state: dict[str, Any]) -> str:
    if "error" in state:
        return f"error={state['error']}"
    return (
        f"name={state.get('name', '')} "
        f"paragraphs={state.get('paragraph_count', 0)} "
        f"tables={state.get('table_count', 0)} "
        f"words={state.get('word_count', 0)} "
        f"saved={state.get('saved', False)}"
    )


def _append_history(history: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    history.append(entry)
    if len(history) > 12:
        del history[:-12]


def _write_trajectory(task: str, history: list[dict[str, Any]]) -> None:
    trajectory_path = LOG_PATH.parent / "trajectory.jsonl"
    try:
        with trajectory_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"task": task, "history": history}) + "\n")
    except OSError:
        LOGGER.warning("Could not write trajectory log.")


def _print_execution_result(result: dict[str, Any]) -> None:
    if result.get("type") == "context_request":
        LOGGER.info("Context request satisfied | success=%s", result.get("success"))
        return
    for step in result.get("steps", []):
        LOGGER.info(
            "Step result | step=%s | command=%s | success=%s",
            step.get("step_index"),
            step.get("command"),
            step.get("success"),
        )


CONTENT_TASK_HINTS = (
    "insert",
    "add",
    "write",
    "replace",
    "delete",
    "table",
    "bold",
    "italic",
    "format",
    "find",
    "save",
    "heading",
    "paragraph",
    "text",
    "font",
)


def _default_capability_selection(task: str) -> dict[str, Any]:
    lowered = task.lower()
    classes = ["document", "paragraph", "font", "table"]
    if any(token in lowered for token in ("section", "header", "footer", "margin", "page")):
        classes.append("section")
    return {
        "classes": classes,
        "inspect_properties": any(
            token in lowered for token in ("margin", "page", "orientation", "border", "style", "header", "footer")
        ),
        "reasoning": "Heuristic defaults for a content-focused task.",
    }


def _looks_content_focused(task: str) -> bool:
    lowered = task.lower()
    return any(hint in lowered for hint in CONTENT_TASK_HINTS)


def run(task: str, max_steps: int = 20) -> bool:
    LOGGER.info("=" * 60)
    LOGGER.info("ScriptAgent task: %s", task)

    schema = WordSchema()
    history: list[dict[str, Any]] = []

    LOGGER.info("selecting capabilities")
    try:
        if _looks_content_focused(task):
            capability_selection = _default_capability_selection(task)
            LOGGER.info("Using heuristic capability selection for content-focused task.")
        else:
            capability_selection = select_capabilities(schema, task)
    except Exception as exc:
        LOGGER.error("Capability discovery failed: %s", exc)
        return False

    class_names = capability_selection["classes"]
    LOGGER.info("Relevant Word classes: %s", ", ".join(class_names))

    field_schema: list[dict[str, Any]] = []
    property_state: dict[str, Any] = {}
    if capability_selection.get("inspect_properties", True):
        LOGGER.info("building field schema")
        field_selection = select_fields(schema, task, class_names)
        field_schema = field_selection.get("field_schema", [])
        selected_paths = field_selection.get("paths", [])
        LOGGER.info("Available schema fields: %s", ", ".join(selected_paths[:10]))
        if selected_paths:
            LOGGER.info("Select metadata fields")
            property_state = read_properties(schema, selected_paths)

    content_state = content_actions.get_content_state()
    if "error" in content_state:
        LOGGER.error("State read failed | %s", content_state["error"])
        return False
    LOGGER.info("Document state | %s", _summarize_content_state(content_state))

    for step_number in range(1, max_steps + 1):
        LOGGER.info("Planning iteration %s/%s", step_number, max_steps)
        try:
            planner_output = plan_action(
                schema=schema,
                task=task,
                class_names=class_names,
                field_schema=field_schema,
                content_state=content_state,
                property_state=property_state,
                history=history,
            )
        except Exception as exc:
            LOGGER.error("Planning failed: %s", exc)
            _append_history(history, {"type": "planning_error", "error": str(exc)})
            continue

        output_type = planner_output.get("type")
        if output_type == "execution_plan" and not planner_output.get("steps"):
            LOGGER.info("TASK COMPLETE | planner returned empty execution plan")
            _write_trajectory(task, history)
            return True

        content_before = dict(content_state)
        property_before = dict(property_state)

        LOGGER.info("Executing planner output | type=%s", output_type)
        execution_result = execute_planner_output(schema, planner_output)

        if output_type == "context_request":
            if not execution_result.get("success"):
                _append_history(
                    history,
                    {
                        "type": "context_request_failed",
                        "planner_output": planner_output,
                        "result": execution_result,
                    },
                )
                continue
            content_state = execution_result.get("content_state") or content_actions.get_content_state()
            _append_history(
                history,
                {
                    "type": "context_request",
                    "planner_output": planner_output,
                    "content_state": content_state,
                },
            )
            LOGGER.info("Context retrieved. Replanning with expanded history.")
            continue

        _print_execution_result(execution_result)
        if not execution_result.get("success"):
            _append_history(
                history,
                {
                    "type": "execution_failed",
                    "planner_output": planner_output,
                    "execution_result": execution_result,
                },
            )
            content_state = content_actions.get_content_state()
            continue

        verification = verify_execution_plan(
            schema=schema,
            plan=planner_output,
            execution_result=execution_result,
            content_before=content_before,
            property_before=property_before,
        )
        LOGGER.info("Verification | verified=%s", verification.get("verified"))
        if not verification.get("verified"):
            _append_history(
                history,
                {
                    "type": "verification_failed",
                    "planner_output": planner_output,
                    "execution_result": execution_result,
                    "verification": verification,
                },
            )
            content_state = content_actions.get_content_state()
            property_state = read_properties(schema, [item["path"] for item in field_schema]) if field_schema else {}
            continue

        _append_history(
            history,
            {
                "type": "execution_success",
                "planner_output": planner_output,
                "execution_result": execution_result,
                "verification": verification,
            },
        )
        content_state = content_actions.get_content_state()
        property_state = read_properties(schema, [item["path"] for item in field_schema]) if field_schema else {}

    LOGGER.warning("Maximum step count reached before the planner returned done.")
    _write_trajectory(task, history)
    return False
