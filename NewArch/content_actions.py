"""Content operations and windowed document state retrieval."""
from __future__ import annotations

import json
from typing import Any

from .applescript import run_applescript_safe
from .utils import applescript_string

COLOR_MAP = {
    "green": [0, 128, 0],
    "bright green": [0, 255, 0],
    "red": [255, 0, 0],
    "blue": [0, 0, 255],
    "yellow": [255, 255, 0],
    "black": [0, 0, 0],
    "white": [255, 255, 255],
    "gray": [128, 128, 128],
    "grey": [128, 128, 128],
    "orange": [255, 165, 0],
    "purple": [128, 0, 128],
}


def _word_bool(value: bool) -> str:
    return "true" if value else "false"


def _word_text_literal(value: str) -> str:
    return f'"{applescript_string(value)}"'


def _word_rgb_literal(value: list[int]) -> str:
    if len(value) != 3:
        raise ValueError("RGB list must contain exactly three integers.")
    return "{" + ", ".join(str(int(item)) for item in value) + "}"


def _table_cell_fill_script(row: int, column: int, color: str | list[int]) -> str:
    if isinstance(color, str):
        color_lower = color.lower().strip()
        if color_lower in COLOR_MAP:
            color = COLOR_MAP[color_lower]

    if isinstance(color, list):
        color_literal = _word_rgb_literal(color)
        return f"set background pattern color of shading of cell {column} of row {row} of targetTable to {color_literal}"
    return f'set background pattern color of shading of cell {column} of row {row} of targetTable to {color}'


def _table_shading_script(cell_colors: list[list[Any]]) -> str:
    lines = []
    for row_index, row in enumerate(cell_colors, start=1):
        for column_index, color in enumerate(row, start=1):
            if color:
                lines.append(_table_cell_fill_script(row_index, column_index, color))
    return "\n        ".join(lines)


def get_content_state(
    paragraph_start: int = 1,
    paragraph_limit: int = 20,
    preview_chars: int = 1200,
) -> dict[str, Any]:
    part1 = '''on escapeJson(txt)
    if txt is missing value then return ""
    set txt to txt as text
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to (character id 7)
    set textItems to every text item of txt
    set AppleScript's text item delimiters to ""
    set txt to textItems as text
    set backslashCharacter to "\\\\"
    set AppleScript's text item delimiters to backslashCharacter
    set textItems to every text item of txt
    set AppleScript's text item delimiters to backslashCharacter & backslashCharacter
    set txt to textItems as text
    set AppleScript's text item delimiters to quote
    set textItems to every text item of txt
    set AppleScript's text item delimiters to backslashCharacter & quote
    set txt to textItems as text
    set AppleScript's text item delimiters to linefeed
    set textItems to every text item of txt
    set AppleScript's text item delimiters to backslashCharacter & "n"
    set txt to textItems as text
    set AppleScript's text item delimiters to return
    set textItems to every text item of txt
    set AppleScript's text item delimiters to backslashCharacter & "r"
    set txt to textItems as text
    set AppleScript's text item delimiters to tab
    set textItems to every text item of txt
    set AppleScript's text item delimiters to backslashCharacter & "t"
    set txt to textItems as text
    set AppleScript's text item delimiters to oldDelimiters
    return txt
end escapeJson

on jsonValue(val)
    if val is missing value then return "null"
    if val is true then return "true"
    if val is false then return "false"
    try
        set valClass to class of val
        if valClass is integer or valClass is real or valClass is number then
            return val as text
        end if
    end try
    return "\\"" & my escapeJson(val as text) & "\\""
end jsonValue

tell application "Microsoft Word"
    if (count documents) is 0 then error "No Word document is open."
    set selectedText to ""
    try
        set selectedText to content of selection
        if length of selectedText > 300 then set selectedText to text 1 thru 300 of selectedText
    end try
    tell document 1
        set paragraphCount to count paragraphs
'''

    part2 = f'''        set requestedStart to {int(paragraph_start)}
        set requestedLimit to {int(paragraph_limit)}
        set requestedPreviewChars to {int(preview_chars)}
'''

    part3 = '''        set requestedEnd to requestedStart + requestedLimit - 1
        if requestedEnd > paragraphCount then set requestedEnd to paragraphCount
        set output to "{\\"name\\":" & my jsonValue(name)
        set output to output & ",\\"saved\\":" & my jsonValue(saved)
        set output to output & ",\\"word_count\\":" & my jsonValue(count words)
        set output to output & ",\\"paragraph_count\\":" & my jsonValue(paragraphCount)
        set output to output & ",\\"selection_text\\":" & my jsonValue(selectedText)
        set output to output & ",\\"paragraphs\\":["
        set firstParagraph to true
        repeat with i from requestedStart to requestedEnd
            tell paragraph i
                set paraText to content of text object
                if length of paraText > requestedPreviewChars then
                    set paraText to text 1 thru requestedPreviewChars of paraText
                end if
                set boldValue to bold of font object
                set italicValue to italic of font object
                set fontName to name of font object
                set fontSize to font size of font object
            end tell
            if not firstParagraph then set output to output & ","
            set firstParagraph to false
            set output to output & "{\\"index\\":" & (i as text)
            set output to output & ",\\"text\\":" & my jsonValue(paraText)
            set output to output & ",\\"bold\\":" & my jsonValue(boldValue)
            set output to output & ",\\"italic\\":" & my jsonValue(italicValue)
            set output to output & ",\\"font_name\\":" & my jsonValue(fontName)
            set output to output & ",\\"font_size\\":" & my jsonValue(fontSize) & "}"
        end repeat
        set output to output & "]"
        set tableCount to count tables
        set output to output & ",\\"table_count\\":" & my jsonValue(tableCount)
        return output & "}"
    end tell
end tell
'''
    script = part1 + part2 + part3
    stdout, stderr, code = run_applescript_safe(script, timeout=45)
    if code != 0:
        return {"error": stderr or "Failed to read Word content state."}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"error": f"Failed to parse content state JSON: {exc}", "raw": stdout[:500]}


def execute_context_request(retrieval: dict[str, Any]) -> dict[str, Any]:
    capability = retrieval.get("capability")
    arguments = retrieval.get("arguments", {})
    if capability == "inspect_object_window":
        object_type = arguments.get("object", "paragraph")
        if object_type != "paragraph":
            return {"success": False, "error": f"Unsupported object window type: {object_type}"}
        state = get_content_state(
            paragraph_start=int(arguments.get("start", 1)),
            paragraph_limit=int(arguments.get("limit", 10)),
            preview_chars=int(arguments.get("preview_chars", 1200)),
        )
        if "error" in state:
            return {"success": False, "error": state["error"]}
        return {"success": True, "content_state": state}

    if capability == "inspect_object_detail":
        index = int(arguments.get("index", 1))
        preview_chars = int(arguments.get("preview_chars", 1200))
        state = get_content_state(paragraph_start=index, paragraph_limit=1, preview_chars=preview_chars)
        if "error" in state:
            return {"success": False, "error": state["error"]}
        return {"success": True, "content_state": state}

    return {"success": False, "error": f"Unknown context retrieval capability: {capability}"}


def insert_text(arguments: dict[str, Any]) -> dict[str, Any]:
    text = str(arguments.get("text", ""))
    position = str(arguments.get("position", "end")).lower()
    literal = _word_text_literal(text)

    if position == "start":
        script = f'''
tell application "Microsoft Word"
    tell document 1
        insert text {literal} at (create range start 0 end 0)
    end tell
end tell
'''
    elif position == "end":
        script = f'''
tell application "Microsoft Word"
    tell document 1
        set targetRange to text object of paragraph (count paragraphs)
        collapse range targetRange direction collapse end
        insert text {literal} at targetRange
    end tell
end tell
'''
    else:
        script = f'''
tell application "Microsoft Word"
    tell selection
        type text text {literal}
    end tell
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=20)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def replace_paragraph(arguments: dict[str, Any]) -> dict[str, Any]:
    paragraph = int(arguments.get("paragraph", 1))
    text = str(arguments.get("text", ""))
    if paragraph < 1:
        return {"success": False, "error": "paragraph/index must be 1 or greater."}
    script = f'''
tell application "Microsoft Word"
    if (count documents) is 0 then error "No Word document is open."
    tell document 1
        if {paragraph} > (count paragraphs) then error "Paragraph index is out of range."
        set content of text object of paragraph {paragraph} to { _word_text_literal(text) }
    end tell
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=20)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def delete_paragraph(arguments: dict[str, Any]) -> dict[str, Any]:
    paragraph = int(arguments.get("paragraph", 1))
    if paragraph < 1:
        return {"success": False, "error": "paragraph/index must be 1 or greater."}
    script = f'''
tell application "Microsoft Word"
    if (count documents) is 0 then error "No Word document is open."
    tell document 1
        if {paragraph} > (count paragraphs) then error "Paragraph index is out of range."
        select text object of paragraph {paragraph}
    end tell
    delete selection
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=20)
    if code != 0:
        fallback = f'''
tell application "Microsoft Word"
    tell document 1
        set content of text object of paragraph {paragraph} to ""
    end tell
end tell
'''
        stdout, stderr, code = run_applescript_safe(fallback, timeout=20)
        if code == 0:
            return {
                "success": True,
                "warning": "Word did not accept deletion of the selected paragraph, so the paragraph content was cleared instead.",
            }
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def format_paragraph(arguments: dict[str, Any]) -> dict[str, Any]:
    paragraph = arguments.get("paragraph")
    parts: list[str] = []
    if arguments.get("font_name") is not None:
        parts.append(f'set name of font object of targetRange to {_word_text_literal(str(arguments["font_name"]))}')
    if arguments.get("font_size") is not None:
        parts.append(f"set font size of font object of targetRange to {float(arguments['font_size'])}")
    if arguments.get("bold") is not None:
        parts.append(f"set bold of font object of targetRange to {_word_bool(bool(arguments['bold']))}")
    if arguments.get("italic") is not None:
        parts.append(f"set italic of font object of targetRange to {_word_bool(bool(arguments['italic']))}")
    if not parts:
        return {"success": False, "error": "No supported formatting arguments supplied."}

    if paragraph is not None:
        paragraph = int(paragraph)
        if paragraph < 1:
            return {"success": False, "error": "paragraph/index must be 1 or greater."}
        target_setup = f'''
        if {paragraph} > (count paragraphs) then error "Paragraph index is out of range."
        set targetRange to text object of paragraph {paragraph} of document 1
'''
    else:
        target_setup = "set targetRange to selection"

    script = f'''
tell application "Microsoft Word"
    if (count documents) is 0 then error "No Word document is open."
    tell document 1
{target_setup}
        {"        ".join(parts)}
    end tell
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=20)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def find_replace(arguments: dict[str, Any]) -> dict[str, Any]:
    find_text = str(arguments.get("find", ""))
    replace_text = str(arguments.get("replace", ""))
    if not find_text:
        return {"success": False, "error": "find_replace requires a non-empty find argument."}
    script = f'''
tell application "Microsoft Word"
    tell document 1
        execute find find object content find text {_word_text_literal(find_text)} replace with {_word_text_literal(replace_text)} replace replace all
    end tell
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=30)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def save_document(arguments: dict[str, Any]) -> dict[str, Any]:
    _ = arguments
    script = '''
tell application "Microsoft Word"
    save document 1
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=20)
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}


def insert_table(arguments: dict[str, Any]) -> dict[str, Any]:
    rows = int(arguments.get("rows", 0))
    columns = int(arguments.get("columns", 0))
    values = arguments.get("values")
    paragraph = arguments.get("paragraph")
    cell_colors = arguments.get("cell_colors")

    if rows < 1 or columns < 1:
        return {"success": False, "error": "rows and columns must be 1 or greater."}
    if values is not None:
        if len(values) != rows or any(len(row) != columns for row in values):
            return {"success": False, "error": "values must match rows x columns."}
    if cell_colors is not None:
        if isinstance(cell_colors, str) or (isinstance(cell_colors, list) and len(cell_colors) == 3 and isinstance(cell_colors[0], int)):
            cell_colors = [[cell_colors for _ in range(columns)] for _ in range(rows)]
        elif len(cell_colors) != rows or any(not isinstance(row, list) or len(row) != columns for row in cell_colors):
            return {"success": False, "error": "cell_colors must match rows x columns."}

    if paragraph is not None:
        paragraph = int(paragraph)
        anchor_script = f'''
        if {paragraph} > (count paragraphs) then error "Paragraph index is out of range."
        set targetRange to text object of paragraph {paragraph}
        set content of targetRange to ""
'''
    else:
        anchor_script = '''
        set targetRange to text object of paragraph (count paragraphs)
        collapse range targetRange direction collapse end
'''

    value_lines = []
    if values:
        for row_index, row in enumerate(values, start=1):
            for column_index, value in enumerate(row, start=1):
                value_lines.append(
                    f"set content of text object of cell {column_index} of row {row_index} of targetTable to {_word_text_literal(str(value))}"
                )

    shading_script = _table_shading_script(cell_colors) if cell_colors else ""
    inner_body = "\n        ".join(filter(None, value_lines + ([shading_script] if shading_script else [])))

    script = f'''
tell application "Microsoft Word"
    if (count documents) is 0 then error "No Word document is open."
    tell document 1
{anchor_script}
        set targetTable to make new table at targetRange with properties {{number of rows:{rows}, number of columns:{columns}}}
        tell border options of targetTable
            set outside line style to line style single
            set inside line style to line style single
        end tell
        tell targetTable
            {inner_body}
        end tell
    end tell
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=45)
    if code != 0 and values:
        flat = "\t".join("\t".join(str(cell) for cell in row) for row in values)
        # Use proper insertion range at the end of the document
        fallback = f'''
tell application "Microsoft Word"
    tell document 1
        set targetRange to text object of paragraph (count paragraphs)
        collapse range targetRange direction collapse end
        insert text {_word_text_literal(flat)} at targetRange
    end tell
end tell
'''
        stdout, stderr, code = run_applescript_safe(fallback, timeout=20)
        if code == 0:
            return {
                "success": True,
                "warning": "Fell back to inserting a tab-separated text representation of the table.",
            }
        return {"success": False, "error": f"Insert table failed and fallback failed: {stderr}"}
    return {"success": code == 0, "error": stderr if code != 0 else None, "stdout": stdout}
