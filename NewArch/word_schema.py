"""Parse Word's installed AppleScript dictionary into a searchable schema."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import deque
from functools import cached_property
from pathlib import Path
from typing import Any

from .config import LOGGER, WORD_SDEF_PATHS
from .utils import normalize_name


class WordSchema:
    """Parses Word's installed AppleScript dictionary into a searchable schema."""

    def __init__(self, sdef_path: str | Path | None = None) -> None:
        self.sdef_path = self._resolve_sdef_path(sdef_path)
        self._tree = ET.parse(self.sdef_path)
        self._root = self._tree.getroot()

    @staticmethod
    def _resolve_sdef_path(explicit_path: str | Path | None) -> Path:
        if explicit_path:
            candidate = Path(explicit_path).expanduser()
            if candidate.exists():
                return candidate
            raise FileNotFoundError(f"Word sdef not found: {candidate}")

        for candidate in WORD_SDEF_PATHS:
            path = Path(candidate).expanduser()
            if path.exists():
                return path

        raise FileNotFoundError("Microsoft Word's Word.sdef dictionary was not found.")

    @staticmethod
    def _text(value: str | None) -> str:
        return value.strip() if value else ""

    def _iter(self, tag: str) -> list[ET.Element]:
        return self._root.findall(f".//{{*}}{tag}")

    @cached_property
    def commands(self) -> list[dict[str, Any]]:
        commands: list[dict[str, Any]] = []
        for command in self._iter("command"):
            commands.append(
                {
                    "name": self._text(command.get("name")),
                    "code": self._text(command.get("code")),
                    "description": self._text(command.get("description")),
                    "parameters": [
                        {
                            "name": self._text(parameter.get("name")),
                            "code": self._text(parameter.get("code")),
                            "type": self._text(parameter.get("type")),
                            "optional": parameter.get("optional", "no").lower() == "yes",
                            "description": self._text(parameter.get("description")),
                        }
                        for parameter in command.findall("./{*}parameter")
                    ],
                }
            )
        return commands

    @cached_property
    def classes(self) -> list[dict[str, Any]]:
        classes: list[dict[str, Any]] = []
        for class_element in self._iter("class"):
            classes.append(
                {
                    "name": self._text(class_element.get("name")),
                    "code": self._text(class_element.get("code")),
                    "plural": self._text(class_element.get("plural")),
                    "inherits": self._text(class_element.get("inherits")),
                    "description": self._text(class_element.get("description")),
                    "properties": [
                        {
                            "name": self._text(property_element.get("name")),
                            "code": self._text(property_element.get("code")),
                            "type": self._text(property_element.get("type")),
                            "access": self._text(property_element.get("access")),
                            "description": self._text(property_element.get("description")),
                        }
                        for property_element in class_element.findall("./{*}property")
                    ],
                    "elements": [
                        self._text(element.get("type"))
                        for element in class_element.findall("./{*}element")
                        if self._text(element.get("type"))
                    ],
                }
            )
        return classes

    @cached_property
    def enumerations(self) -> list[dict[str, Any]]:
        enumerations: list[dict[str, Any]] = []
        for enumeration in self._iter("enumeration"):
            enumerations.append(
                {
                    "name": self._text(enumeration.get("name")),
                    "code": self._text(enumeration.get("code")),
                    "enumerators": [
                        {
                            "name": self._text(enumerator.get("name")),
                            "code": self._text(enumerator.get("code")),
                        }
                        for enumerator in enumeration.findall("./{*}enumerator")
                    ],
                }
            )
        return enumerations

    @cached_property
    def class_index(self) -> dict[str, dict[str, Any]]:
        return {item["name"].lower(): item for item in self.classes if item["name"]}

    @cached_property
    def command_index(self) -> dict[str, dict[str, Any]]:
        return {item["name"].lower(): item for item in self.commands if item["name"]}

    @cached_property
    def enumeration_codes(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str]] = {}
        for enumeration in self.enumerations:
            enum_name = enumeration["name"]
            mapping[enum_name.lower()] = {
                item["name"].lower(): item["code"] or item["name"]
                for item in enumeration.get("enumerators", [])
            }
        return mapping

    def enum_values(self, enum_name: str) -> list[str]:
        enumeration = next(
            (item for item in self.enumerations if item["name"].lower() == enum_name.lower()),
            None,
        )
        if not enumeration:
            return []
        return [item["name"] for item in enumeration.get("enumerators", [])]

    def get_element(self, name: str) -> dict[str, Any] | None:
        return self.class_index.get(name.lower())

    def get_property(self, class_name: str, property_name: str) -> dict[str, Any] | None:
        class_item = self.get_element(class_name)
        if not class_item:
            return None
        for property_item in class_item.get("properties", []):
            if property_item["name"].lower() == property_name.lower():
                return property_item
        return None

    def inherited_properties(self, class_name: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        properties: list[dict[str, Any]] = []
        current = self.get_element(class_name)
        while current:
            for property_item in current.get("properties", []):
                key = property_item["name"].lower()
                if key not in seen:
                    seen.add(key)
                    properties.append(property_item)
            parent = current.get("inherits")
            current = self.get_element(parent) if parent else None
        return properties

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2]

    def _search_text(self, haystack: str, tokens: list[str]) -> int:
        lowered = haystack.lower()
        score = sum(1 for token in tokens if token in lowered)
        return score

    def search(self, query: str, max_results: int = 20) -> dict[str, list[dict[str, Any]]]:
        tokens = self._tokenize(query)
        if not tokens:
            return {"commands": [], "classes": [], "enumerations": []}

        scored_commands: list[tuple[int, dict[str, Any]]] = []
        for command in self.commands:
            haystack = " ".join(
                [
                    command["name"],
                    command["description"],
                    " ".join(parameter["name"] for parameter in command.get("parameters", [])),
                ]
            )
            score = self._search_text(haystack, tokens)
            if score:
                if any(token in command["name"].lower() for token in tokens):
                    score += 5
                scored_commands.append((score, command))

        scored_classes: list[tuple[int, dict[str, Any]]] = []
        for class_item in self.classes:
            haystack = " ".join(
                [
                    class_item["name"],
                    class_item["description"],
                    " ".join(property_item["name"] for property_item in class_item.get("properties", [])),
                    " ".join(class_item.get("elements", [])),
                ]
            )
            score = self._search_text(haystack, tokens)
            if score:
                if any(token in class_item["name"].lower() for token in tokens):
                    score += 5
                scored_classes.append((score, class_item))

        scored_enumerations: list[tuple[int, dict[str, Any]]] = []
        for enumeration in self.enumerations:
            haystack = " ".join(
                [enumeration["name"], " ".join(item["name"] for item in enumeration.get("enumerators", []))]
            )
            score = self._search_text(haystack, tokens)
            if score:
                if any(token in enumeration["name"].lower() for token in tokens):
                    score += 5
                scored_enumerations.append((score, enumeration))

        scored_commands.sort(key=lambda item: (-item[0], item[1]["name"]))
        scored_classes.sort(key=lambda item: (-item[0], item[1]["name"]))
        scored_enumerations.sort(key=lambda item: (-item[0], item[1]["name"]))

        return {
            "commands": [item[1] for item in scored_commands[:max_results]],
            "classes": [item[1] for item in scored_classes[:max_results]],
            "enumerations": [item[1] for item in scored_enumerations[:max_results]],
        }

    def class_snippet(self, class_name: str) -> dict[str, Any] | None:
        class_item = self.get_element(class_name)
        if not class_item:
            return None
        return {
            "name": class_item["name"],
            "description": class_item["description"],
            "properties": [
                {
                    "name": property_item["name"],
                    "type": property_item["type"],
                    "access": property_item["access"],
                }
                for property_item in self.inherited_properties(class_item["name"])
            ],
            "elements": class_item.get("elements", []),
        }

    def _routes_from_root(self, root_name: str) -> list[list[str]]:
        routes: list[list[str]] = [[root_name]]
        queue: deque[list[str]] = deque([[root_name]])
        seen = {root_name.lower()}

        while queue:
            route = queue.popleft()
            current = self.get_element(route[-1])
            if not current:
                continue
            for element_type in current.get("elements", []):
                lowered = element_type.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                next_route = route + [element_type]
                routes.append(next_route)
                queue.append(next_route)
        return routes

    def reachable_field_schema(self, class_names: list[str]) -> list[dict[str, Any]]:
        fields: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        roots = class_names or ["document"]
        for root in roots:
            for route in self._routes_from_root(root):
                for index, class_name in enumerate(route):
                    class_item = self.get_element(class_name)
                    if not class_item:
                        continue
                    prefix_parts = []
                    for segment_index, segment in enumerate(route[: index + 1]):
                        if segment_index == 0:
                            prefix_parts.append(normalize_name(segment))
                        else:
                            prefix_parts.append(f"{normalize_name(segment)}[1]")
                    prefix = ".".join(prefix_parts)

                    for property_item in self.inherited_properties(class_name):
                        access = property_item.get("access", "")
                        if access and access.lower() == "r":
                            continue
                        path = f"{prefix}.{normalize_name(property_item['name'])}"
                        if path in seen_paths:
                            continue
                        seen_paths.add(path)
                        fields.append(
                            {
                                "path": path,
                                "class": class_name,
                                "property": property_item["name"],
                                "type": property_item["type"],
                                "access": property_item.get("access", ""),
                            }
                        )
        return sorted(fields, key=lambda item: item["path"])

    def focused_context(
        self,
        class_names: list[str],
        command_names: list[str] | None = None,
        enum_names: list[str] | None = None,
    ) -> dict[str, Any]:
        objects = [self.class_snippet(name) for name in class_names]
        objects = [item for item in objects if item]

        commands = []
        for name in command_names or []:
            command = self.command_index.get(name.lower())
            if command:
                commands.append(command)

        enumerations = []
        for name in enum_names or []:
            enumeration = next(
                (item for item in self.enumerations if item["name"].lower() == name.lower()),
                None,
            )
            if enumeration:
                enumerations.append(
                    {
                        "name": enumeration["name"],
                        "values": [item["name"] for item in enumeration.get("enumerators", [])],
                    }
                )

        return {
            "objects": objects,
            "commands": commands,
            "enumerations": enumerations,
        }

    def search_documents(self, query: str, max_results: int = 20) -> dict[str, list[dict[str, Any]]]:
        return self.search(query, max_results=max_results)
