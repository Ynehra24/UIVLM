# ScriptAgent (`NewArch`)

**Dynamic, SDEF Knowledge-Graph Grounded AppleScript Automation for Microsoft Word**

ScriptAgent dynamically parses Microsoft Word's dictionary (`Word.sdef`) into a 7,846-node relational Knowledge Graph, retrieves precise sub-graph context using hybrid TF-IDF + BFS graph traversal, reads live document state before code generation, and executes verified AppleScript with automatic self-healing.

---

## 🚀 Quick Start

Ensure Microsoft Word is installed on your Mac and configured with environment API keys:

```bash
# Clone and setup environment
pip install -r requirements.txt

# Run any Word automation task
python -m NewArch "Insert text 'Executive Summary' at the start and set font size to 24"
python -m NewArch "Create a 3x3 table with visible borders and set row 1 shading to blue"
python -m NewArch "Set page orientation to landscape and top margin to 72 points"
```

---

## 🏛 Architecture (`NewArch`)

```
User Task Query
       │
       ▼
┌────────────────────────────────────────────────────────┐
│ 1. Live Document Perception (_read_live_doc_context)   │
│    Reads table count, paragraph count & range bounds   │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 2. SDEF Knowledge Graph Retrieval (sdef_graph.py)       │
│    7,846 Nodes | 8,008 Edges | TF-IDF → BFS Sub-Graph  │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 3. Dynamic Prompt Assembly & LLM Generation (llm.py)   │
│    Gemini 2.5 Flash (~1.5s) → OpenRouter Fallback Chain │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 4. Truncation Sanitizer & Auto-Balancer (agent.py)     │
│    Pops incomplete lines & balances unclosed tell/ifs │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ 5. Execution & Two-Tier State Verification             │
│    osascript → missing value check → Snapshot Delta    │
└────────────────────────────────────────────────────────┘
```

---

## 🛠 Features

* **Zero Hardcoded Handlers**: 100% dictionary-driven from Microsoft Word's native `Word.sdef`.
* **Sub-Second Execution**: Powered by `gemini-2.5-flash` with instant 429 rate-limit failover to OpenRouter.
* **Document State Awareness**: Inspects active document before writing code to prevent accidental overwrites or invalid table cell targeting.
* **Non-Overwriting Range Collapse**: Uses `create range start 0 end 0` and `collapse range endRange direction collapse end` for clean, safe text/table insertion.
* **Truncation Sanitizer**: Automatically cleans mid-line API output cutoffs and balances unclosed `tell` / `if` blocks.
* **Two-Tier Verification**: Combines stdout `missing value` detection with pre/post document character & paragraph count snapshots.

---

## ⚙️ CLI Commands & Utilities

```bash
# Run a task
python -m NewArch "<PROMPT>"

# Print SDEF Knowledge Graph statistics
python -m NewArch --graph-info

# Rebuild SDEF Knowledge Graph cache
python -m NewArch --rebuild-graph

# Run Unit Test Suite (27 tests)
python -m unittest NewArch.test_sdef_graph -v

# Run Context Compression Benchmark
python -m NewArch.test_compressed_graph "<PROMPT>" --llm
```

---

## 🔑 Environment Configuration (`.env`)

```env
# Primary LLM API Keys
GEMINI_KEY=AIzaSy...
OPENROUTER_API_KEY=sk-or-v1-...

# Optional model configuration
GEMINI_MODEL=gemini-2.5-flash
OPENROUTER_MODELS=google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free
```
