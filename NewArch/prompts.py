"""Dynamic Prompt Rule Assembly Engine for ScriptAgent.

Assembles specific AppleScript idiom rules dynamically on-the-fly based on:
  1. The node kinds & concept types present in the retrieved SDEF sub-graph
  2. The perception state of the active Word document (empty vs existing tables/paragraphs)
  3. Error history feedback from previous attempts
"""
from __future__ import annotations

from typing import Any

# Base system rules (apply to all tasks)
BASE_RULES = [
    'Write ONLY valid AppleScript code that executes in osascript.',
    'Use ONLY commands, classes, properties, and enumerations shown in the SDEF context below.',
    'ALWAYS wrap code in: tell application "Microsoft Word" ... end tell',
    'Use "active document" to refer to the current document.',
    'CRITICAL: NEVER write "set text of paragraph..." or "set content of text object of paragraph 1" to add text at start! Paragraph 1 might be a table cell.',
    'TO INSERT TEXT AT START OF DOCUMENT (BEFORE TABLES): set startRange to create range active document start 0 end 0 -> insert text "Your Text" & linefeed at startRange',
    'TO INSERT AT END OF DOCUMENT (AFTER TABLES): set endRange to text object of active document -> collapse range endRange direction collapse end -> insert text "Your Text" at endRange',
]

# Modular rule templates dynamically injected based on graph context
IDIOM_RULES = {
    "font": [
        "TEXT CONTENT — NEVER write 'set text of paragraph' or 'set the text of paragraph' (it is invalid in Word AppleScript and silently fails!).\n"
        "     ALWAYS use: set content of text object of paragraph 1 of active document to \"Your Text\"\n"
        "     FONT ACCESS — ALWAYS use 'font object of text object of paragraph N' NOT 'font of paragraph N':\n"
        "     set f to font object of text object of paragraph 1 of active document\n"
        "     set bold of f to true\n"
        "     set font size of f to 24\n"
        "     set color index of f to blue   -- use 'color index' for font color (blue, red, green, etc.)"
    ],
    "table": [
        "TABLE BORDERS — Word creates tables with NO borders by default; always add:\n"
        "     set tbl to make new table at endRange with properties {number of rows:3, number of columns:3}\n"
        "     tell border options of tbl\n"
        "         set outside line style to line style single\n"
        "         set inside line style to line style single\n"
        "     end tell",
        "CELL TEXT — use content of text object of cell:\n"
        "     set content of text object of cell 1 of row 1 of tbl to \"Header 1\""
    ],
    "shading": [
        "TABLE SHADING (ROW/CELL BACKGROUND COLOR):\n"
        "     -- Word AppleScript shading is applied PER CELL in a loop, not on the row object directly:\n"
        "     tell row 1 of tbl\n"
        "         repeat with aCell in cells\n"
        "             set background pattern color index of shading of aCell to blue\n"
        "         end repeat\n"
        "     end tell"
    ],
    "page setup": [
        "PAGE ORIENTATION & MARGINS — MUST use 'orient landscape' or 'orient portrait' on 'page setup of section 1 of active document':\n"
        "     tell page setup of section 1 of active document\n"
        "         set orientation to orient landscape    -- MUST be 'orient landscape' or 'orient portrait'\n"
        "         set top margin to 72.0                  -- write margins as float/number 72.0 (NOT '72 points')\n"
        "         set bottom margin to 72.0\n"
        "     end tell"
    ],
    "find": [
        "FIND & REPLACE:\n"
        "     set findObj to find object of (text object of active document)\n"
        "     clear formatting findObj\n"
        "     execute find findObj find text \"Draft\" replace with \"Final Version\" replace replace all"
    ],
    "alignment": [
        "ALIGNMENT & PARAGRAPH FORMAT — ALIGNMENT is on 'paragraph format', NOT directly on paragraph:\n"
        "     set alignment of paragraph format of paragraph 1 of active document to align paragraph center\n"
        "     -- Or on last paragraph:\n"
        "     set p to paragraph (count paragraphs of active document) of active document\n"
        "     set alignment of paragraph format of p to align paragraph center"
    ],
}


def build_dynamic_prompt(
    task: str,
    sdef_context: str,
    doc_state: str,
    error_history: list[dict[str, str]] | None = None,
) -> str:
    """Dynamically assemble system prompt rules based on graph context & doc perception."""

    # 1. Base rules
    rules: list[str] = list(BASE_RULES)
    rule_counter = len(rules) + 1

    # 2. Perception rules (append/collapse range vs replace)
    lowered_task = task.lower()
    lowered_context = sdef_context.lower()

    if "table" in lowered_context or "table" in lowered_task:
        rules.append(
            f"{rule_counter}. WORD PARAGRAPH MODEL: Every cell inside a table is counted as a paragraph by Word! "
            f"Therefore, 'paragraph (count paragraphs)' targets a CELL INSIDE THE LAST TABLE, NOT the document end!"
        )
        rule_counter += 1
        rules.append(
            f"{rule_counter}. TO INSERT TEXT AT START OF DOCUMENT:\n"
            "     set startRange to text object of active document\n"
            "     collapse range startRange direction collapse start\n"
            "     insert text \"Your Text Here\" & linefeed at startRange\n"
            "     -- For inserting a new table at document end:\n"
            "     set endRange to text object of active document\n"
            "     collapse range endRange direction collapse end\n"
            "     set tbl to make new table at endRange with properties {number of rows:3, number of columns:3}"
        )
        rule_counter += 1

    # 3. Dynamic idiom rules based on concepts in task/context
    for key, idiom_list in IDIOM_RULES.items():
        if key in lowered_task or key in lowered_context:
            for idiom in idiom_list:
                rules.append(f"{rule_counter}. {idiom}")
                rule_counter += 1

    rules.append(f"{rule_counter}. Output ONLY the AppleScript code inside ```applescript ... ``` fences. No extra text.")

    # 4. Assemble system prompt header
    rules_text = "\n".join(f"{r}" for r in rules)

    prompt = (
        f"You are an AppleScript expert for Microsoft Word on macOS.\n"
        f"You will be given a user task, the LIVE STATE of the Word document, and a precise SDEF dictionary sub-graph.\n\n"
        f"RULES:\n{rules_text}\n\n"
        f"SDEF CONTEXT:\n{sdef_context}\n\n"
        f"{doc_state}\n\n"
        f"USER TASK: {task}"
    )

    # 5. Inject error history feedback if retrying (compact to prevent completion truncation)
    if error_history:
        last_entry = error_history[-1]
        err_msg = last_entry['error'][:300]
        prompt += (
            f"\n\nPREVIOUS ATTEMPT FAILED WITH ERROR:\n{err_msg}\n\n"
            "Fix this error. Make sure:\n"
            "- Code is wrapped in: tell application \"Microsoft Word\" ... end tell\n"
            "- If document starts with a table, use 'insert paragraph before text object of table 1'\n"
            "- Use 'orientation landscape' for page orientation\n"
            "Output ONLY the complete corrected AppleScript code inside ```applescript ... ```."
        )

    return prompt
