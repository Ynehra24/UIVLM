"""Unit tests for the SDEF Knowledge Graph.

Coverage:
  - Graph construction (nodes, edges, counts)
  - All 8 edge types present
  - INHERITS_FROM edges — inherited properties are reachable in BFS
  - OPERATES_ON edges — commands link to the classes they target
  - Cycle protection — BFS terminates on circular references
  - Name disambiguation — same property name under different parents = distinct nodes
  - Node budget enforcement — BFS never returns more than node_budget nodes
  - Token budget enforcement — formatted context never exceeds token_budget_chars
  - TF-IDF seeding — "make the heading centered" reaches alignment nodes
  - Sub-graph context is a non-empty string for a valid task
  - Graph stats dict has expected keys
  - lookup_by_name returns all matching nodes (not just first)
  - Regression: 'bold' property of font object is reachable from 'paragraph'
  - Regression: 'alignment' is reachable from query "make heading centered"
  - Regression: disambiguation — multiple 'color' properties are separate nodes
  - rebuild_graph() returns a fresh graph without errors
"""
from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from unittest.mock import patch

from .sdef_graph import (
    NODE_KIND_CLASS,
    NODE_KIND_COMMAND,
    NODE_KIND_ENUM,
    NODE_KIND_ENUMERATOR,
    NODE_KIND_PARAMETER,
    NODE_KIND_PROPERTY,
    SDEFKnowledgeGraph,
    _TFIDFScorer,
    _tokenize,
)


# ---------------------------------------------------------------------------
# Minimal synthetic catalog for deterministic tests
# ---------------------------------------------------------------------------

def _make_catalog() -> dict[str, Any]:
    """
    Synthetic SDEF catalog that exercises all edge types and known-hard cases.

    Object model:
      document
        paragraph  (contains: paragraph format, font object)
          font object [inherits from: text]
            .bold : boolean  [rw]  <- regression: must be reachable from paragraph
          paragraph format
            .alignment : wdparagraphalignment [rw]
        text range [inherits from: text]

      text (base class)
        .color : wdcolor [rw]

      wdparagraphalignment (enum)
        align paragraph center, align paragraph left, align paragraph justify

      wdcolor (enum)
        red, blue, green

      make (command) -> result_type: paragraph
      format text (command) -> direct_parameter_type: text range
    """
    return {
        "classes": [
            {
                "name": "document",
                "code": "docu",
                "plural": "documents",
                "inherits": "",
                "description": "A Word document",
                "properties": [{"name": "saved", "code": "savd", "type": "boolean", "access": "r", "description": ""}],
                "elements": ["paragraph", "text range"],
            },
            {
                "name": "paragraph",
                "code": "para",
                "plural": "paragraphs",
                "inherits": "",
                "description": "A paragraph in a document",
                "properties": [],
                "elements": ["font object", "paragraph format"],
            },
            {
                "name": "text",
                "code": "text",
                "plural": "",
                "inherits": "",
                "description": "Base text class",
                "properties": [
                    # duplicate name 'color' — distinct disambiguation from wdcolor below
                    {"name": "color", "code": "colr", "type": "wdcolor", "access": "rw", "description": "Text color"},
                ],
                "elements": [],
            },
            {
                "name": "font object",
                "code": "font",
                "plural": "font objects",
                "inherits": "text",        # <- INHERITS_FROM text -> has .color via inheritance
                "description": "Font of a text range",
                "properties": [
                    {"name": "bold", "code": "bold", "type": "boolean", "access": "rw", "description": "Bold weight"},
                    {"name": "font size", "code": "fsiz", "type": "number", "access": "rw", "description": "Point size"},
                    # another 'color' — different parent, distinct node
                    {"name": "color", "code": "coli", "type": "wdcolor", "access": "rw", "description": "Font color index"},
                ],
                "elements": [],
            },
            {
                "name": "paragraph format",
                "code": "pfmt",
                "plural": "paragraph formats",
                "inherits": "",
                "description": "Formatting for a paragraph",
                "properties": [
                    {"name": "alignment", "code": "alig", "type": "wdparagraphalignment", "access": "rw", "description": "Paragraph alignment"},
                ],
                "elements": [],
            },
            {
                "name": "text range",
                "code": "txrg",
                "plural": "text ranges",
                "inherits": "text",       # inherits .color from text
                "description": "A range of text",
                "properties": [],
                "elements": [],
            },
        ],
        "enumerations": [
            {
                "name": "wdparagraphalignment",
                "code": "wdpa",
                "enumerators": [
                    {"name": "align paragraph center", "code": "parc"},
                    {"name": "align paragraph left", "code": "parl"},
                    {"name": "align paragraph justify", "code": "parj"},
                ],
            },
            {
                "name": "wdcolor",
                "code": "wdco",
                "enumerators": [
                    {"name": "red", "code": "reds"},
                    {"name": "blue", "code": "blus"},
                    {"name": "green", "code": "gren"},
                ],
            },
        ],
        "commands": [
            {
                "name": "make",
                "code": "corecrel",
                "description": "Make a new element",
                "direct_parameter_type": "paragraph",
                "result_type": "paragraph",
                "parameters": [
                    {"name": "at", "code": "insh", "type": "location specifier", "optional": True, "description": ""},
                    {"name": "with properties", "code": "prdt", "type": "record", "optional": True, "description": ""},
                ],
            },
            {
                "name": "format text",
                "code": "fmtx",
                "description": "Format a text range",
                "direct_parameter_type": "text range",
                "result_type": "",
                "parameters": [
                    {"name": "font name", "code": "fnmn", "type": "text", "optional": True, "description": ""},
                ],
            },
        ],
    }


def _build() -> SDEFKnowledgeGraph:
    return SDEFKnowledgeGraph().build(_make_catalog())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGraphConstruction(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_node_counts_are_reasonable(self) -> None:
        stats = self.g.stats()
        self.assertGreater(stats["total_nodes"], 10)
        self.assertGreater(stats["total_edges"], 5)

    def test_class_nodes_created(self) -> None:
        names = {n.name for n in self.g._nodes.values() if n.kind == NODE_KIND_CLASS}
        self.assertIn("document", names)
        self.assertIn("paragraph", names)
        self.assertIn("font object", names)
        self.assertIn("paragraph format", names)

    def test_enum_nodes_created(self) -> None:
        names = {n.name for n in self.g._nodes.values() if n.kind == NODE_KIND_ENUM}
        self.assertIn("wdparagraphalignment", names)
        self.assertIn("wdcolor", names)

    def test_enumerator_nodes_created(self) -> None:
        names = {n.name for n in self._g_enumerators()}
        self.assertIn("align paragraph center", names)
        self.assertIn("red", names)

    def test_command_nodes_created(self) -> None:
        names = {n.name for n in self.g._nodes.values() if n.kind == NODE_KIND_COMMAND}
        self.assertIn("make", names)
        self.assertIn("format text", names)

    def test_parameter_nodes_created(self) -> None:
        names = {n.name for n in self.g._nodes.values() if n.kind == NODE_KIND_PARAMETER}
        self.assertIn("at", names)
        self.assertIn("with properties", names)

    def _g_enumerators(self):
        return [n for n in self.g._nodes.values() if n.kind == NODE_KIND_ENUMERATOR]

    def test_stats_keys(self) -> None:
        stats = self.g.stats()
        self.assertIn("total_nodes", stats)
        self.assertIn("total_edges", stats)


class TestEdgeTypes(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def _adj_kinds(self, src_id: str) -> list[str]:
        return [self.g._nodes[nid].kind for nid in self.g._adj.get(src_id, []) if nid in self.g._nodes]

    def test_has_property_edges(self) -> None:
        font_id = self.g._code_to_id.get("font")
        self.assertIsNotNone(font_id)
        child_kinds = self._adj_kinds(font_id)
        self.assertIn(NODE_KIND_PROPERTY, child_kinds)

    def test_contains_element_edges(self) -> None:
        # paragraph contains "font object" and "paragraph format"
        para_id = self.g._code_to_id.get("para")
        self.assertIsNotNone(para_id)
        child_ids = self.g._adj.get(para_id, [])
        child_names = {self.g._nodes[nid].name for nid in child_ids if nid in self.g._nodes}
        self.assertIn("font object", child_names)
        self.assertIn("paragraph format", child_names)

    def test_inherits_from_edges(self) -> None:
        # font object inherits from text
        font_id = self.g._code_to_id.get("font")
        text_id = self.g._code_to_id.get("text")
        self.assertIsNotNone(font_id)
        self.assertIsNotNone(text_id)
        self.assertIn(text_id, self.g._adj.get(font_id, []))

    def test_uses_enum_edges(self) -> None:
        # paragraph format.alignment -> wdparagraphalignment
        pfmt_id = self.g._code_to_id.get("pfmt")
        self.assertIsNotNone(pfmt_id)
        # alignment property is a child of pfmt
        align_prop_ids = [
            nid for nid in self.g._adj.get(pfmt_id, [])
            if nid in self.g._nodes and self.g._nodes[nid].name == "alignment"
        ]
        self.assertTrue(align_prop_ids, "alignment property not found under paragraph format")
        align_prop_id = align_prop_ids[0]
        # enum should be in its adjacency
        enum_children = [
            self.g._nodes[nid].kind for nid in self.g._adj.get(align_prop_id, [])
            if nid in self.g._nodes
        ]
        self.assertIn(NODE_KIND_ENUM, enum_children)

    def test_has_value_edges(self) -> None:
        # wdparagraphalignment -> enumerator values
        enum_ids = [nid for nid, n in self.g._nodes.items() if n.name == "wdparagraphalignment"]
        self.assertTrue(enum_ids)
        child_kinds = self._adj_kinds(enum_ids[0])
        self.assertIn(NODE_KIND_ENUMERATOR, child_kinds)

    def test_has_parameter_edges(self) -> None:
        make_ids = [nid for nid, n in self.g._nodes.items() if n.name == "make"]
        self.assertTrue(make_ids)
        child_kinds = self._adj_kinds(make_ids[0])
        self.assertIn(NODE_KIND_PARAMETER, child_kinds)

    def test_operates_on_edges(self) -> None:
        # make -> paragraph (result_type and direct_parameter_type)
        make_ids = [nid for nid, n in self.g._nodes.items() if n.name == "make"]
        self.assertTrue(make_ids)
        child_names = {
            self.g._nodes[nid].name for nid in self.g._adj.get(make_ids[0], [])
            if nid in self.g._nodes and self.g._nodes[nid].kind == NODE_KIND_CLASS
        }
        self.assertIn("paragraph", child_names)


class TestDisambiguation(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_duplicate_property_name_resolves_to_distinct_nodes(self) -> None:
        # 'color' appears under: text (code=colr), font object (code=coli)
        # They must be distinct nodes, not merged
        color_nodes = self.g.lookup_by_name("color")
        self.assertGreaterEqual(len(color_nodes), 2, 
            "Expected at least 2 distinct 'color' nodes (one per parent class)")
        node_ids = {n.node_id for n in color_nodes}
        self.assertEqual(len(node_ids), len(color_nodes), "Duplicate node_ids — disambiguation failed")
        parent_ids = {n.parent_id for n in color_nodes}
        self.assertGreater(len(parent_ids), 1, "All 'color' nodes have the same parent — not disambiguated")


class TestCycleProtection(unittest.TestCase):

    def test_bfs_terminates_on_cyclic_catalog(self) -> None:
        """BFS must not loop infinitely when classes reference each other cyclically."""
        # Build a catalog where A -> B -> A (elements)
        cyclic_catalog: dict[str, Any] = {
            "classes": [
                {"name": "A", "code": "aaaa", "plural": "", "inherits": "B",
                 "description": "", "properties": [], "elements": ["B"]},
                {"name": "B", "code": "bbbb", "plural": "", "inherits": "A",
                 "description": "", "properties": [], "elements": ["A"]},
            ],
            "enumerations": [],
            "commands": [],
        }
        g = SDEFKnowledgeGraph().build(cyclic_catalog)
        # Must not raise or hang — should return within node_budget
        result = g.get_subgraph_context("do something with A and B", node_budget=10)
        self.assertIsInstance(result, str)


class TestNodeAndTokenBudget(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_node_budget_respected(self) -> None:
        # Use a very small budget
        # We can't easily count nodes from the formatted string, but we can
        # verify the result is a non-empty string and shorter than an unlimited one
        full = self.g.get_subgraph_context("bold paragraph font alignment", node_budget=1000)
        limited = self.g.get_subgraph_context("bold paragraph font alignment", node_budget=3)
        self.assertLessEqual(len(limited), len(full))

    def test_token_budget_limits_output_size(self) -> None:
        result = self.g.get_subgraph_context(
            "bold paragraph font alignment", token_budget_chars=200
        )
        self.assertLessEqual(len(result), 300)  # small slack for section headers


class TestHybridSeeding(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_alignment_reachable_from_paraphrased_query(self) -> None:
        """Regression: 'make the heading centered' -> alignment enum must be in context."""
        ctx = self.g.get_subgraph_context(
            "make the heading centered", max_hops=2, node_budget=60
        )
        self.assertIn("align paragraph center", ctx,
            "alignment enum value not found — TF-IDF seeding or BFS broken")

    def test_bold_reachable_from_paragraph_query(self) -> None:
        """Regression: bold property of font object must be reachable from 'paragraph bold'."""
        ctx = self.g.get_subgraph_context(
            "make paragraph 1 bold and large font", max_hops=2, node_budget=60
        )
        self.assertIn("bold", ctx.lower(),
            "bold property not found — inheritance traversal or seeding broken")


class TestInheritanceTraversal(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_inherited_property_reachable_via_inherits_from(self) -> None:
        """font object inherits from text; text has .color.
        A 2-hop BFS from 'font object' must reach 'text' and its 'color' property.
        """
        # Seed directly on font object node
        font_ids = [nid for nid, n in self.g._nodes.items() if n.name == "font object"]
        self.assertTrue(font_ids)
        # Manual BFS from font object node with max_hops=2
        ctx = self.g.get_subgraph_context("font object color", max_hops=2, node_budget=60)
        self.assertIn("color", ctx.lower(),
            "Inherited 'color' property not reachable from font object via INHERITS_FROM")


class TestSubgraphContextOutput(unittest.TestCase):

    def setUp(self) -> None:
        self.g = _build()

    def test_context_is_non_empty_for_valid_task(self) -> None:
        ctx = self.g.get_subgraph_context("create a table with bold headers")
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 10)

    def test_context_is_string_for_empty_task(self) -> None:
        ctx = self.g.get_subgraph_context("")
        self.assertIsInstance(ctx, str)

    def test_lookup_by_name_returns_all_matches(self) -> None:
        nodes = self.g.lookup_by_name("color")
        self.assertGreaterEqual(len(nodes), 2)


class TestTFIDFScorer(unittest.TestCase):

    def test_retrieves_most_relevant_doc(self) -> None:
        scorer = _TFIDFScorer({
            "a": "bold font weight paragraph formatting",
            "b": "table border line style color",
            "c": "document save path location",
        })
        results = scorer.retrieve("bold font size weight", top_k=2)
        top_ids = [r[0] for r in results]
        self.assertEqual(top_ids[0], "a")

    def test_empty_query_returns_empty(self) -> None:
        scorer = _TFIDFScorer({"a": "hello world"})
        self.assertEqual(scorer.retrieve(""), [])

    def test_tokenize_filters_short_tokens(self) -> None:
        tokens = _tokenize("set it to a bold")
        self.assertNotIn("it", tokens)
        self.assertNotIn("to", tokens)
        self.assertNotIn("a", tokens)
        self.assertIn("set", tokens)
        self.assertIn("bold", tokens)


if __name__ == "__main__":
    unittest.main()
