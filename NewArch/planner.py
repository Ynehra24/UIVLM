"""Build capability graphs and produce structured planner output."""
from __future__ import annotations

import json
from typing import Any

from .execution_runtime import EXECUTOR_ARGUMENT_SCHEMAS, EXECUTOR_CAPABILITIES
from .gemini_client import call_llm_json
from .prompts import PLANNER_PROMPT
from .property_actions import compact_property_state
from .word_schema import WordSchema

CONTEXT_RETRIEVAL_CAPABILITIES = {
    "inspect_object_window": {
        "description": "Retrieve a bounded window of document context for an object type.",
        "arguments": {
            "object": "paragraph|table|section|style|header|footer|comment",
            "start": "1-indexed starting object",
            "limit": "number of objects to inspect",
            "preview_chars": "maximum text returned per object",
        },
    },
    "inspect_object_detail": {
        "description": "Retrieve deeper context for one document object.",
        "arguments": {
            "object": "paragraph|table|section|style|header|footer|comment",
            "index": "1-indexed object index",
            "preview_chars": "maximum text returned",
        },
    },
}

VERIFICATION_MODES = {
    "command_invocation": "Execute the command, then reread affected object/content state.",
    "property_write": "Read the canonical property before and after writing.",
    "content_delta": "Compare document content before and after the step.",
    "completion": "Return done only when inspected state proves completion or capability is unavailable.",
}


def build_capability_graph(
    schema: WordSchema,
    task: str,
    class_names: list[str],
    field_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    search = schema.search(task, max_results=15)
    focused = schema.focused_context(
        class_names=class_names,
        command_names=[item["name"] for item in search.get("commands", [])],
        enum_names=[item["name"] for item in search.get("enumerations", [])],
    )
    return {
        "objects": focused["objects"],
        "commands": search.get("commands", []) + focused.get("commands", []),
        "enumerations": search.get("enumerations", []) + focused.get("enumerations", []),
        "field_paths": field_schema,
        "executor_capabilities": [
            {
                "command": name,
                "description": description,
                "arguments": EXECUTOR_ARGUMENT_SCHEMAS.get(name, {}),
            }
            for name, description in EXECUTOR_CAPABILITIES.items()
        ],
        "context_retrieval_capabilities": [
            {"capability": name, **meta} for name, meta in CONTEXT_RETRIEVAL_CAPABILITIES.items()
        ],
    }


def build_verification_policy() -> dict[str, str]:
    return VERIFICATION_MODES


def build_planning_prompt(
    task: str,
    capability_graph: dict[str, Any],
    content_state: dict[str, Any],
    property_state: dict[str, Any],
    history: list[dict[str, Any]],
) -> str:
    history_text = json.dumps(history[-6:], indent=2) if history else "[]"
    return f"""
{PLANNER_PROMPT}

USER TASK:
{task}

CAPABILITY GRAPH:
{json.dumps(capability_graph, indent=2)}

DOCUMENT CONTENT STATE:
{json.dumps(content_state, indent=2)}

INSPECTED PROPERTY STATE:
{json.dumps(compact_property_state(property_state), indent=2)}

VERIFICATION MODES:
{json.dumps(build_verification_policy(), indent=2)}

RECENT HISTORY:
{history_text}
"""


def plan_action(
    schema: WordSchema,
    task: str,
    class_names: list[str],
    field_schema: list[dict[str, Any]],
    content_state: dict[str, Any],
    property_state: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any]:
    capability_graph = build_capability_graph(schema, task, class_names, field_schema)
    prompt = build_planning_prompt(task, capability_graph, content_state, property_state, history)
    plan = call_llm_json(prompt)
    return _validate_plan(plan, capability_graph, field_schema)


def _validate_plan(
    plan: dict[str, Any],
    capability_graph: dict[str, Any],
    field_schema: list[dict[str, Any]],
) -> dict[str, Any]:
    plan_type = plan.get("type")
    if plan_type == "context_request":
        retrieval = plan.get("retrieval", {})
        capability = retrieval.get("capability")
        if capability not in CONTEXT_RETRIEVAL_CAPABILITIES:
            plan["reasoning"] = (plan.get("reasoning", "") + " Invalid context capability requested.").strip()
            plan = {"type": "execution_plan", "steps": [], "reasoning": plan["reasoning"]}
        return plan

    if plan_type != "execution_plan":
        return {"type": "execution_plan", "steps": [], "reasoning": "Planner returned unsupported output type."}

    allowed_commands = set(EXECUTOR_CAPABILITIES) | {
        item["name"].lower() for item in capability_graph.get("commands", [])
    }
    allowed_paths = {item["path"] for item in field_schema}
    validated_steps = []

    for step in plan.get("steps", []):
        command = step.get("command")
        if not command:
            continue
        command_text = str(command)
        if command_text == "property_write":
            path = step.get("arguments", {}).get("path") or step.get("target")
            if path and path not in allowed_paths:
                continue
        elif command_text not in EXECUTOR_CAPABILITIES and command_text.lower() not in allowed_commands:
            continue
        if step.get("verification") not in VERIFICATION_MODES:
            step["verification"] = "command_invocation"
        validated_steps.append(step)

    plan["steps"] = validated_steps
    return plan
