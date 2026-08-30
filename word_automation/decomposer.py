from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


class TaskDecomposer:
    """
    Uses Nemotron to intelligently break down complex, multi-part, or page-budgeted tasks
    into sequential, layered sub-tasks routed to FILE_MANIPULATION (python-docx) or APP_CONTROL (AppleScript).
    """

    FILE_MANIPULATION_KEYWORDS = [
        "text", "image", "table", "format", "paragraph", "heading", "style",
        "content", "add", "create", "document", "section", "essay", "report",
        "page", "form field", "form fields", "fillable", "checkbox", "signature", "nda", "proposal"
    ]

    APP_CONTROL_KEYWORDS = [
        "track changes", "track revisions", "print", "export as pdf", "save as pdf",
        "macro", "macros", "vba", "mail merge", "dialog"
    ]

    def classify_task(self, task_description: str) -> str:
        """Classify a single task as FILE_MANIPULATION or APP_CONTROL based on keywords with word boundaries."""
        task_lower = task_description.lower()

        for kw in self.APP_CONTROL_KEYWORDS:
            pattern = rf"\b{re.escape(kw)}\b"
            if re.search(pattern, task_lower):
                return "APP_CONTROL"
        return "FILE_MANIPULATION"

    def _extract_json_array(self, content: str) -> Optional[List[Dict[str, Any]]]:
        """Extract and parse a JSON array from raw model output."""
        if not content:
            return None

        text = content.strip()
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)(?:```|$)", text, re.IGNORECASE)
            if match:
                text = match.group(1).strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "tasks" in parsed and isinstance(parsed["tasks"], list):
                return parsed["tasks"]
        except Exception:
            pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                candidate = text[start : end + 1]
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass

        return None

    def decompose(
        self,
        user_request: str,
        nemotron_client: Any,
        model: str = "nvidia/nemotron-3-super-120b-a12b",
    ) -> List[Dict[str, str]]:
        """
        Use Nemotron to break complex requests into sequential sub-tasks.
        """
        subtasks, _ = self.decompose_with_usage(user_request, nemotron_client, model)
        return subtasks

    def decompose_with_usage(
        self,
        user_request: str,
        nemotron_client: Any,
        model: str = "nvidia/nemotron-3-super-120b-a12b",
    ) -> Tuple[List[Dict[str, str]], int]:
        """
        Decompose user request into sequential sub-tasks and return token usage.
        """
        # Detect explicit page request (e.g., '3 page analysis', '5 pages essay')
        page_match = re.search(r"\b(\d+)\s+pages?\b", user_request, re.IGNORECASE)
        page_count = int(page_match.group(1)) if page_match else 0

        system_prompt = f"""You are a task decomposition expert for Word automation.
Decompose the user request into minimal, concise, sequential sub-tasks.

CRITICAL PAGE BUDGETING RULE:
{f"- The user explicitly requested {page_count} PAGES. You MUST output EXACTLY {page_count} sequential FILE_MANIPULATION sub-tasks (Page 1 to Page {page_count}), each covering ~450 words of distinct content plus requested tables/formatting." if page_count > 1 else "- For standard documents, output 1-3 sequential sub-tasks."}

ROUTING:
- FILE_MANIPULATION: Creating files, text, tables, form fields, checkboxes, styles.
- APP_CONTROL: ONLY for live macOS Word runtime controls (Track Changes, Print, PDF export, VBA).

Output ONLY a valid JSON list of objects:
[
  {{"type": "FILE_MANIPULATION", "description": "Page 1: Initialize document, apply global font/size, Title, write Section 1 (~450 words) with formatting rules."}},
  {{"type": "FILE_MANIPULATION", "description": "Page 2: Open document, write Section 2 (~450 words) with formatting rules."}},
  {{"type": "FILE_MANIPULATION", "description": "Page 3: Open document, insert comparison table and write Section 3 (~350 words) with formatting rules."}}
]"""

        tokens_used = 0
        try:
            response = nemotron_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_request},
                ],
                temperature=0.1,
                max_tokens=600,
            )

            if hasattr(response, "usage") and response.usage:
                tokens_used = getattr(response.usage, "total_tokens", 0)

            content = response.choices[0].message.content or ""
            raw_subtasks = self._extract_json_array(content)

            if raw_subtasks:
                normalized = []
                for item in raw_subtasks:
                    if not isinstance(item, dict):
                        continue
                    desc = item.get("description") or item.get("task") or item.get("text") or ""
                    if not desc:
                        continue
                    t_type = item.get("type") or item.get("task_type") or self.classify_task(str(desc))
                    t_type = "APP_CONTROL" if "APP" in str(t_type).upper() else "FILE_MANIPULATION"
                    normalized.append({"type": t_type, "description": str(desc)})
                if normalized:
                    return normalized, tokens_used

        except Exception:
            pass

        fallback_type = self.classify_task(user_request)
        return [{"type": fallback_type, "description": user_request}], tokens_used
