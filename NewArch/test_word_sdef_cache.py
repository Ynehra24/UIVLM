"""Regression tests for the persistent Word SDEF catalog cache."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import word_sdef


SDEF_TEMPLATE = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<dictionary xmlns=\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
  <suite name=\"Word\" code=\"WdWr\">
    <command name=\"{command}\" code=\"{code}\" description=\"{description}\">
      <parameter name=\"target\" code=\"data\" type=\"text\"/>
    </command>
    <class name=\"document\" code=\"docu\" plural=\"documents\">
      <property name=\"title\" code=\"ptit\" type=\"text\" access=\"rw\"/>
    </class>
    <enumeration name=\"color\" code=\"colr\"><enumerator name=\"blue\" code=\"blue\"/></enumeration>
  </suite>
</dictionary>
"""


class WordSdefCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sdef_path = self.root / "Word.sdef"
        self.cache_path = self.root / "sdef.sqlite3"
        self._write_sdef("insert text", "inst", "Insert document text")
        word_sdef.clear_memory_cache()

    def tearDown(self) -> None:
        word_sdef.clear_memory_cache()
        self.temp_dir.cleanup()

    def _write_sdef(self, command: str, code: str, description: str) -> None:
        self.sdef_path.write_text(
            SDEF_TEMPLATE.format(command=command, code=code, description=description), encoding="utf-8"
        )

    def test_second_process_style_load_uses_sqlite_catalog(self) -> None:
        first = word_sdef.load_catalog(str(self.sdef_path), cache_path=self.cache_path)
        self.assertEqual(first["command_names"], ["insert text"])
        self.assertTrue(self.cache_path.exists())

        word_sdef.clear_memory_cache()
        with patch("NewArch.word_sdef._parse_sdef_catalog", side_effect=AssertionError("XML should not be reparsed")):
            cached = word_sdef.load_catalog(str(self.sdef_path), cache_path=self.cache_path)
        self.assertEqual(cached["command_names"], ["insert text"])

    def test_cache_rebuilds_when_word_dictionary_changes(self) -> None:
        word_sdef.load_catalog(str(self.sdef_path), cache_path=self.cache_path)
        self._write_sdef("replace text with formatting", "repl", "Replace text and formatting")
        word_sdef.clear_memory_cache()

        rebuilt = word_sdef.load_catalog(str(self.sdef_path), cache_path=self.cache_path)
        self.assertEqual(rebuilt["command_names"], ["replace text with formatting"])
        results = word_sdef.search_catalog("formatting", str(self.sdef_path), cache_path=self.cache_path)
        self.assertEqual([item["name"] for item in results["commands"]], ["replace text with formatting"])


if __name__ == "__main__":
    unittest.main()
