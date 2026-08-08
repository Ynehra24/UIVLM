"""SDEF Knowledge Graph — relational graph over Word's AppleScript dictionary.

Architecture:
    1. Load catalog from word_sdef (SQLite-cached, auto-invalidated on SDEF changes).
    2. Build directed graph with node kinds:
         CLASS, PROPERTY, ENUM, ENUMERATOR, COMMAND, PARAMETER
       and 8 typed edge kinds:
         CONTAINS_ELEMENT  — class contains elements of another class
         INHERITS_FROM     — class inherits from parent class (propagates properties)
         HAS_PROPERTY      — class declares a named property
         HAS_TYPE          — property type resolves to another class
         USES_ENUM         — property type resolves to an enumeration
         HAS_VALUE         — enumeration contains an enumerator value
         HAS_PARAMETER     — command carries a parameter
         OPERATES_ON       — command targets/creates a class (via direct-parameter or result)
    3. Disambiguation: node IDs include kind + code + parent code, so duplicate
       property names across different parent classes stay separate nodes.
    4. Hybrid seeding: TF-IDF scorer (no external deps) ranks candidate seed nodes
       from the task query, then BFS expands structurally from those seeds.
    5. BFS uses an explicit visited-set to prevent infinite loops on SDEF cycles
       (e.g., range -> parent -> document -> range).
    6. Budget enforcement: node_budget (default 40) + token_budget (default ~1500 tok)
       — lowest-priority nodes (furthest from seed, lowest similarity) dropped first.
    7. Graph is built once per process (lru_cache keyed on SDEF identity) and
       automatically invalidated when Word updates its sdef file.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import LOGGER, WORD_SDEF_PATHS
from .word_sdef import _load_catalog_internal, clear_memory_cache

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_KIND_CLASS = "CLASS"
NODE_KIND_PROPERTY = "PROPERTY"
NODE_KIND_ENUM = "ENUM"
NODE_KIND_ENUMERATOR = "ENUMERATOR"
NODE_KIND_COMMAND = "COMMAND"
NODE_KIND_PARAMETER = "PARAMETER"

EDGE_CONTAINS_ELEMENT = "CONTAINS_ELEMENT"
EDGE_HAS_PROPERTY = "HAS_PROPERTY"
EDGE_HAS_TYPE = "HAS_TYPE"
EDGE_USES_ENUM = "USES_ENUM"
EDGE_HAS_VALUE = "HAS_VALUE"
EDGE_HAS_PARAMETER = "HAS_PARAMETER"
EDGE_INHERITS_FROM = "INHERITS_FROM"
EDGE_OPERATES_ON = "OPERATES_ON"

# ~1500 tokens x 4 chars/token
TOKEN_BUDGET_CHARS = 6_000
DEFAULT_NODE_BUDGET = 40
DEFAULT_MAX_HOPS = 2

# Dense hub-class codes — limit inline property expansion to prevent context explosion
_DENSE_CLASS_CODES = frozenset({"WDrg", "WDdt", "WDst"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """A vertex in the SDEF knowledge graph."""
    node_id: str
    kind: str
    name: str
    code: str
    parent_id: str | None
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# TF-IDF scorer (zero external dependencies)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class _TFIDFScorer:
    """Lightweight TF-IDF retriever for hybrid graph seeding — no external deps."""

    def __init__(self, docs: dict[str, str]) -> None:
        self._doc_count = max(len(docs), 1)
        self._tf: dict[str, dict[str, float]] = {}
        self._df: dict[str, int] = defaultdict(int)
        for node_id, text in docs.items():
            terms = _tokenize(text)
            if not terms:
                self._tf[node_id] = {}
                continue
            raw: dict[str, int] = defaultdict(int)
            for t in terms:
                raw[t] += 1
            n = len(terms)
            self._tf[node_id] = {t: c / n for t, c in raw.items()}
            for t in raw:
                self._df[t] += 1

    def retrieve(self, query: str, top_k: int = 6) -> list[tuple[str, float]]:
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scores: dict[str, float] = defaultdict(float)
        for term in q_terms:
            if term not in self._df:
                continue
            idf = math.log((1 + self._doc_count) / (1 + self._df[term])) + 1.0
            for node_id, tf_map in self._tf.items():
                if term in tf_map:
                    scores[node_id] += tf_map[term] * idf
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:top_k]


# ---------------------------------------------------------------------------
# Knowledge Graph
# ---------------------------------------------------------------------------

class SDEFKnowledgeGraph:
    """Relational directed graph over Microsoft Word's SDEF scripting dictionary."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._adj: dict[str, list[str]] = defaultdict(list)
        self._name_index: dict[str, list[str]] = defaultdict(list)
        self._code_to_id: dict[str, str] = {}
        self._tfidf: _TFIDFScorer | None = None

    def _add_node(self, node: Node) -> None:
        self._nodes[node.node_id] = node
        self._name_index[node.name.lower()].append(node.node_id)
        if node.code and node.code not in self._code_to_id:
            self._code_to_id[node.code] = node.node_id

    @staticmethod
    def _class_id(code: str, name: str) -> str:
        return f"class:{code or _slug(name)}"

    @staticmethod
    def _prop_id(cls_code: str, prop_code: str, prop_name: str) -> str:
        return f"prop:{cls_code}:{prop_code or _slug(prop_name)}"

    @staticmethod
    def _enum_id(code: str, name: str) -> str:
        return f"enum:{code or _slug(name)}"

    @staticmethod
    def _enumerator_id(enum_code: str, val_code: str, val_name: str) -> str:
        return f"enumerator:{enum_code}:{val_code or _slug(val_name)}"

    @staticmethod
    def _cmd_id(code: str, name: str) -> str:
        return f"cmd:{code or _slug(name)}"

    @staticmethod
    def _param_id(cmd_code: str, param_code: str, param_name: str) -> str:
        return f"param:{cmd_code}:{param_code or _slug(param_name)}"

    def build(self, catalog: dict[str, Any]) -> "SDEFKnowledgeGraph":
        """Populate the graph from a parsed SDEF catalog dict (4-pass + TF-IDF)."""
        classes_by_name: dict[str, str] = {}
        enums_by_name: dict[str, str] = {}

        # Pass 1: Class + property nodes
        for cls in catalog.get("classes", []):
            c_id = self._class_id(cls["code"], cls["name"])
            self._add_node(Node(
                node_id=c_id, kind=NODE_KIND_CLASS,
                name=cls["name"], code=cls["code"], parent_id=None,
                description=cls.get("description", ""),
                metadata={"inherits": cls.get("inherits", ""), "elements": cls.get("elements", [])},
            ))
            classes_by_name[cls["name"].lower()] = c_id

            for prop in cls.get("properties", []):
                p_id = self._prop_id(cls["code"], prop["code"], prop["name"])
                self._add_node(Node(
                    node_id=p_id, kind=NODE_KIND_PROPERTY,
                    name=prop["name"], code=prop["code"], parent_id=c_id,
                    description=prop.get("description", ""),
                    metadata={"type": prop.get("type", ""), "access": prop.get("access", "")},
                ))
                self._adj[c_id].append(p_id)

        # Pass 2: Enum + enumerator nodes
        for enum in catalog.get("enumerations", []):
            e_id = self._enum_id(enum["code"], enum["name"])
            self._add_node(Node(
                node_id=e_id, kind=NODE_KIND_ENUM,
                name=enum["name"], code=enum["code"], parent_id=None,
            ))
            enums_by_name[enum["name"].lower()] = e_id
            for ev in enum.get("enumerators", []):
                ev_id = self._enumerator_id(enum["code"], ev["code"], ev["name"])
                self._add_node(Node(
                    node_id=ev_id, kind=NODE_KIND_ENUMERATOR,
                    name=ev["name"], code=ev["code"], parent_id=e_id,
                ))
                self._adj[e_id].append(ev_id)

        # Pass 3: Command + parameter nodes
        for cmd in catalog.get("commands", []):
            cmd_id = self._cmd_id(cmd["code"], cmd["name"])
            self._add_node(Node(
                node_id=cmd_id, kind=NODE_KIND_COMMAND,
                name=cmd["name"], code=cmd["code"], parent_id=None,
                description=cmd.get("description", ""),
                metadata={
                    "direct_parameter_type": cmd.get("direct_parameter_type", ""),
                    "result_type": cmd.get("result_type", ""),
                },
            ))
            for param in cmd.get("parameters", []):
                par_id = self._param_id(cmd["code"], param["code"], param["name"])
                self._add_node(Node(
                    node_id=par_id, kind=NODE_KIND_PARAMETER,
                    name=param["name"], code=param["code"], parent_id=cmd_id,
                    description=param.get("description", ""),
                    metadata={"type": param.get("type", ""), "optional": param.get("optional", False)},
                ))
                self._adj[cmd_id].append(par_id)

        # Pass 4: Wire cross-reference edges
        for cls in catalog.get("classes", []):
            c_id = classes_by_name.get(cls["name"].lower(), "")
            if not c_id:
                continue
            # INHERITS_FROM
            parent_name = (cls.get("inherits") or "").lower()
            if parent_name and parent_name in classes_by_name:
                pid = classes_by_name[parent_name]
                if pid not in self._adj[c_id]:
                    self._adj[c_id].append(pid)
            # CONTAINS_ELEMENT
            for elem_type in cls.get("elements", []):
                tid = classes_by_name.get(elem_type.lower(), "")
                if tid and tid not in self._adj[c_id]:
                    self._adj[c_id].append(tid)
            # HAS_TYPE / USES_ENUM from property types
            for prop in cls.get("properties", []):
                p_id = self._prop_id(cls["code"], prop["code"], prop["name"])
                ptype = (prop.get("type") or "").lower()
                if ptype in enums_by_name:
                    eid = enums_by_name[ptype]
                    if eid not in self._adj[p_id]:
                        self._adj[p_id].append(eid)
                elif ptype in classes_by_name:
                    tid = classes_by_name[ptype]
                    if tid not in self._adj[p_id]:
                        self._adj[p_id].append(tid)

        # OPERATES_ON: command -> class it creates or targets
        for cmd in catalog.get("commands", []):
            cmd_id = self._cmd_id(cmd["code"], cmd["name"])
            for attr in ("direct_parameter_type", "result_type"):
                t = (cmd.get(attr) or "").lower()
                if t in classes_by_name:
                    tid = classes_by_name[t]
                    if tid not in self._adj[cmd_id]:
                        self._adj[cmd_id].append(tid)

        # Pass 5: TF-IDF index
        docs: dict[str, str] = {}
        for nid, node in self._nodes.items():
            parts = [node.name, node.description]
            for v in node.metadata.values():
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, list):
                    parts.extend(str(x) for x in v)
            docs[nid] = " ".join(filter(None, parts))
        self._tfidf = _TFIDFScorer(docs)

        LOGGER.info(
            "SDEFKnowledgeGraph built | nodes=%d adj_edges=%d",
            len(self._nodes),
            sum(len(v) for v in self._adj.values()),
        )
        return self

    # ------------------------------------------------------------------
    # Sub-graph context retrieval (the main agent-facing API)
    # ------------------------------------------------------------------

    def get_subgraph_context(
        self,
        task: str,
        max_hops: int = DEFAULT_MAX_HOPS,
        node_budget: int = DEFAULT_NODE_BUDGET,
        token_budget_chars: int = TOKEN_BUDGET_CHARS,
    ) -> str:
        """Return a formatted SDEF context string for the LLM prompt.

        TF-IDF seeding -> BFS expansion (visited-set cycle protection) ->
        node budget trim -> token budget trim.
        """
        if not self._tfidf:
            return ""
        seeds = self._tfidf.retrieve(task, top_k=6)
        if not seeds:
            return ""

        visited: set[str] = set()
        queue: deque[tuple[str, float, int]] = deque(
            (nid, score, 0) for nid, score in seeds
        )
        collected: list[tuple[Node, float]] = []

        while queue:
            node_id, score, hop = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            node = self._nodes.get(node_id)
            if not node:
                continue
            priority = score / (1.0 + hop)
            collected.append((node, priority))
            if hop < max_hops:
                for neighbor_id in self._adj.get(node_id, []):
                    if neighbor_id not in visited:
                        queue.append((neighbor_id, score, hop + 1))

        collected.sort(key=lambda x: -x[1])
        collected = collected[:node_budget]
        return self._format_context(collected, token_budget_chars)

    def _format_context(
        self, items: list[tuple[Node, float]], token_budget_chars: int
    ) -> str:
        by_kind: dict[str, list[tuple[Node, float]]] = defaultdict(list)
        for node, score in items:
            by_kind[node.kind].append((node, score))

        sections: list[str] = []
        total_chars = 0
        truncated = 0
        render_order = [
            NODE_KIND_COMMAND, NODE_KIND_CLASS, NODE_KIND_ENUM,
            NODE_KIND_PROPERTY, NODE_KIND_ENUMERATOR, NODE_KIND_PARAMETER,
        ]
        for kind in render_order:
            ns = by_kind.get(kind, [])
            if not ns:
                continue
            lines = [f"[{kind}S]"]
            for node, _ in ns:
                entry = self._format_node(node)
                if total_chars + len(entry) > token_budget_chars:
                    truncated += 1
                    continue
                lines.append(entry)
                total_chars += len(entry)
            if len(lines) > 1:
                sections.append("\n".join(lines))

        if truncated:
            LOGGER.info("Graph: %d nodes dropped for token budget", truncated)
        return "\n\n".join(sections)

    def _format_node(self, node: Node) -> str:
        if node.kind == NODE_KIND_CLASS:
            prop_ids = [
                nid for nid in self._adj.get(node.node_id, [])
                if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_PROPERTY
            ]
            limit = 8 if node.code in _DENSE_CLASS_CODES else 15
            props = []
            for pid in prop_ids[:limit]:
                pn = self._nodes[pid]
                access = pn.metadata.get("access", "")
                ptype = pn.metadata.get("type", "")
                access_tag = f" [{access}]" if access else ""
                # Inline enum values so they're always visible regardless of BFS depth.
                # This is the key fix for the "centered" -> "center" semantic gap:
                # the LLM sees enum values whenever it sees the property, even if the
                # enum node itself wasn't reached as a seed.
                enum_inline = ""
                enum_adj = [
                    nid for nid in self._adj.get(pid, [])
                    if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_ENUM
                ]
                if enum_adj:
                    enum_node = self._nodes[enum_adj[0]]
                    val_ids = [
                        nid for nid in self._adj.get(enum_node.node_id, [])
                        if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_ENUMERATOR
                    ]
                    vals = [self._nodes[vid].name for vid in val_ids[:12]]
                    if vals:
                        enum_inline = f" → values: {', '.join(vals)}"
                props.append(f"      .{pn.name} : {ptype}{access_tag}{enum_inline}")
            prop_block = "\n".join(props) if props else "      (no properties)"
            desc = f" — {node.description}" if node.description else ""
            return f"  CLASS {node.name}{desc}\n{prop_block}"


        if node.kind == NODE_KIND_COMMAND:
            param_ids = [
                nid for nid in self._adj.get(node.node_id, [])
                if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_PARAMETER
            ]
            params = []
            for pid in param_ids:
                pn = self._nodes[pid]
                opt = " (optional)" if pn.metadata.get("optional") else ""
                params.append(f"      {pn.name} : {pn.metadata.get('type','')}{opt}")
            param_block = "\n".join(params) if params else "      (no parameters)"
            ops_ids = [
                nid for nid in self._adj.get(node.node_id, [])
                if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_CLASS
            ]
            ops_str = (
                " [targets: " + ", ".join(self._nodes[nid].name for nid in ops_ids[:3]) + "]"
                if ops_ids else ""
            )
            desc = f" — {node.description}" if node.description else ""
            return f"  COMMAND {node.name}{ops_str}{desc}\n{param_block}"

        if node.kind == NODE_KIND_ENUM:
            val_ids = [
                nid for nid in self._adj.get(node.node_id, [])
                if nid in self._nodes and self._nodes[nid].kind == NODE_KIND_ENUMERATOR
            ]
            vals = [self._nodes[vid].name for vid in val_ids[:25]]
            return f"  ENUM {node.name}: {', '.join(vals)}"

        if node.kind == NODE_KIND_PROPERTY:
            ptype = node.metadata.get("type", "")
            access = node.metadata.get("access", "")
            parent = self._nodes.get(node.parent_id or "")
            parent_tag = f" (of {parent.name})" if parent else ""
            return f"  PROPERTY {node.name}{parent_tag} : {ptype}" + (f" [{access}]" if access else "")

        if node.kind == NODE_KIND_ENUMERATOR:
            parent = self._nodes.get(node.parent_id or "")
            enum_tag = f" (in {parent.name})" if parent else ""
            return f"  VALUE {node.name}{enum_tag}"

        return f"  {node.kind} {node.name}"

    # ------------------------------------------------------------------
    # Stats / maintenance
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        kind_counts: dict[str, int] = defaultdict(int)
        for node in self._nodes.values():
            kind_counts[node.kind] += 1
        return {
            "total_nodes": len(self._nodes),
            "total_edges": sum(len(v) for v in self._adj.values()),
            **{k.lower() + "_count": v for k, v in sorted(kind_counts.items())},
        }

    def lookup_by_name(self, name: str) -> list[Node]:
        """Return all nodes whose name matches (may be multiple due to disambiguation)."""
        return [
            self._nodes[nid]
            for nid in self._name_index.get(name.lower(), [])
            if nid in self._nodes
        ]


# ---------------------------------------------------------------------------
# Process-level singleton with auto-invalidation
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _build_graph_cached(sdef_path: str, mtime_ns: int, size_bytes: int) -> SDEFKnowledgeGraph:
    """Build and cache graph; cache key includes SDEF identity for auto-invalidation."""
    LOGGER.info(
        "Building SDEFKnowledgeGraph | sdef=%s mtime_ns=%d size=%d",
        sdef_path, mtime_ns, size_bytes,
    )
    catalog = _load_catalog_internal()
    return SDEFKnowledgeGraph().build(catalog)


def _resolve_sdef_identity() -> tuple[str, int, int]:
    for candidate in WORD_SDEF_PATHS:
        p = Path(candidate).expanduser()
        if p.exists():
            stat = p.stat()
            return str(p), stat.st_mtime_ns, stat.st_size
    return "unknown", 0, 0


def get_graph() -> SDEFKnowledgeGraph:
    """Return the process-local SDEF knowledge graph, building it once per process."""
    path, mtime_ns, size_bytes = _resolve_sdef_identity()
    return _build_graph_cached(path, mtime_ns, size_bytes)


def rebuild_graph() -> SDEFKnowledgeGraph:
    """Force a complete rebuild: clears the SDEF catalog cache and the graph cache."""
    clear_memory_cache()
    _build_graph_cached.cache_clear()
    LOGGER.info("Graph cache cleared — rebuilding from scratch.")
    return get_graph()
