from __future__ import annotations

import ast
import contextlib
import io
import os
import re
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import docx
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Cm, Inches, Mm, Pt, RGBColor


class PythonDocxCodeGenerator:
    """
    Generates and executes concise, robust python-docx code for file manipulation tasks.
    Enforces safe table indexing, token-efficient code generation, and professional styling.
    """

    CONTEXT = r"""
# Python-DOCX Reference & Best Practices

import os
import re
from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm, Mm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT

filename = 'output.docx'
doc = Document(filename) if os.path.exists(filename) else Document()

# 1. Document-Wide Typography:
style = doc.styles['Normal']
style.font.name = 'Georgia'  # Set requested font (e.g. Georgia, Calibri, Arial)
style.font.size = Pt(11)     # Set requested font size

# 2. Section Heading & Title (Add ONCE):
title_p = doc.add_paragraph()
title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
t_run = title_p.add_run('Quantum Computing and Cryptography')
t_run.bold = True
t_run.font.size = Pt(16)
title_p.paragraph_format.space_after = Pt(12)

# 3. Granular Token-Level Styling (Acronyms Bold, Terms Italic, Milestone Years Red & Bold):
def add_styled_paragraph(doc, text):
    p = doc.add_paragraph()
    tokens = re.split(r'(\s+|[.,;:!?\"\'()]+)', text.strip())
    for t in tokens:
        if not t: continue
        if t.isspace() or t in '.,;:!?\"\'()':
            p.add_run(t)
            continue
        clean = t.strip('.,;:!?\"\'()')
        if not clean:
            p.add_run(t)
            continue
        # Milestone Years (e.g., 1994, 2024, 1980s) -> Red and Bold
        if re.match(r'^(19|20)\d\d(s)?$', clean):
            r = p.add_run(t)
            r.bold = True
            r.font.color.rgb = RGBColor(255, 0, 0)
        # Acronyms (e.g., RSA, NIST, Qubit, PQC, AES, ECC, DES) -> Bold
        elif clean.isupper() or clean in {'Qubit', 'Qubits'}:
            r = p.add_run(t)
            r.bold = True
        # Math & Physics terms -> Italic
        elif clean.lower() in {'superposition', 'entanglement', 'qubits', 'decoherence', 'hamiltonian', 'lattice', 'logarithm', 'isogeny', 'polynomial'}:
            r = p.add_run(t)
            r.italic = True
        else:
            p.add_run(t)

# 4. Safe 4x4 Comparison Table (Never causes IndexErrors):
headers = ['Criteria', 'Classical Cryptography', 'Quantum Threat', 'Post-Quantum Defense']
data = [
    ['Key Primitive', 'RSA / ECC (Factorization)', 'Shor Algorithm solves in polynomial time', 'Lattice (ML-KEM / ML-DSA)'],
    ['Symmetric Security', 'AES-128 / AES-256', 'Grover Algorithm reduces security by half', 'Upgrade to AES-256'],
    ['NIST Status', 'Legacy FIPS Standards', 'Vulnerable to Harvest-Now-Decrypt-Later', 'FIPS 203, 204, 205 Standards']
]
table = doc.add_table(rows=len(data) + 1, cols=len(headers))
table.style = 'Table Grid'
for c, h in enumerate(headers):
    table.cell(0, c).text = h
    if table.cell(0, c).paragraphs and table.cell(0, c).paragraphs[0].runs:
        table.cell(0, c).paragraphs[0].runs[0].bold = True
for r, row in enumerate(data, start=1):
    for c, val in enumerate(row):
        table.cell(r, c).text = str(val)

# 5. Saving:
doc.save(filename)
"""

    def _extract_clean_code(self, raw_content: str) -> str:
        """Extract clean Python code from model response, stripping thinking tags and code fences."""
        if not raw_content:
            return ""

        text = raw_content.strip()
        text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()

        if "```" in text:
            match = re.search(r"```(?:python)?\s*([\s\S]*?)(?:```|$)", text, re.IGNORECASE)
            if match:
                text = match.group(1).strip()

        lines = text.splitlines()
        code_start_keywords = (
            "import ", "from ", "#", "doc =", "doc=", "def ", "class ",
            "table =", "table=", "p =", "p=", "run =", "run=", "filename =", "filename="
        )
        start_idx = 0
        found_code = False
        for idx, line in enumerate(lines):
            l_str = line.strip()
            if any(l_str.startswith(kw) for kw in code_start_keywords):
                start_idx = idx
                found_code = True
                break

        if found_code and start_idx > 0:
            text = "\n".join(lines[start_idx:]).strip()

        return text

    def generate(
        self,
        task_description: str,
        nemotron_client: Any,
        model: str = "nvidia/nemotron-3-super-120b-a12b",
        error_context: Optional[str] = None,
    ) -> str:
        code, _ = self.generate_with_usage(task_description, nemotron_client, model, error_context)
        return code

    def generate_with_usage(
        self,
        task_description: str,
        nemotron_client: Any,
        model: str = "nvidia/nemotron-3-super-120b-a12b",
        error_context: Optional[str] = None,
    ) -> Tuple[str, int]:
        """
        Generate concise, executable python-docx code and track token usage.
        """
        system_prompt = f"""You are a python-docx expert.

Generate concise, accurate, production-ready Python code using the python-docx library.

{self.CONTEXT}

CRITICAL RULES:
1. Generate ONLY valid, executable Python code with all necessary imports.
2. Be CONCISE and TOKEN-LEAN. Do not write repetitive code or unnecessary comments.
3. PERSISTENCE: Use `doc = Document(filename) if os.path.exists(filename) else Document()` so subtasks layer cleanly.
4. TYPOGRAPHY: Set font family and font size globally on `doc.styles['Normal']`.
5. FORMATTING RULES: If requested (e.g. bold acronyms, italic terms, red milestone years), define a clean helper `add_styled_paragraph(doc, text)` as shown in the reference.
6. NO UNREQUESTED PAGE BREAKS: Do NOT insert empty manual page breaks; let continuous paragraphs fill the document.
7. TABLES: Use `table.cell(r, c).text = ...` and `table.style = 'Table Grid'`.
8. Always save the document with `doc.save(filename)`.
9. No conversational explanations, output ONLY executable Python code."""

        user_content = task_description
        if error_context:
            user_content += f"\n\nIMPORTANT: Fix this previous error:\n{error_context}"

        tokens_used = 0
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            "max_tokens": 2048,
        }

        try:
            response = nemotron_client.chat.completions.create(
                **kwargs,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
        except Exception:
            response = nemotron_client.chat.completions.create(**kwargs)

        if hasattr(response, "usage") and response.usage:
            tokens_used = getattr(response.usage, "total_tokens", 0)

        content = response.choices[0].message.content or ""
        code = self._extract_clean_code(content)
        return code, tokens_used

    def execute(self, code: str, working_dir: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        Execute generated python-docx code in a structured namespace.
        """
        if not code.strip():
            return False, "", "No code provided to execute"

        try:
            ast.parse(code)
        except SyntaxError as syn_err:
            return False, "", f"SyntaxError in generated code: {syn_err}"

        # Extract filename if present in code to provide resilient default doc
        filename_match = re.search(r"filename\s*=\s*['\"]([^'\"]+)['\"]", code)
        doc_inst = None
        if filename_match:
            fname = filename_match.group(1)
            if os.path.exists(fname):
                try:
                    doc_inst = Document(fname)
                except Exception:
                    doc_inst = Document()
            else:
                doc_inst = Document()

        # Setup namespace
        namespace: Dict[str, Any] = {
            "os": os,
            "re": re,
            "docx": docx,
            "Document": Document,
            "doc": doc_inst or Document(),
            "Inches": Inches,
            "Pt": Pt,
            "Cm": Cm,
            "Mm": Mm,
            "RGBColor": RGBColor,
            "WD_ALIGN_PARAGRAPH": WD_ALIGN_PARAGRAPH,
            "WD_TABLE_ALIGNMENT": WD_TABLE_ALIGNMENT,
            "parse_xml": parse_xml,
            "nsdecls": nsdecls,
            "Path": Path,
        }

        try:
            import openpyxl
            namespace["openpyxl"] = openpyxl
        except ImportError:
            pass

        try:
            import pptx
            namespace["pptx"] = pptx
        except ImportError:
            pass

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        try:
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                exec(code, namespace)

            captured_out = stdout_buf.getvalue().strip()
            captured_err = stderr_buf.getvalue().strip()
            output_msg = captured_out or "Code executed successfully"
            if captured_err:
                output_msg += f"\nStderr:\n{captured_err}"
            return True, output_msg, ""
        except Exception as e:
            err_details = traceback.format_exc()
            return False, stdout_buf.getvalue().strip(), f"Execution error: {e}\n{err_details}"
