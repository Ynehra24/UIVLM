"""Automated Testbed & Ground-Truth Verification Suite for ScriptAgent (NewArch).

Runs a battery of natural language tasks across different Word capability areas,
inspects the live Word DOM via AppleScript after execution, and compares against
ground-truth expectations to compute an accuracy benchmark score.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List

from .agent import run_task
from .applescript import run_applescript_safe
from .config import LOGGER


# Helper to reset document state before each test case
def reset_word_document() -> bool:
    """Close all open documents without saving and open a fresh document."""
    script = '''
tell application "Microsoft Word"
    activate
    set docCount to count documents
    repeat with i from docCount to 1 by -1
        close document i saving no
    end repeat
    create new document
end tell
'''
    stdout, stderr, code = run_applescript_safe(script, timeout=10)
    time.sleep(0.5)
    return code == 0


# Test Cases with Ground-Truth Verification Logic
TEST_CASES: List[Dict[str, Any]] = [
    {
        "id": "TC-01",
        "name": "Table Creation & Dimensions",
        "task": "Create a 3x4 table with visible borders",
        "setup_script": None,
        "inspect_script": '''
tell application "Microsoft Word"
    tell active document
        set tCount to count tables
        if tCount > 0 then
            set rCount to count rows of table 1
            set cCount to count columns of table 1
            return (tCount as text) & "|" & (rCount as text) & "|" & (cCount as text)
        else
            return "0|0|0"
        end if
    end tell
end tell
''',
        "verify": lambda raw: (
            raw.strip().split("|") == ["1", "3", "4"]
        ),
        "ground_truth": {"table_count": 1, "rows": 3, "columns": 4},
    },
    {
        "id": "TC-02",
        "name": "Text Insertion & Character Formatting",
        "task": "Insert text 'Q4 Financial Report' and set font size to 24 and bold to true",
        "setup_script": None,
        "inspect_script": '''
tell application "Microsoft Word"
    tell active document
        set pText to content of text object of paragraph 1
        set fSize to font size of font object of text object of paragraph 1
        set isBold to bold of font object of text object of paragraph 1
        return pText & "|" & (fSize as text) & "|" & (isBold as text)
    end tell
end tell
''',
        "verify": lambda raw: (
            "Q4 Financial Report" in raw.split("|")[0]
            and float(raw.split("|")[1]) == 24.0
            and raw.split("|")[2].lower() in {"true", "1"}
        ),
        "ground_truth": {"text_contains": "Q4 Financial Report", "font_size": 24.0, "bold": True},
    },
    {
        "id": "TC-03",
        "name": "Find & Replace",
        "task": "Replace all occurrences of 'Draft' with 'Approved'",
        "setup_script": '''
tell application "Microsoft Word"
    tell active document
        set content of text object to "Status: Draft version. This is a Draft document."
    end tell
end tell
''',
        "inspect_script": '''
tell application "Microsoft Word"
    tell active document
        return content of text object
    end tell
end tell
''',
        "verify": lambda raw: "Approved" in raw and "Draft" not in raw,
        "ground_truth": {"contains": "Approved", "not_contains": "Draft"},
    },
    {
        "id": "TC-04",
        "name": "Page Orientation Setup",
        "task": "Set the document page orientation to landscape",
        "setup_script": None,
        "inspect_script": '''
tell application "Microsoft Word"
    tell active document
        set orient to orientation of page setup of section 1
        return orient as text
    end tell
end tell
''',
        "verify": lambda raw: "landscape" in raw.lower(),
        "ground_truth": {"orientation": "orientation landscape"},
    },
    {
        "id": "TC-05",
        "name": "Table Cell Content & Styling",
        "task": "Create a 2x2 table and set cell 1 row 1 text to 'Header 1'",
        "setup_script": None,
        "inspect_script": '''
tell application "Microsoft Word"
    tell active document
        set tCount to count tables
        if tCount > 0 then
            set cText to content of text object of cell 1 of row 1 of table 1
            return (tCount as text) & "|" & cText
        else
            return "0|"
        end if
    end tell
end tell
''',
        "verify": lambda raw: (
            raw.split("|")[0] == "1" and "Header 1" in raw.split("|")[1]
        ),
        "ground_truth": {"table_count": 1, "cell_1_1_contains": "Header 1"},
    },
]


def run_testbed() -> Dict[str, Any]:
    """Execute all testbed cases and generate accurate ground-truth comparison results."""
    print("\n" + "=" * 70)
    print(" 🚀 ScriptAgent (NewArch) — Automated Testbed & Ground-Truth Evaluator")
    print("=" * 70)

    results = []
    passed_count = 0
    total_count = len(TEST_CASES)

    for idx, tc in enumerate(TEST_CASES, 1):
        print(f"\n[Test {idx}/{total_count}] {tc['id']}: {tc['name']}")
        print(f"  Task Prompt: \"{tc['task']}\"")

        # 1. Reset Word state
        if not reset_word_document():
            print("  ❌ Failed to reset Word document.")
            results.append({"id": tc["id"], "name": tc["name"], "passed": False, "error": "Reset failed"})
            continue

        # 2. Execute optional setup script
        if tc.get("setup_script"):
            run_applescript_safe(tc["setup_script"])

        # 3. Run Agent Task
        start_t = time.monotonic()
        agent_res = run_task(tc["task"], max_retries=3)
        duration = time.monotonic() - start_t

        if not agent_res["success"]:
            print(f"  ❌ Execution Failed in Agent: {agent_res.get('error')}")
            results.append({
                "id": tc["id"],
                "name": tc["name"],
                "passed": False,
                "duration": round(duration, 2),
                "error": agent_res.get("error"),
                "script": agent_res.get("script"),
            })
            continue

        # 4. Inspect Document State for Ground Truth Verification
        stdout, stderr, code = run_applescript_safe(tc["inspect_script"])
        actual_raw = stdout.strip()

        is_correct = tc["verify"](actual_raw)
        if is_correct:
            passed_count += 1
            print(f"  ✅ PASSED ({duration:.2f}s)")
            print(f"     Ground Truth Expected: {tc['ground_truth']}")
            print(f"     Document Actual State: '{actual_raw}'")
        else:
            print(f"  ❌ VERIFICATION FAILED ({duration:.2f}s)")
            print(f"     Ground Truth Expected: {tc['ground_truth']}")
            print(f"     Document Actual State: '{actual_raw}'")

        results.append({
            "id": tc["id"],
            "name": tc["name"],
            "passed": is_correct,
            "duration": round(duration, 2),
            "ground_truth": tc["ground_truth"],
            "actual_raw": actual_raw,
            "generated_script": agent_res.get("script"),
        })

    accuracy_score = (passed_count / total_count) * 100.0

    print("\n" + "=" * 70)
    print(" 📊 EVALUATION SUMMARY SCORE")
    print("=" * 70)
    print(f" Total Tests Run : {total_count}")
    print(f" Passed          : {passed_count}")
    print(f" Failed          : {total_count - passed_count}")
    print(f" ACCURACY SCORE  : {accuracy_score:.1f}%")
    print("=" * 70 + "\n")

    return {
        "accuracy_score": accuracy_score,
        "passed": passed_count,
        "total": total_count,
        "details": results,
    }


if __name__ == "__main__":
    run_testbed()
