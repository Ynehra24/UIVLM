from __future__ import annotations

from .applescript_generator import AppleScriptCodeGenerator
from .config import AutomationConfig
from .decomposer import TaskDecomposer
from .docx_generator import PythonDocxCodeGenerator
from .pipeline import WordAutomationPipeline

__all__ = [
    "WordAutomationPipeline",
    "AutomationConfig",
    "TaskDecomposer",
    "PythonDocxCodeGenerator",
    "AppleScriptCodeGenerator",
]
