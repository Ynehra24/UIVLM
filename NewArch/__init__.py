"""NewArch — SDEF-grounded structured planning pipeline.

The LLM produces a JSON execution plan (not raw AppleScript).
Deterministic Python code compiles correct AppleScript from the plan
using Word's SDEF schema as the source of truth.

Pipeline: Task → Capability Selection → Field Selection → Planner (JSON IR)
  → Deterministic Executor → Verifier → Repair Loop
"""

__version__ = "0.3.0"
