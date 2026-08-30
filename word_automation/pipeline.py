from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

from openai import OpenAI

from .applescript_generator import AppleScriptCodeGenerator
from .config import AutomationConfig
from .decomposer import TaskDecomposer
from .docx_generator import PythonDocxCodeGenerator


class WordAutomationPipeline:
    """
    Main orchestration layer for Word automation using a 3-layer architecture:
    1. Nemotron (Task decomposition & routing)
    2. Python-docx (File-level manipulation - 70-80% of tasks)
    3. Osaurus AppleScript-8B (App-level control - edge cases, with Nemotron fallback if Osaurus offline)
    """

    def __init__(
        self,
        nemotron_api_key: Optional[str] = None,
        osaurus_base_url: str = "http://localhost:8080/v1",
        config: Optional[AutomationConfig] = None,
        nemotron_base_url: Optional[str] = None,
        nemotron_client: Optional[Any] = None,
        osaurus_client: Optional[Any] = None,
        max_retries: int = 2,
    ):
        self.config = config or AutomationConfig.from_env()
        self.max_retries = max_retries

        api_key = nemotron_api_key or self.config.nemotron_api_key
        base_url = nemotron_base_url or self.config.nemotron_base_url
        os_url = osaurus_base_url or self.config.osaurus_base_url

        if nemotron_client is not None:
            self.nemotron = nemotron_client
        else:
            self.nemotron = OpenAI(
                base_url=base_url,
                api_key=api_key or "not-set",
            )

        if osaurus_client is not None:
            self.osaurus = osaurus_client
        else:
            self.osaurus = OpenAI(
                base_url=os_url,
                api_key="not-needed",
            )

        self.decomposer = TaskDecomposer()
        self.docx_gen = PythonDocxCodeGenerator()
        self.applescript_gen = AppleScriptCodeGenerator(
            osaurus_client=self.osaurus,
            fallback_client=self.nemotron,
            fallback_model=self.config.nemotron_model,
            sdef_cache_dir=self.config.cache_dir,
            timeout=self.config.applescript_timeout,
            sdef_timeout=self.config.sdef_extraction_timeout,
        )

        self.execution_log: List[Dict[str, Any]] = []

    def _determine_target_filename(self, user_request: str) -> str:
        """Derive or extract a clean target filename for the document."""
        match = re.search(r"[\w\-]+\.docx", user_request, re.IGNORECASE)
        if match:
            return match.group(0)
        # Extract title keywords or default
        clean = re.sub(r"[^\w\s]", "", user_request.lower())
        words = [w for w in clean.split() if w not in {"write", "me", "a", "an", "the", "on", "and", "in", "page", "pages", "document", "docx", "create"}]
        if words:
            return "_".join(words[:4]).capitalize() + ".docx"
        return "Document.docx"

    def execute(self, user_request: str, verbose: bool = True) -> Dict[str, Any]:
        """
        Execute a Word automation request end-to-end.
        """
        results: Dict[str, Any] = {
            "status": "success",
            "original_request": user_request,
            "subtasks": [],
            "errors": [],
            "total_tokens_used": 0,
            "total_tokens": 0,
        }

        if verbose:
            print(f"📋 Task: {user_request}\n")

        # Determine target file and ensure fresh start
        target_filename = self._determine_target_filename(user_request)
        if os.path.exists(target_filename):
            try:
                os.remove(target_filename)
            except Exception:
                pass

        total_tokens = 0

        try:
            if verbose:
                print("🔍 Decomposing task with Nemotron...")

            subtasks, decomp_tokens = self.decomposer.decompose_with_usage(
                user_request,
                self.nemotron,
                model=self.config.nemotron_model,
            )
            total_tokens += decomp_tokens

            if verbose:
                print(f"   Found {len(subtasks)} sub-task(s)\n")

            all_succeeded = True
            any_succeeded = False

            for i, subtask in enumerate(subtasks):
                task_type = subtask.get("type", "FILE_MANIPULATION")
                description = subtask.get("description", "")
                is_first_subtask = (i == 0)

                # Ensure consistent filename in description
                if target_filename not in description and ".docx" not in description:
                    description = f"Target file '{target_filename}'. " + description

                if verbose:
                    print(f"📝 Sub-task {i+1}/{len(subtasks)}: [{task_type}]")
                    print(f"   Description: {description[:100]}...")

                if task_type == "FILE_MANIPULATION":
                    result, sub_tokens = self._handle_file_manipulation(
                        description, target_filename, is_first_subtask, verbose
                    )
                elif task_type == "APP_CONTROL":
                    result, sub_tokens = self._handle_app_control(description, verbose)
                else:
                    result = {
                        "success": False,
                        "error": f"Unknown task type: {task_type}",
                        "output": "",
                    }
                    sub_tokens = 0

                total_tokens += sub_tokens

                results["subtasks"].append({
                    "type": task_type,
                    "description": description,
                    "result": result,
                })

                if result.get("success", False):
                    any_succeeded = True
                else:
                    all_succeeded = False
                    err = result.get("error", "Unknown error")
                    results["errors"].append(err)

            if all_succeeded:
                results["status"] = "success"
            elif any_succeeded:
                results["status"] = "partial"
            else:
                results["status"] = "failure"

        except Exception as e:
            results["status"] = "failure"
            results["errors"].append(str(e))

        results["total_tokens_used"] = total_tokens
        results["total_tokens"] = total_tokens
        self.execution_log.append(dict(results))

        if verbose:
            print(f"\n✅ Pipeline Finished. Status: {results['status'].upper()}")
            print(f"   Total Tokens: ~{total_tokens}")
            if results["errors"]:
                print("⚠️ Errors encountered:")
                for err in results["errors"]:
                    print(f"   - {err}")

        return results

    def _handle_file_manipulation(
        self, description: str, target_filename: str, is_first_subtask: bool, verbose: bool
    ) -> tuple[Dict[str, Any], int]:
        total_tokens = 0
        last_error = ""
        last_code = ""
        last_output = ""

        # Enforce fresh document creation on subtask 1 vs appending on subsequent subtasks
        context_guidance = (
            f"This is the first subtask. Create a new document: `filename = '{target_filename}'; doc = Document()`."
            if is_first_subtask
            else f"This is an appending subtask. Open the existing document: `filename = '{target_filename}'; doc = Document(filename)`. Do NOT repeat the main document title."
        )

        for attempt in range(1, self.max_retries + 1):
            try:
                if verbose:
                    retry_label = f" (Attempt {attempt}/{self.max_retries})" if attempt > 1 else ""
                    print(f"   📄 Generating python-docx code via Nemotron{retry_label}...")

                full_desc = f"{context_guidance}\n{description}"
                code, tokens = self.docx_gen.generate_with_usage(
                    full_desc,
                    self.nemotron,
                    model=self.config.nemotron_model,
                    error_context=last_error if attempt > 1 else None,
                )
                total_tokens += tokens
                last_code = code

                if verbose:
                    print(f"   Code generated ({len(code)} chars)")
                    print("   ⚙️ Executing python-docx code...")

                success, output, error = self.docx_gen.execute(code)
                last_output = output

                if success:
                    return {
                        "success": True,
                        "code": code,
                        "output": output,
                        "error": "",
                    }, total_tokens
                else:
                    last_error = error
                    if verbose:
                        print(f"   ⚠️ Execution attempt {attempt} failed: {error[:150]}...")

            except Exception as e:
                last_error = str(e)
                if verbose:
                    print(f"   ⚠️ Exception on attempt {attempt}: {last_error}")

        return {
            "success": False,
            "code": last_code,
            "output": last_output,
            "error": last_error,
        }, total_tokens

    def _handle_app_control(
        self, description: str, verbose: bool
    ) -> tuple[Dict[str, Any], int]:
        total_tokens = 0
        last_error = ""
        last_script = ""
        last_output = ""

        for attempt in range(1, self.max_retries + 1):
            try:
                if verbose:
                    retry_label = f" (Attempt {attempt}/{self.max_retries})" if attempt > 1 else ""
                    print(f"   🍎 Generating AppleScript via Osaurus-8B (or Nemotron fallback){retry_label}...")

                script, tokens = self.applescript_gen.generate_with_usage(
                    description,
                    include_sdef=True,
                    model=self.config.osaurus_model,
                    error_context=last_error if attempt > 1 else None,
                )
                total_tokens += tokens
                last_script = script

                if verbose:
                    print(f"   Script generated ({len(script)} chars)")
                    print("   ✓ Verifying syntax with osacompile...")

                is_valid, compile_error = self.applescript_gen.compile_check(script)

                if not is_valid:
                    last_error = f"Compilation failed: {compile_error}"
                    if verbose:
                        print(f"   ❌ Syntax compilation failed: {compile_error[:150]}...")
                    continue

                if verbose:
                    print("   ⚙️ Executing AppleScript via osascript...")

                success, stdout, stderr = self.applescript_gen.execute(script)
                last_output = stdout

                if success:
                    return {
                        "success": True,
                        "script": script,
                        "output": stdout,
                        "error": stderr,
                    }, total_tokens
                else:
                    last_error = stderr
                    if verbose:
                        print(f"   ⚠️ osascript execution failed: {stderr[:150]}...")

            except Exception as e:
                last_error = str(e)

        return {
            "success": False,
            "script": last_script,
            "output": last_output,
            "error": last_error,
        }, total_tokens
