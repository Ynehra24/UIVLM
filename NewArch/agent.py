"""SDEF Knowledge-Graph-grounded AppleScript generation agent.

Pipeline:
  1. User task (plain English)
  2. Graph sub-graph retrieval: TF-IDF seed -> BFS expansion over SDEF graph
     (handles inheritance, composition, type resolution, command->class wiring)
  3. Inject precise sub-graph context into LLM prompt
  4. LLM generates AppleScript grounded in the real Word dictionary
  5. Execute via osascript
  6. Verification:
     - Explicit "missing value" detection in stdout (distinct failure, not coerced)
     - Lightweight pre/post document-state delta check for mutation tasks
     - On failure: feed stderr + verification failure back to LLM -> retry
"""
from __future__ import annotations

import re
from typing import Any

from .applescript import run_applescript_safe
from .config import LOGGER
from .llm import call_llm
from .sdef_graph import get_graph


# ---------------------------------------------------------------------------
# System prompt (canonical AppleScript syntax rules)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = '''You are an AppleScript expert for Microsoft Word on macOS.
You will be given a user task, the LIVE STATE of the Word document, and a precise SDEF dictionary sub-graph.

CRITICAL NON-OVERWRITING & INSERTION RULES:
1. NEVER overwrite existing document text or objects UNLESS the user explicitly says "replace", "overwrite", or "delete".
2. WORD PARAGRAPH MODEL: Every cell inside a table is counted as a paragraph by Word! Therefore, "paragraph (count paragraphs)" targets a CELL INSIDE THE LAST TABLE, NOT the document end!
3. TO INSERT NEW TEXT OR A NEW TABLE AT THE VERY END OF THE DOCUMENT (AFTER ALL EXISTING TABLES):
     set endRange to text object of active document
     collapse range endRange direction collapse end
     -- For inserting a new table:
     set tbl to make new table at endRange with properties {number of rows:3, number of columns:3}
     tell border options of tbl
         set outside line style to line style single
         set inside line style to line style single
     end tell
     -- For inserting text at end:
     insert text "Your text here" & linefeed at endRange

4. TO INSERT BEFORE A SPECIFIC TABLE OR AT START OF DOCUMENT:
     set startRange to create range active document start 0 end 0
     -- OR for start of document:
     set startRange to text object of active document
     collapse range startRange direction collapse start

5. FONT & FORMATTING RULES:
     set f to font object of text object of paragraph 1 of active document
     set bold of f to true
     set font size of f to 24
     set color index of f to blue   -- use 'color index' for font color (blue, red, green, etc.)

6. ALIGNMENT — use unquoted keywords:
     set alignment of paragraph format of paragraph 1 of active document to align paragraph center

7. TABLE SHADING (ROW/CELL BACKGROUND COLOR):
     -- Word AppleScript shading is applied PER CELL in a loop, not on the row object directly:
     tell row 1 of tbl
         repeat with aCell in cells
             set background pattern color index of shading of aCell to blue
         end repeat
     end tell

8. PAGE ORIENTATION & MARGINS — MUST use 'orientation landscape' or 'orientation portrait':
     tell page setup of section 1 of active document
         set orientation to orientation landscape    -- MUST be 'orientation landscape', NEVER just 'landscape'
         set top margin to 72.0                       -- points (72.0 = 1 inch)
     end tell

9. CELL TEXT:
     set content of text object of cell 1 of row 1 of tbl to "Header 1"

10. FIND & REPLACE:
     set myRange to text object of active document
     set findObj to find object of myRange
     clear formatting findObj
     set find text of findObj to "Draft"
     set content of replacement of findObj to "Approved"
     execute find findObj replace replace all

11. Output ONLY the AppleScript code inside ```applescript ... ``` fences. No extra text outside.

SDEF CONTEXT:
'''


# ---------------------------------------------------------------------------
# Live Document Perception (0.05s inspect before code generation)
# ---------------------------------------------------------------------------

_DOC_PERCEPTION_SCRIPT = '''\
tell application "Microsoft Word"
    if (count documents) = 0 then return "NO_DOC"
    tell active document
        set pCount to count paragraphs
        set tCount to count tables
        return (pCount as text) & "|" & (tCount as text)
    end tell
end tell
'''


def _read_live_doc_context() -> str:
    """Read active document state (total paragraphs including table cells, table count)."""
    stdout, _, code = run_applescript_safe(_DOC_PERCEPTION_SCRIPT, timeout=5)
    if code != 0 or stdout.strip() == "NO_DOC":
        return "LIVE DOCUMENT STATE: No document or unable to read state."

    parts = stdout.strip().split("|")
    if len(parts) >= 2:
        p_count = parts[0]
        t_count = parts[1]
        has_tables = int(t_count) > 0
        has_content = int(p_count) > 1 or has_tables

        state_str = f"LIVE DOCUMENT STATE:\n"
        state_str += f"- Existing Tables: {t_count}\n"
        state_str += f"- Total Paragraphs (including table cell paragraphs): {p_count}\n"
        if has_tables:
            state_str += (
                f"- ATTENTION: Document contains {t_count} table(s)! Word treats every cell inside a table as a paragraph. "
                "To add new text or a new table AFTER the existing tables, use: "
                "'set endRange to text object of active document' followed by 'collapse range endRange direction collapse end'. "
                "DO NOT use paragraph (count paragraphs) or target existing table cells!\n"
            )
        elif has_content:
            state_str += "- ATTENTION: Document contains existing content! Collapse range to end before inserting.\n"
        else:
            state_str += "- Document is currently empty.\n"
        return state_str

    return "LIVE DOCUMENT STATE: Active document is open."


# ---------------------------------------------------------------------------
# SDEF context (graph-backed)
# ---------------------------------------------------------------------------

def _build_sdef_context(task: str) -> str:
    """Retrieve a precise SDEF sub-graph for the task via TF-IDF -> BFS expansion."""
    try:
        graph = get_graph()
        search_query = task
        lowered = task.lower()
        if any(w in lowered for w in ("orientation", "landscape", "portrait", "margin", "page")):
            search_query += " section page setup orientation top margin"
        elif any(w in lowered for w in ("replace", "find", "substitute")):
            search_query += " find object replacement execute find"
        elif any(w in lowered for w in ("shading", "background", "color", "fill")):
            search_query += " shading cell table background pattern color index"

        context = graph.get_subgraph_context(search_query)
        LOGGER.info("Graph context retrieved | chars=%d", len(context))
        return context
    except Exception as exc:
        LOGGER.warning("Graph retrieval failed, falling back to empty context: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(
    task: str,
    sdef_context: str,
    doc_state: str,
    error_history: list[dict[str, str]] | None = None,
) -> str:
    return build_dynamic_prompt(task, sdef_context, doc_state, error_history)


from .applescript import run_applescript_safe
from .config import LOGGER
from .llm import call_llm
from .prompts import build_dynamic_prompt
from .sdef_graph import get_graph


# ---------------------------------------------------------------------------
# AppleScript extraction & auto-wrapping
# ---------------------------------------------------------------------------

def _extract_applescript(response: str) -> str:
    """Pull AppleScript code block from LLM response, auto-repairing truncation and balancing tell blocks."""
    script = ""
    match = re.search(r"```(?:applescript|osascript)?\s*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
    if match:
        script = match.group(1).strip()
    else:
        # Fallback: search for tell application ... end tell block
        match_tell = re.search(r"(tell application\s+\"Microsoft Word\".*?end tell)", response, re.DOTALL | re.IGNORECASE)
        if match_tell:
            script = match_tell.group(1).strip()
        else:
            script = response.strip()

    # Strip raw markdown fences if present
    script = re.sub(r"^```[a-z]*\n?", "", script, flags=re.IGNORECASE)
    script = re.sub(r"\n?```$", "", script).strip()

    # --- Truncation Sanitizer & Block Auto-Balancer ---
    lines = [line.rstrip() for line in script.splitlines() if line.strip()]
    if lines:
        # If last line is incomplete (ends with preposition/operator/keyword), drop it
        incomplete_endings = (
            " to", " of", " set", " tell", " with", " at", " in", "=", " forming", " properties", ","
        )
        while lines:
            last = lines[-1].lower().strip()
            if any(last.endswith(ending) for ending in incomplete_endings) or last in ("tell", "if", "set", "repeat"):
                lines.pop()
            else:
                break

    # Count unclosed tell, if, repeat blocks and append missing closures
    full_text = "\n".join(lines)
    tell_opens = len(re.findall(r"\btell\b", full_text, re.IGNORECASE))
    tell_closes = len(re.findall(r"\bend\s+tell\b", full_text, re.IGNORECASE))
    if_opens = len(re.findall(r"\bif\b", full_text, re.IGNORECASE))
    if_closes = len(re.findall(r"\bend\s+if\b", full_text, re.IGNORECASE))
    repeat_opens = len(re.findall(r"\brepeat\b", full_text, re.IGNORECASE))
    repeat_closes = len(re.findall(r"\bend\s+repeat\b", full_text, re.IGNORECASE))

    for _ in range(repeat_opens - repeat_closes):
        lines.append("    end repeat")
    for _ in range(if_opens - if_closes):
        lines.append("    end if")
    for _ in range(tell_opens - tell_closes):
        lines.append("end tell")

    sanitized = "\n".join(lines)

    # Auto-wrap in tell application "Microsoft Word" if missing
    if "tell application" not in sanitized.lower():
        sanitized = f'tell application "Microsoft Word"\n    {sanitized}\nend tell'

    return sanitized


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

_SNAPSHOT_SCRIPT = '''\
tell application "Microsoft Word"
    if (count documents) = 0 then return "0|0|0|0"
    tell active document
        set pCount to count paragraphs
        set tCount to count tables
        set wCount to word count of words
        set cCount to 0
        try
            set cCount to (count characters of text object)
        end try
        return (pCount as text) & "|" & (tCount as text) & "|" & (wCount as text) & "|" & (cCount as text)
    end tell
end tell
'''


def _get_doc_snapshot() -> dict[str, int] | None:
    """Read lightweight paragraph/table/word/char counts for pre/post delta."""
    stdout, _, code = run_applescript_safe(_SNAPSHOT_SCRIPT, timeout=10)
    if code != 0:
        return None
    parts = stdout.strip().split("|")
    if len(parts) != 4:
        return None
    try:
        return {
            "paragraphs": int(parts[0]),
            "tables": int(parts[1]),
            "words": int(parts[2]),
            "chars": int(parts[3]),
        }
    except ValueError:
        return None


# Mutation indicators — used to decide whether to run a pre/post delta check
_MUTATION_HINTS = frozenset({
    "insert", "add", "write", "create", "make", "set", "replace", "delete",
    "remove", "format", "bold", "italic", "align", "center", "justify",
    "table", "heading", "font", "size", "color", "border", "margin",
    "save", "orientation", "landscape", "portrait",
})

# Non-deterministic document fields — excluded from delta comparison
_DELTA_IGNORE_FIELDS = frozenset({"saved", "name", "path"})


def _is_mutation_task(task: str) -> bool:
    lowered = task.lower()
    return any(h in lowered for h in _MUTATION_HINTS)


def _snapshot_changed(before: dict[str, int], after: dict[str, int]) -> bool:
    return any(before.get(k) != after.get(k) for k in before if k not in _DELTA_IGNORE_FIELDS)


def _contains_missing_value(stdout: str, stderr: str) -> bool:
    """Detect the 'missing value' failure mode — osascript exits 0 but property is unset."""
    combined = (stdout + stderr).lower()
    return "missing value" in combined


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

def run_task(task: str, max_retries: int = 3) -> dict[str, Any]:
    """
    Run a user task against Microsoft Word using Knowledge-Graph-grounded generation.

    Returns dict with: success, script, stdout, stderr, attempts, [error]
    """
    LOGGER.info("=" * 60)
    LOGGER.info("ScriptAgent | task: %s", task)

    # 1. Graph-backed SDEF context
    sdef_context = _build_sdef_context(task)

    # 2. Perception: Read live document state before generating code
    doc_state = _read_live_doc_context()
    LOGGER.info("Doc perception | %s", doc_state.replace('\n', ' | '))

    # 3. Pre-execution snapshot (only for mutation tasks)
    is_mutation = _is_mutation_task(task)
    snapshot_before: dict[str, int] | None = None
    if is_mutation:
        snapshot_before = _get_doc_snapshot()

    # 4. Generate-execute-verify loop
    error_history: list[dict[str, str]] = []
    last_script = ""
    last_stdout = ""
    last_stderr = ""

    for attempt in range(1, max_retries + 1):
        LOGGER.info("Attempt %d/%d", attempt, max_retries)

        prompt = _build_prompt(task, sdef_context, doc_state, error_history or None)
        try:
            raw_response = call_llm(prompt)
        except RuntimeError as exc:
            LOGGER.error("LLM call failed: %s", exc)
            return {"success": False, "error": str(exc), "attempts": attempt}

        script = _extract_applescript(raw_response)
        last_script = script
        LOGGER.info("Generated AppleScript (%d chars):\n%s", len(script), script[:600])

        if not script or "tell application" not in script:
            err = "Output did not contain valid AppleScript with 'tell application'"
            LOGGER.warning(err)
            error_history.append({"script": script, "error": err})
            continue

        # Execute
        stdout, stderr, code = run_applescript_safe(script, timeout=30)
        last_stdout = stdout
        last_stderr = stderr

        if code != 0:
            LOGGER.warning("Script failed (exit %d): %s", code, stderr[:400])
            error_history.append({"script": script, "error": stderr})
            continue

        # ── Verification ──────────────────────────────────────────────────
        # V1: Explicit missing value detection — not coerced to False/0
        if _contains_missing_value(stdout, stderr):
            err = (
                "Script exited 0 but output contains 'missing value' — "
                "the property accessor path is wrong (e.g. use "
                "'font object of text object of paragraph N' not 'font of paragraph N')."
            )
            LOGGER.warning(err)
            error_history.append({"script": script, "error": err})
            continue

        # V2: Delta check — did the document actually change?
        if is_mutation and snapshot_before is not None:
            snapshot_after = _get_doc_snapshot()
            if snapshot_after is not None and not _snapshot_changed(snapshot_before, snapshot_after):
                err = (
                    "Script exited 0 but the document state did not change "
                    "(paragraph/table/word/char counts identical before and after). "
                    "The script likely targeted the wrong object or used a no-op path."
                )
                LOGGER.warning(err)
                error_history.append({"script": script, "error": err})
                # Update snapshot_before for next attempt
                snapshot_before = snapshot_after
                continue

        # ── Success ───────────────────────────────────────────────────────
        LOGGER.info("Task completed successfully on attempt %d", attempt)
        if stdout:
            LOGGER.info("Output: %s", stdout[:300])
        return {
            "success": True,
            "script": script,
            "stdout": stdout,
            "stderr": stderr,
            "attempts": attempt,
        }

    LOGGER.error("All %d attempts failed", max_retries)
    return {
        "success": False,
        "script": last_script,
        "stdout": last_stdout,
        "stderr": last_stderr,
        "attempts": max_retries,
        "error": f"Failed after {max_retries} attempts. Last error: {last_stderr}",
    }
