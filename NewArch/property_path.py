"""Validates and compiles canonical property paths into AppleScript."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .applescript import run_applescript_safe
from .utils import applescript_string, normalize_name
from .word_schema import WordSchema

SEGMENT_PATTERN = re.compile(r"^([a-z0-9_]+)(?:\[(\*|\d+)\])?$")


@dataclass
class PropertyPath:
    schema: WordSchema
    path: str
    segments: list[dict[str, Any]]
    owner_class: str
    property: str
    wildcard: bool = False

    @classmethod
    def parse(cls, schema: WordSchema, path: str) -> "PropertyPath":
        raw_segments = [segment.strip() for segment in path.split(".") if segment.strip()]
        if not raw_segments:
            raise ValueError(f"Invalid property path: {path}")

        segments: list[dict[str, Any]] = []
        wildcard = False
        current_class = raw_segments[0].split("[", 1)[0]
        class_item = schema.get_element(current_class)
        if not class_item:
            raise ValueError(f"Invalid property root in path: {path}")

        for index, raw_segment in enumerate(raw_segments):
            match = SEGMENT_PATTERN.fullmatch(normalize_name(raw_segment.replace("[", "_").replace("]", "")))
            if not match and "[" in raw_segment:
                base, bracket = raw_segment.split("[", 1)
                bracket = bracket.rstrip("]")
                segment_name = normalize_name(base)
                index_value = "*" if bracket == "*" else int(bracket)
                match = True
            else:
                segment_name = match.group(1) if match else None
                index_value = None
                if match and match.group(2):
                    index_value = "*" if match.group(2) == "*" else int(match.group(2))

            if index == len(raw_segments) - 1:
                property_item = schema.get_property(current_class, raw_segment.split(".")[-1])
                if not property_item:
                    property_item = schema.get_property(current_class, segment_name.replace("_", " "))
                if not property_item:
                    raise ValueError(f"{raw_segment} is not a property of {current_class}")
                segments.append(
                    {
                        "kind": "property",
                        "name": property_item["name"],
                        "type": property_item.get("type", ""),
                        "access": property_item.get("access", ""),
                    }
                )
                owner_class = current_class
                property_name = property_item["name"]
                break

            if segment_name is None:
                raise ValueError(f"Invalid path segment: {raw_segment}")

            if index == 0:
                segments.append({"kind": "root", "name": class_item["name"], "expression": "document 1"})
                current_class = class_item["name"]
                continue

            parent = schema.get_element(current_class)
            if not parent:
                raise ValueError(f"Invalid path segment: {raw_segment}")

            element_name = None
            for candidate in parent.get("elements", []):
                if normalize_name(candidate) == segment_name:
                    element_name = candidate
                    break
            if not element_name:
                raise ValueError(f"{segment_name} is not an object property of {current_class}")

            if index_value is None:
                raise ValueError(f"Collection path requires an index: {raw_segment}")
            if index_value == "*":
                wildcard = True
                if any(item.get("wildcard") for item in segments):
                    raise ValueError("Only one wildcard collection is supported per path.")
            expression = (
                f"{element_name} {index_value}"
                if index_value != "*"
                else f"every {element_name}"
            )
            segments.append(
                {
                    "kind": "element",
                    "name": element_name,
                    "index": index_value,
                    "wildcard": index_value == "*",
                    "expression": expression,
                }
            )
            current_class = element_name
        else:
            raise ValueError(f"Path does not terminate in a property: {path}")

        return cls(
            schema=schema,
            path=path,
            segments=segments,
            owner_class=owner_class,
            property=property_name,
            wildcard=wildcard,
        )

    def object_expression(self) -> str:
        expression = "document 1"
        for segment in self.segments:
            if segment["kind"] == "root":
                expression = segment["expression"]
            elif segment["kind"] == "element":
                if segment.get("wildcard"):
                    return f"every {segment['name']} of {expression}"
                expression = f"{segment['expression']} of {expression}"
        return expression

    @staticmethod
    def _coerce_primitive(value: Any, property_type: str) -> Any:
        lowered = property_type.lower()
        if lowered in {"boolean", "bool"}:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in {"true", "yes", "1"}
            return bool(value)
        if lowered in {"integer", "int"}:
            return int(value)
        if lowered in {"real", "float", "number"}:
            return float(value)
        return value

    @staticmethod
    def _value_literal(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            rendered = ", ".join(PropertyPath._value_literal(item) for item in value)
            return "{" + rendered + "}"
        return f'"{applescript_string(str(value))}"'

    def compile(self, value: Any) -> str:
        target = self.object_expression()
        coerced = self._coerce_primitive(value, self.segments[-1].get("type", ""))
        literal = self._value_literal(coerced)
        return (
            f'tell application "Microsoft Word"\n'
            f"    tell {target}\n"
            f"        set {self.property} to {literal}\n"
            f"    end tell\n"
            f"end tell"
        )

    def read(self) -> list[str]:
        target = self.object_expression()
        property_name = self.property
        script = f'''
on scalarText(valueObject)
    if valueObject is missing value then return "__MISSING__"
    if class of valueObject is list then
        set oldDelimiters to AppleScript's text item delimiters
        set AppleScript's text item delimiters to ","
        set rendered to valueObject as text
        set AppleScript's text item delimiters to oldDelimiters
        return rendered
    end if
    return valueObject as text
end scalarText

set outputItems to {{}}
tell application "Microsoft Word"
    tell {target}
        try
            set end of outputItems to my scalarText({property_name})
        on error errorMessage
            set end of outputItems to "__ERROR__" & errorMessage
        end try
    end tell
end tell
set oldDelimiters to AppleScript's text item delimiters
set AppleScript's text item delimiters to "|||UIVLM_ITEM|||"
set renderedOutput to outputItems as text
set AppleScript's text item delimiters to oldDelimiters
return renderedOutput
'''
        stdout, stderr, code = run_applescript_safe(script, timeout=30)
        if code != 0:
            raise RuntimeError(stderr or "Word property read failed.")
        if not stdout:
            return []
        return [item for item in stdout.split("|||UIVLM_ITEM|||") if item]

    @staticmethod
    def normalized_expected(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)
