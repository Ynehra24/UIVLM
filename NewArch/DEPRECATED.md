# Deprecation Checklist — NewArch Legacy Files

Files that belonged to the structured JSON IR pipeline (Approach 2) that has been
superseded by the SDEF Knowledge Graph + direct AppleScript generation (Approach 1).

No active code path in `NewArch` should import any file marked `removed`.

## Status Key
- `bypassed` — file still on disk, no active import from cli/agent pipeline
- `pending` — needs manual review before removal

---

| File | Status | Notes |
|------|--------|-------|
| `runner.py` | `bypassed` | Multi-step JSON IR loop; no longer called from cli.py |
| `planner.py` | `bypassed` | LLM JSON planner; superseded by direct graph-grounded generation |
| `execution_runtime.py` | `bypassed` | JSON plan executor; superseded by agent.py direct execution |
| `selectors.py` | `bypassed` | LLM capability/field selectors; superseded by graph seeding |
| `verifier.py` | `bypassed` | Plan verifier; superseded by agent.py inline verification |
| `content_actions.py` | `bypassed` | Hand-coded AppleScript templates; no longer called from main pipeline |
| `property_actions.py` | `bypassed` | Property write executor; no longer called from main pipeline |
| `property_path.py` | `bypassed` | Property path parser; no longer called from main pipeline |
| `prompts.py` | `bypassed` | Planner prompt templates; superseded by agent.py SYSTEM_PROMPT |
| `word_schema.py` | `bypassed` | WordSchema wrapper; superseded by word_sdef.py + sdef_graph.py |
| `sdef_indexer.py` | `bypassed` | Legacy SDEF keyword indexing; superseded by sdef_graph.py TF-IDF |

## Verification script

Run to confirm no active import of bypassed files from the live pipeline:

```bash
cd /Users/yatharthnehva/Desktop/Projects_ALL/ScriptAgent
grep -rn "from .runner\|from .planner\|from .execution_runtime\|from .selectors\|from .verifier\|from .property_actions\|from .property_path\|from .prompts\|from .word_schema\|from .sdef_indexer" NewArch/cli.py NewArch/agent.py NewArch/sdef_graph.py NewArch/word_sdef.py NewArch/__main__.py
```

Expected output: empty (no matches).
