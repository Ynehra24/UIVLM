"""Command-line entry point for ScriptAgent (SDEF Knowledge-Graph architecture)."""
from __future__ import annotations

import argparse
import json
import sys

from .config import LOGGER
from .agent import run_task
from .word_manager import manage_word_documents


def _cmd_graph_info() -> int:
    """Print knowledge graph statistics and SDEF cache staleness status."""
    from .sdef_graph import get_graph, _resolve_sdef_identity
    from pathlib import Path

    path, mtime_ns, size_bytes = _resolve_sdef_identity()
    print(f"\n SDEF Knowledge Graph Info")
    print(f" {'─' * 40}")
    if path == "unknown":
        print(" ⚠️  Microsoft Word.sdef not found — is Word installed?")
        return 1

    print(f" SDEF file  : {path}")
    print(f" mtime_ns   : {mtime_ns}")
    print(f" size_bytes : {size_bytes:,}")

    graph = get_graph()
    stats = graph.stats()
    print(f"\n Graph shape:")
    for k, v in stats.items():
        print(f"   {k:<25}: {v:,}")
    print()
    return 0


def _cmd_rebuild_graph() -> int:
    """Force-rebuild the SDEF knowledge graph and SDEF catalog cache."""
    from .sdef_graph import rebuild_graph
    print("\n Rebuilding SDEF knowledge graph (clears catalog cache)...")
    graph = rebuild_graph()
    stats = graph.stats()
    print(f" Done. nodes={stats['total_nodes']:,}  edges={stats['total_edges']:,}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ScriptAgent — SDEF Knowledge-Graph-grounded Microsoft Word automation"
    )
    parser.add_argument(
        "task", nargs="*",
        help="Natural-language task to perform in Word (omit to use --graph-info or --rebuild-graph)",
    )
    parser.add_argument("--retries", type=int, default=3, help="Max retry attempts on errors")
    parser.add_argument(
        "--graph-info", action="store_true",
        help="Print knowledge graph statistics and SDEF cache status, then exit",
    )
    parser.add_argument(
        "--rebuild-graph", action="store_true",
        help="Force-rebuild the SDEF knowledge graph and catalog cache, then exit",
    )
    args = parser.parse_args(argv)

    # Utility modes (no Word interaction needed)
    if args.graph_info:
        return _cmd_graph_info()
    if args.rebuild_graph:
        return _cmd_rebuild_graph()

    task = " ".join(args.task).strip()
    if not task:
        parser.error("Provide a task, or use --graph-info / --rebuild-graph.")

    print()
    if not manage_word_documents():
        print("No document selected. Exiting.")
        return 1

    result = run_task(task, max_retries=args.retries)

    if result["success"]:
        print(f"\n✅ Task completed successfully (attempt {result['attempts']})")
        if result.get("stdout"):
            print(f"Output: {result['stdout']}")
        return 0

    print(f"\n❌ Task failed after {result.get('attempts', '?')} attempts")
    print(f"Error: {result.get('error', 'unknown')}")
    LOGGER.error("Task failed: %s | %s", task, result.get("error"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
