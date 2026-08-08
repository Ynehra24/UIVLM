from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Tuple


def run_applescript_safe(script: str, timeout: int = 15, retries: int = 0) -> Tuple[str, str, int]:
    """Run an AppleScript snippet through `osascript` and return stdout, stderr, and exit code."""

    attempt = 0
    last_result = ("", "", 1)

    while attempt <= retries:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".applescript", delete=False) as file_handle:
            file_handle.write(script)
            script_path = file_handle.name

        try:
            result = subprocess.run(
                ["osascript", script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            last_result = (result.stdout.strip(), result.stderr.strip(), result.returncode)
            if result.returncode == 0:
                return last_result
        except subprocess.TimeoutExpired as exc:
            last_result = (
                exc.stdout.strip() if exc.stdout else "",
                exc.stderr.strip() if exc.stderr else "",
                124,
            )
        except FileNotFoundError:
            return "", "osascript command not found", 127
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

        attempt += 1

    return last_result
