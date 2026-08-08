"""Deterministic execution of planner output."""
from __future__ import annotations

from typing import Any, Callable

from . import content_actions
from .applescript import run_applescript_safe
from .property_actions import execute_property_changes
from .property_path import PropertyPath
from .word_schema import WordSchema

EXECUTOR_CAPABILITIES = {
    "insert_text": "Insert text at document start, document end, or current selection.",
    "replace_paragraph": "Replace one 1-indexed paragraph's text content.",
    "delete_paragraph": "Delete one 1-indexed paragraph.",
    "format_paragraph": "Apply simple font formatting to one paragraph.",
    "insert_table": "Insert a table, optionally replacing an existing paragraph, with optional cell shading.",
    "find_replace": "Find and replace text across the active document.",
    "save_document": "Save the active document.",
    "property_write": "Set one canonical writable property path from the active field schema.",
}

EXECUTOR_ARGUMENT_SCHEMAS = {
    "insert_text": {"text": "str", "position": "start|end|selection"},
    "replace_paragraph": {"paragraph": "int", "text": "str"},
    "delete_paragraph": {"paragraph": "int"},
    "format_paragraph": {
        "paragraph": "int (optional)",
        "bold": "bool (optional)",
        "italic": "bool (optional)",
        "font_name": "str (optional)",
        "font_size": "number (optional)",
    },
    "insert_table": {
        "rows": "int",
        "columns": "int",
        "values": "rows x columns nested list (optional)",
        "paragraph": "int (optional)",
        "cell_colors": "rows x columns color names (optional)",
    },
    "find_replace": {"find": "str", "replace": "str"},
    "save_document": {},
    "property_write": {"path": "canonical path", "value": "any"},
}

CONTENT_CAPABILITY_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "insert_text": content_actions.insert_text,
    "replace_paragraph": content_actions.replace_paragraph,
    "delete_paragraph": content_actions.delete_paragraph,
    "format_paragraph": content_actions.format_paragraph,
    "insert_table": content_actions.insert_table,
    "find_replace": content_actions.find_replace,
    "save_document": content_actions.save_document,
}


def applescript_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "{" + ", ".join(applescript_value(item) for item in value) + "}"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def compile_sdef_command(schema: WordSchema, command_name: str, arguments: dict[str, Any]) -> str:
    command = schema.command_index.get(command_name.lower())
    if not command:
        raise ValueError(f"Command is not present in Word SDEF: {command_name}")

    parameter_literals = []
    for parameter in command.get("parameters", []):
        name = parameter["name"]
        if name not in arguments and not parameter.get("optional"):
            raise ValueError(f"Missing required parameter '{name}' for command '{command_name}'")
        if name in arguments:
            parameter_literals.append(f"{name} {applescript_value(arguments[name])}")

    parameter_text = ", ".join(parameter_literals)
    if parameter_text:
        return (
            f'tell application "Microsoft Word"\n'
            f"    {command['name']} {parameter_text}\n"
            f"end tell"
        )
    return f'tell application "Microsoft Word"\n    {command["name"]}\nend tell'


def execute_sdef_command(schema: WordSchema, command_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    script = compile_sdef_command(schema, command_name, arguments)
    stdout, stderr, code = run_applescript_safe(script, timeout=30)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout, "script": script}


def execute_property_write(schema: WordSchema, step: dict[str, Any]) -> dict[str, Any]:
    arguments = step.get("arguments", {})
    path = arguments.get("path") or step.get("target")
    value = arguments.get("value")
    if not path:
        return {"success": False, "error": "Unknown or unavailable property path: "}
    try:
        property_path = PropertyPath.parse(schema, path)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    access = property_path.segments[-1].get("access", "")
    if access.lower() == "r":
        return {"success": False, "error": f"Property path is read-only: {path}"}
    return execute_property_changes(schema, [{"path": path, "value": value}])


def execute_capability_step(schema: WordSchema, step: dict[str, Any]) -> dict[str, Any]:
    command = step.get("command")
    arguments = step.get("arguments", {})
    if command == "property_write":
        return execute_property_write(schema, step)
    handler = CONTENT_CAPABILITY_HANDLERS.get(command or "")
    if handler:
        result = handler(arguments)
        return {"success": result.get("success", False), **result, "command": command}
    if command and schema.command_index.get(str(command).lower()):
        return execute_sdef_command(schema, str(command), arguments)
    return {"success": False, "error": f"Unsupported executor capability: {command}"}


def execute_execution_plan(schema: WordSchema, plan: dict[str, Any]) -> dict[str, Any]:
    steps = plan.get("steps") or []
    if not steps:
        return {"success": False, "error": "Planner returned an empty execution plan.", "steps": []}

    step_results = []
    for index, step in enumerate(steps, start=1):
        result = execute_capability_step(schema, step)
        result["step_index"] = index
        result["verification"] = step.get("verification", "command_invocation")
        step_results.append(result)
        if not result.get("success"):
            return {"success": False, "steps": step_results, "failed_step": index}
    return {"success": True, "steps": step_results}


def execute_context_request(retrieval: dict[str, Any]) -> dict[str, Any]:
    return content_actions.execute_context_request(retrieval)


def execute_planner_output(schema: WordSchema, planner_output: dict[str, Any]) -> dict[str, Any]:
    output_type = planner_output.get("type")
    if output_type == "context_request":
        retrieval = planner_output.get("retrieval", {})
        result = execute_context_request(retrieval)
        return {"type": "context_request", **result}
    if output_type == "execution_plan":
        result = execute_execution_plan(schema, planner_output)
        return {"type": "execution_plan", **result}
    return {"success": False, "error": f"Unsupported planner output type: {output_type}"}
