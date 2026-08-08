"""Word SDEF discovery with a persistent, automatically invalidated cache.

The Word scripting dictionary is immutable until Word is upgraded, so repeatedly
parsing its XML is needless work.  This module is the single catalog source for
the dynamic NewArch agent: it parses once, stores the complete catalog in
SQLite, then serves subsequent processes from that cache.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import SDEF_DB_PATH, WORD_SDEF_PATHS

CACHE_FORMAT_VERSION = 2
_CATALOG_TABLE = "sdef_catalog"


def resolve_sdef_path(explicit_path: Optional[str] = None) -> Path:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.exists():
            return candidate.resolve()
        raise FileNotFoundError(f"Word sdef not found: {candidate}")

    for candidate in WORD_SDEF_PATHS:
        candidate_path = Path(candidate).expanduser()
        if candidate_path.exists():
            return candidate_path.resolve()

    raise FileNotFoundError("Could not locate Microsoft Word.sdef in the configured search paths")


@lru_cache(maxsize=4)
def load_sdef_tree(explicit_path: Optional[str] = None) -> ET.ElementTree:
    """Load raw XML for callers that explicitly need the ElementTree."""
    return ET.parse(resolve_sdef_path(explicit_path))


def _text(value: Optional[str]) -> str:
    return value.strip() if value else ""


def _iter_children(element: ET.Element, tag: str) -> Iterable[ET.Element]:
    return element.findall(f".//{{*}}{tag}")


def _parse_sdef_catalog(sdef_path: Path) -> dict[str, Any]:
    """Parse the installed SDEF into JSON-serialisable catalog data."""
    root = ET.parse(sdef_path).getroot()
    commands: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    enumerations: list[dict[str, Any]] = []

    for command in _iter_children(root, "command"):
        # direct-parameter: the class/type the command directly operates on
        dp_el = command.find("./{*}direct-parameter")
        direct_parameter_type = _text(dp_el.get("type")) if dp_el is not None else ""

        # result: the type of object the command produces / creates
        result_el = command.find("./{*}result")
        result_type = _text(result_el.get("type")) if result_el is not None else ""

        commands.append(
            {
                "name": _text(command.get("name")),
                "code": _text(command.get("code")),
                "description": _text(command.get("description")),
                "direct_parameter_type": direct_parameter_type,
                "result_type": result_type,
                "parameters": [
                    {
                        "name": _text(parameter.get("name")),
                        "code": _text(parameter.get("code")),
                        "type": _text(parameter.get("type")),
                        "optional": parameter.get("optional", "no").lower() == "yes",
                        "description": _text(parameter.get("description")),
                    }
                    for parameter in command.findall("./{*}parameter")
                ],
            }
        )

    for class_element in _iter_children(root, "class"):
        classes.append(
            {
                "name": _text(class_element.get("name")),
                "code": _text(class_element.get("code")),
                "plural": _text(class_element.get("plural")),
                "inherits": _text(class_element.get("inherits")),
                "description": _text(class_element.get("description")),
                "properties": [
                    {
                        "name": _text(property_element.get("name")),
                        "code": _text(property_element.get("code")),
                        "type": _text(property_element.get("type")),
                        "access": _text(property_element.get("access")),
                        "description": _text(property_element.get("description")),
                    }
                    for property_element in class_element.findall("./{*}property")
                ],
                "elements": [
                    _text(element.get("type"))
                    for element in class_element.findall("./{*}element")
                    if _text(element.get("type"))
                ],
            }
        )

    for enumeration in _iter_children(root, "enumeration"):
        enumerations.append(
            {
                "name": _text(enumeration.get("name")),
                "code": _text(enumeration.get("code")),
                "enumerators": [
                    {
                        "name": _text(enumerator.get("name")),
                        "code": _text(enumerator.get("code")),
                    }
                    for enumerator in enumeration.findall("./{*}enumerator")
                ],
            }
        )

    return {
        "sdef_path": str(sdef_path),
        "command_count": len(commands),
        "class_count": len(classes),
        "enumeration_count": len(enumerations),
        "command_names": [command["name"] for command in commands],
        "commands": commands,
        "classes": classes,
        "enumerations": enumerations,
    }


def _cache_path(cache_path: Optional[str | Path]) -> Path:
    return Path(cache_path or SDEF_DB_PATH).expanduser()


def _connect(cache_file: Path) -> sqlite3.Connection:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(cache_file, timeout=15)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_CATALOG_TABLE} (
            source_path TEXT PRIMARY KEY,
            source_mtime_ns INTEGER NOT NULL,
            source_size INTEGER NOT NULL,
            format_version INTEGER NOT NULL,
            catalog_json TEXT NOT NULL
        )
        """
    )
    return connection


def _source_identity(sdef_path: Path) -> tuple[str, int, int]:
    stat = sdef_path.stat()
    return str(sdef_path), stat.st_mtime_ns, stat.st_size


def _cached_catalog(
    sdef_path: Path,
    cache_file: Path,
    source_mtime_ns: int,
    source_size: int,
) -> dict[str, Any] | None:
    with _connect(cache_file) as connection:
        row = connection.execute(
            f"""
            SELECT catalog_json
            FROM {_CATALOG_TABLE}
            WHERE source_path = ?
              AND source_mtime_ns = ?
              AND source_size = ?
              AND format_version = ?
            """,
            (str(sdef_path), source_mtime_ns, source_size, CACHE_FORMAT_VERSION),
        ).fetchone()
    return json.loads(row[0]) if row else None


def _store_catalog(
    sdef_path: Path,
    cache_file: Path,
    source_mtime_ns: int,
    source_size: int,
    catalog: dict[str, Any],
) -> None:
    encoded_catalog = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    with _connect(cache_file) as connection:
        # A short write transaction makes concurrent launches converge on one
        # catalog rather than exposing a partially written cache.
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            f"""
            INSERT INTO {_CATALOG_TABLE}
                (source_path, source_mtime_ns, source_size, format_version, catalog_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                source_mtime_ns = excluded.source_mtime_ns,
                source_size = excluded.source_size,
                format_version = excluded.format_version,
                catalog_json = excluded.catalog_json
            """,
            (str(sdef_path), source_mtime_ns, source_size, CACHE_FORMAT_VERSION, encoded_catalog),
        )


@lru_cache(maxsize=8)
def _load_catalog_cached(
    source_path: str,
    source_mtime_ns: int,
    source_size: int,
    cache_path: str,
) -> dict[str, Any]:
    sdef_path = Path(source_path)
    cache_file = Path(cache_path)
    try:
        catalog = _cached_catalog(sdef_path, cache_file, source_mtime_ns, source_size)
        if catalog is not None:
            return catalog
        catalog = _parse_sdef_catalog(sdef_path)
        _store_catalog(sdef_path, cache_file, source_mtime_ns, source_size, catalog)
        return catalog
    except sqlite3.Error:
        # The live agent remains usable if a cache directory is unavailable.
        return _parse_sdef_catalog(sdef_path)


def load_catalog(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Return the installed Word catalog, rebuilding only when the SDEF changes."""
    return copy.deepcopy(_load_catalog_internal(explicit_path, cache_path=cache_path))


def _load_catalog_internal(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Return the process-local catalog for internal read-only operations."""
    sdef_path = resolve_sdef_path(explicit_path)
    source_path, source_mtime_ns, source_size = _source_identity(sdef_path)
    return _load_catalog_cached(source_path, source_mtime_ns, source_size, str(_cache_path(cache_path)))


def warm_sdef_cache(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Build or validate the persistent catalog cache and return its metadata."""
    catalog = load_catalog(explicit_path, cache_path=cache_path)
    return {
        "sdef_path": catalog["sdef_path"],
        "cache_path": str(_cache_path(cache_path)),
        "command_count": catalog["command_count"],
        "class_count": catalog["class_count"],
        "enumeration_count": catalog["enumeration_count"],
    }


def clear_memory_cache() -> None:
    """Clear process-local caches; useful for tests and explicit refreshes."""
    _load_catalog_cached.cache_clear()
    load_sdef_tree.cache_clear()


def list_commands(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    return copy.deepcopy(_load_catalog_internal(explicit_path, cache_path=cache_path)["commands"])


def list_classes(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    return copy.deepcopy(_load_catalog_internal(explicit_path, cache_path=cache_path)["classes"])


def list_enumerations(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    return copy.deepcopy(_load_catalog_internal(explicit_path, cache_path=cache_path)["enumerations"])


def summarize_catalog(
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, Any]:
    return load_catalog(explicit_path, cache_path=cache_path)


def find_command(
    name: str,
    explicit_path: Optional[str] = None,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, Any] | None:
    target = name.strip().lower()
    return next(
        (
            command
            for command in list_commands(explicit_path, cache_path=cache_path)
            if command["name"].lower() == target
        ),
        None,
    )


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2]


def _score_text(text: str, tokens: list[str]) -> int:
    lowered = text.lower()
    return sum(token in lowered for token in tokens)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = item.get("name", "")
        if key and key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def search_catalog(
    query: str,
    explicit_path: Optional[str] = None,
    max_results: int = 20,
    *,
    cache_path: Optional[str | Path] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Search the persisted catalog without reparsing the Word SDEF XML."""
    tokens = _tokenize(query)
    if not tokens:
        return {"commands": [], "classes": [], "enumerations": []}

    catalog = _load_catalog_internal(explicit_path, cache_path=cache_path)
    scored_commands: list[tuple[int, dict[str, Any]]] = []
    for command in catalog["commands"]:
        haystack = " ".join(
            [
                command["name"],
                command["description"],
                " ".join(parameter["name"] for parameter in command["parameters"]),
            ]
        )
        score = _score_text(haystack, tokens)
        if score:
            if any(token in command["name"].lower() for token in tokens):
                score += 5
            scored_commands.append((score, command))

    scored_classes: list[tuple[int, dict[str, Any]]] = []
    for class_item in catalog["classes"]:
        haystack = " ".join(
            [
                class_item["name"],
                class_item["description"],
                " ".join(property_item["name"] for property_item in class_item["properties"]),
                " ".join(class_item["elements"]),
            ]
        )
        score = _score_text(haystack, tokens)
        if score:
            if any(token in class_item["name"].lower() for token in tokens):
                score += 5
            scored_classes.append((score, class_item))

    scored_enumerations: list[tuple[int, dict[str, Any]]] = []
    for enumeration in catalog["enumerations"]:
        haystack = " ".join(
            [enumeration["name"], " ".join(item["name"] for item in enumeration["enumerators"])]
        )
        score = _score_text(haystack, tokens)
        if score:
            if any(token in enumeration["name"].lower() for token in tokens):
                score += 5
            scored_enumerations.append((score, enumeration))

    scored_commands.sort(key=lambda item: (-item[0], item[1]["name"]))
    scored_classes.sort(key=lambda item: (-item[0], item[1]["name"]))
    scored_enumerations.sort(key=lambda item: (-item[0], item[1]["name"]))
    return {
        "commands": _dedupe([item[1] for item in scored_commands])[:max_results],
        "classes": _dedupe([item[1] for item in scored_classes])[:max_results],
        "enumerations": _dedupe([item[1] for item in scored_enumerations])[:max_results],
    }
