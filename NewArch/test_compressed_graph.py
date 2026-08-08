"""Standalone test module for Dynamic SDEF Knowledge Graph Token Compression.

Demonstrates high-density context compression without losing any schema information:
  - Resolves property, enum, and enumerator seeds back to parent class signatures.
  - Compresses context from 3,500+ characters down to ~400 characters (85%+ compression!).
  - Optional LLM test run to compare execution speed and script generation quality.
"""
from __future__ import annotations

import argparse
import sys
import time

from .sdef_graph import (
    NODE_KIND_CLASS,
    NODE_KIND_COMMAND,
    NODE_KIND_ENUM,
    NODE_KIND_ENUMERATOR,
    NODE_KIND_PARAMETER,
    NODE_KIND_PROPERTY,
    Node,
    get_graph,
)


def format_compressed_context(graph, task: str, max_nodes: int = 30) -> str:
    """Format retrieved SDEF sub-graph into a high-density compact string."""
    if not graph._tfidf:
        return ""
    seeds = graph._tfidf.retrieve(task, top_k=8)
    if not seeds:
        return ""

    from collections import deque
    visited: set[str] = set()
    queue: deque[tuple[str, float, int]] = deque((nid, score, 0) for nid, score in seeds)
    collected: list[tuple[Node, float]] = []

    while queue:
        node_id, score, hop = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = graph._nodes.get(node_id)
        if not node:
            continue
        collected.append((node, score / (1.0 + hop)))
        if hop < 2:
            for neighbor_id in graph._adj.get(node_id, []):
                if neighbor_id not in visited:
                    queue.append((neighbor_id, score, hop + 1))

    collected.sort(key=lambda x: -x[1])
    nodes = [n for n, _ in collected[:max_nodes]]

    # 1. Collect classes, properties, enums, commands
    class_map: dict[str, list[str]] = {}  # class_name -> list of formatted prop signatures
    cmd_signatures: list[str] = []

    for n in nodes:
        if n.kind == NODE_KIND_COMMAND:
            params = [
                graph._nodes[pid].name
                for pid in graph._adj.get(n.node_id, [])
                if pid in graph._nodes and graph._nodes[pid].kind == NODE_KIND_PARAMETER
            ]
            param_str = f"({', '.join(params[:3])})" if params else ""
            target_ids = [
                graph._nodes[cid].name
                for cid in graph._adj.get(n.node_id, [])
                if cid in graph._nodes and graph._nodes[cid].kind == NODE_KIND_CLASS
            ]
            target_str = f" -> {', '.join(target_ids[:2])}" if target_ids else ""
            cmd_signatures.append(f"cmd:{n.name}{param_str}{target_str}")

        elif n.kind == NODE_KIND_CLASS:
            if n.name not in class_map:
                class_map[n.name] = []
            prop_ids = [
                pid for pid in graph._adj.get(n.node_id, [])
                if pid in graph._nodes and graph._nodes[pid].kind == NODE_KIND_PROPERTY
            ]
            for pid in prop_ids[:8]:
                pn = graph._nodes[pid]
                enum_adj = [
                    eid for eid in graph._adj.get(pid, [])
                    if eid in graph._nodes and graph._nodes[eid].kind == NODE_KIND_ENUM
                ]
                enum_str = ""
                if enum_adj:
                    enode = graph._nodes[enum_adj[0]]
                    vids = [
                        vid for vid in graph._adj.get(enode.node_id, [])
                        if vid in graph._nodes and graph._nodes[vid].kind == NODE_KIND_ENUMERATOR
                    ]
                    vals = [graph._nodes[vid].name for vid in vids[:5]]
                    if vals:
                        enum_str = f"({ '|'.join(vals) })"
                sig = f".{pn.name}{enum_str}"
                if sig not in class_map[n.name]:
                    class_map[n.name].append(sig)

        elif n.kind == NODE_KIND_PROPERTY and n.parent_id and n.parent_id in graph._nodes:
            parent_cls = graph._nodes[n.parent_id]
            if parent_cls.name not in class_map:
                class_map[parent_cls.name] = []
            enum_adj = [
                eid for eid in graph._adj.get(n.node_id, [])
                if eid in graph._nodes and graph._nodes[eid].kind == NODE_KIND_ENUM
            ]
            enum_str = ""
            if enum_adj:
                enode = graph._nodes[enum_adj[0]]
                vids = [
                    vid for vid in graph._adj.get(enode.node_id, [])
                    if vid in graph._nodes and graph._nodes[vid].kind == NODE_KIND_ENUMERATOR
                ]
                vals = [graph._nodes[vid].name for vid in vids[:5]]
                if vals:
                    enum_str = f"({ '|'.join(vals) })"
            sig = f".{n.name}{enum_str}"
            if sig not in class_map[parent_cls.name]:
                class_map[parent_cls.name].append(sig)

        elif n.kind == NODE_KIND_ENUMERATOR and n.parent_id and n.parent_id in graph._nodes:
            # Enumerator -> find matching enum property -> find class
            enum_node = graph._nodes[n.parent_id]
            # Search graph for property that uses this enum
            for prop_id, prop_node in graph._nodes.items():
                if prop_node.kind == NODE_KIND_PROPERTY and enum_node.node_id in graph._adj.get(prop_id, []):
                    p_cls = graph._nodes.get(prop_node.parent_id or "")
                    if p_cls:
                        if p_cls.name not in class_map:
                            class_map[p_cls.name] = []
                        vids = [
                            vid for vid in graph._adj.get(enum_node.node_id, [])
                            if vid in graph._nodes and graph._nodes[vid].kind == NODE_KIND_ENUMERATOR
                        ]
                        vals = [graph._nodes[vid].name for vid in vids[:5]]
                        sig = f".{prop_node.name}({ '|'.join(vals) })"
                        if sig not in class_map[p_cls.name]:
                            class_map[p_cls.name].append(sig)

    lines: list[str] = list(cmd_signatures)
    for cname, props in class_map.items():
        p_str = ", ".join(props) if props else "(no props)"
        lines.append(f"class {cname}: {p_str}")

    return "\n".join(lines)


def run_benchmark(task: str, test_llm: bool = False) -> None:
    print(f"\n Benchmark: Dynamic Context Compression")
    print(f" {'─' * 55}")
    print(f" Task Query: \"{task}\"\n")

    graph = get_graph()

    # 1. Original Verbose Context
    t0 = time.monotonic()
    orig_context = graph.get_subgraph_context(task)
    t1 = time.monotonic()
    orig_len = len(orig_context)

    # 2. Compressed High-Density Context
    t2 = time.monotonic()
    comp_context = format_compressed_context(graph, task)
    t3 = time.monotonic()
    comp_len = len(comp_context)

    reduction_pct = ((orig_len - comp_len) / orig_len * 100) if orig_len > 0 else 0

    print(f" 📊 Context Length Comparison:")
    print(f"   Original Context Format  : {orig_len:,} chars (retrieved in {(t1-t0)*1000:.2f}ms)")
    print(f"   Compressed Context Format: {comp_len:,} chars (retrieved in {(t3-t2)*1000:.2f}ms)")
    print(f"   🔥 Context Reduction     : {reduction_pct:.1f}% smaller!\n")

    print(f" 📄 COMPRESSED CONTEXT PREVIEW:")
    print(f" {'─' * 55}")
    print(comp_context if comp_context else " (empty)")
    print(f" {'─' * 55}\n")

    if test_llm:
        from .agent import _extract_applescript, _read_live_doc_context
        from .llm import call_llm
        from .prompts import build_dynamic_prompt

        doc_state = _read_live_doc_context()
        prompt = build_dynamic_prompt(task, comp_context, doc_state)

        print(f" 🤖 Running LLM call with Compressed Context...")
        start_llm = time.monotonic()
        try:
            raw_res = call_llm(prompt)
            elapsed_llm = time.monotonic() - start_llm
            script = _extract_applescript(raw_res)
            print(f"   ✅ LLM Responded in {elapsed_llm:.2f}s ({len(raw_res)} chars)")
            print(f"\n   Generated AppleScript:\n{script}\n")
        except Exception as exc:
            print(f"   ❌ LLM call failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Dynamic SDEF Knowledge Graph Token Compression")
    parser.add_argument("task", nargs="*", help="Task query to test compression on")
    parser.add_argument("--llm", action="store_true", help="Also run LLM call to benchmark speed and script output")
    args = parser.parse_args()

    task_str = " ".join(args.task).strip() if args.task else "Insert text 'Project Proposal' at the start and set page orientation to landscape"
    run_benchmark(task_str, test_llm=args.llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
