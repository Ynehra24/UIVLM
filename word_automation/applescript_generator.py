from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class AppleScriptCodeGenerator:
    """
    Generates AppleScript code using Osaurus AppleScript-8B (with seamless Nemotron fallback when Osaurus is offline)
    and verifies syntax and execution with osacompile and osascript.
    """

    CONTEXT = """
-- Canonical Microsoft Word AppleScript Quick Reference:

tell application "Microsoft Word"
    -- 1. Ensure target document is open:
    if (count documents) = 0 then
        try
            set docFile to POSIX file "output.docx"
            open file docFile
        end try
    end if

    -- 2. Range targeting at end of document:
    set myRange to text object of active document
    collapse range myRange direction collapse end

    -- 3. Form Fields (Text and Checkbox):
    -- Text field:
    set ff1 to make new form field at myRange with data form field text
    set name of ff1 to "RecipientName"

    -- Checkbox field:
    set ff2 to make new form field at myRange with data form field check box
    set name of ff2 to "AgreeToTerms"

    -- 4. Track Changes:
    set track revisions of active document to true

    -- 5. Margins & Page Setup:
    tell page setup of section 1 of active document
        set top margin to 72.0
        set bottom margin to 72.0
    end tell
end tell
"""

    def __init__(
        self,
        osaurus_client: Any,
        fallback_client: Optional[Any] = None,
        fallback_model: str = "nvidia/nemotron-3-super-120b-a12b",
        sdef_cache_dir: str = ".automation_cache",
        timeout: int = 10,
        sdef_timeout: int = 5,
    ):
        self.osaurus = osaurus_client
        self.fallback_client = fallback_client
        self.fallback_model = fallback_model
        self.sdef_cache_dir = Path(sdef_cache_dir)
        self.timeout = timeout
        self.sdef_timeout = sdef_timeout
        self._sdef_memory_cache: Optional[str] = None

    def get_word_sdef(self) -> str:
        """
        Extract Word.sdef (scripting definition).
        Caches result in memory and on disk to avoid repeated extraction.

        Returns: sdef XML as string (trimmed to ~2000 chars for token efficiency)
        """
        if self._sdef_memory_cache is not None:
            return self._sdef_memory_cache

        # Check disk cache
        try:
            self.sdef_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.sdef_cache_dir / "word_sdef_snippet.txt"
            if cache_file.exists():
                cached = cache_file.read_text(encoding="utf-8").strip()
                if cached:
                    self._sdef_memory_cache = cached
                    return cached
        except Exception:
            pass

        # 1. Try sdef command
        content = ""
        try:
            result = subprocess.run(
                ["sdef", "/Applications/Microsoft Word.app"],
                capture_output=True,
                text=True,
                timeout=self.sdef_timeout,
            )
            if result.returncode == 0 and result.stdout.strip():
                content = result.stdout[:2000]
        except Exception:
            pass

        # 2. Fallback to direct sdef file reading
        if not content:
            candidate_paths = [
                "/Applications/Microsoft Word.app/Contents/Resources/Word.sdef",
                "/Applications/Microsoft Word.app/Contents/Resources/Word 2019.sdef",
            ]
            for p in candidate_paths:
                fpath = Path(p)
                if fpath.exists():
                    try:
                        raw = fpath.read_text(encoding="utf-8", errors="ignore")
                        content = raw[:2000]
                        break
                    except Exception:
                        pass

        if content:
            self._sdef_memory_cache = content
            try:
                cache_file = self.sdef_cache_dir / "word_sdef_snippet.txt"
                cache_file.write_text(content, encoding="utf-8")
            except Exception:
                pass
            return content

        return ""

    def _extract_clean_script(self, raw_content: str) -> str:
        """Extract clean AppleScript from model response, stripping thinking tags and code fences."""
        if not raw_content:
            return ""

        text = raw_content.strip()

        # Remove <think>...</think> tags if model produces reasoning output
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

        # Extract from ```applescript or ``` code block if present
        if "```" in text:
            match = re.search(r"```(?:applescript|osascript)?\s*([\s\S]*?)(?:```|$)", text, re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()

        # If tell block is embedded in explanatory text, extract the tell block
        tell_match = re.search(r'(tell\s+application\s+"Microsoft Word"[\s\S]*?end\s+tell)', text, re.IGNORECASE)
        if tell_match:
            return tell_match.group(1).strip()

        return text

    def generate(
        self,
        task_description: str,
        include_sdef: bool = True,
        model: str = "osaurus-applescript-8b",
        error_context: Optional[str] = None,
    ) -> str:
        """
        Generate AppleScript code using Osaurus-8B or fallback model.
        """
        script, _ = self.generate_with_usage(
            task_description, include_sdef=include_sdef, model=model, error_context=error_context
        )
        return script

    def generate_with_usage(
        self,
        task_description: str,
        include_sdef: bool = True,
        model: str = "osaurus-applescript-8b",
        error_context: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Generate AppleScript code and return the token usage count.
        Seamlessly falls back to fallback_client (Nemotron) if Osaurus is offline.
        """
        system_prompt = f"""You are an AppleScript expert specializing in Microsoft Word automation on macOS.

Generate clean, production-ready, valid AppleScript code.

{self.CONTEXT}

Rules:
1. Always use 'tell application "Microsoft Word"' blocks
2. Generate ONLY valid AppleScript (do NOT use VBA constants like wdFieldFormTextInput; use native AppleScript like `make new form field at myRange with data form field text`)
3. Ensure the document is open before operating on it (e.g. `if (count documents) = 0 then ...`)
4. Target active document or open the specified document
5. No explanations, output ONLY executable AppleScript"""

        if include_sdef:
            sdef_content = self.get_word_sdef()
            if sdef_content:
                system_prompt += f"\n\nWord.sdef context:\n{sdef_content}"

        user_content = task_description
        if error_context:
            user_content += f"\n\nIMPORTANT: Fix this previous error:\n{error_context}"

        tokens_used = 0
        response = None

        # 1. Try Primary Client (Osaurus-8B)
        try:
            response = self.osaurus.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
        except Exception as osaurus_exc:
            # 2. If Osaurus is offline/unreachable, fallback to Nemotron
            if self.fallback_client is not None:
                try:
                    kwargs: Dict[str, Any] = {
                        "model": self.fallback_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048,
                    }
                    try:
                        response = self.fallback_client.chat.completions.create(
                            **kwargs,
                            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                        )
                    except Exception:
                        response = self.fallback_client.chat.completions.create(**kwargs)
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"Both Osaurus and fallback failed. Osaurus: {osaurus_exc}; Fallback: {fallback_exc}"
                    )
            else:
                raise osaurus_exc

        if hasattr(response, "usage") and response.usage:
            tokens_used = getattr(response.usage, "total_tokens", 0)

        content = response.choices[0].message.content or ""
        script = self._extract_clean_script(content)
        return script, tokens_used

    def compile_check(self, script: str) -> Tuple[bool, str]:
        """
        Verify AppleScript syntax validity using osacompile without executing side-effects.

        Returns: (is_valid: bool, error_message: str)
        """
        if not script.strip():
            return False, "Empty AppleScript code"

        try:
            result = subprocess.run(
                ["osacompile", "-o", "/dev/null", "-e", script],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            if result.returncode == 0:
                return True, ""
            return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "osacompile timed out"
        except Exception as e:
            return False, str(e)

    def execute(self, script: str) -> Tuple[bool, str, str]:
        """
        Execute AppleScript via osascript.
        Only executes if compile check passes.

        Returns: (success: bool, stdout: str, stderr: str)
        """
        is_valid, error = self.compile_check(script)
        if not is_valid:
            return False, "", f"Compilation error: {error}"

        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            success = result.returncode == 0
            return success, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "", "osascript execution timed out"
        except Exception as e:
            return False, "", str(e)
