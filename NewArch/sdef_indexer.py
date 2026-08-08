"""Backward-compatible entry points for NewArch's SDEF catalog cache.

The old implementation created SQLite tables but the live search path continued
to parse Word.sdef.  ``word_sdef`` now owns one complete, self-invalidating
catalog cache; these functions keep existing callers working against it.
"""
from __future__ import annotations

from typing import Any, Optional

from .config import SDEF_DB_PATH
from .word_sdef import (
    list_classes,
    list_commands,
    search_catalog,
    warm_sdef_cache,
)


def init_cache_db(db_path: str) -> dict[str, Any]:
    return warm_sdef_cache(cache_path=db_path)


def build_cache(db_path: str, sdef_path: Optional[str] = None) -> dict[str, Any]:
    return warm_sdef_cache(sdef_path, cache_path=db_path)


def ensure_cache(db_path: str = SDEF_DB_PATH) -> dict[str, Any]:
    return warm_sdef_cache(cache_path=db_path)


def get_cached_commands(db_path: str, limit: int = 100) -> list[dict[str, Any]]:
    return list_commands(cache_path=db_path)[:limit]


def get_cached_classes(db_path: str, limit: int = 100) -> list[dict[str, Any]]:
    return list_classes(cache_path=db_path)[:limit]


def search_cached(db_path: str, query: str, max_results: int = 20) -> dict[str, list[dict[str, Any]]]:
    return search_catalog(query, max_results=max_results, cache_path=db_path)


if __name__ == "__main__":
    print(ensure_cache())
