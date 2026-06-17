"""One-line file descriptions.

Heuristic by default (docstring / leading comment / synthesised from structure)
so the tool works fully offline. ``llm.py`` can replace these with richer
AI-generated summaries when an API key is available.
"""

from __future__ import annotations

import re

from .extractors import ExtractResult
from .scanner import FileNode

_MAX_LEN = 140

_ROLE_PHRASES = {
    "controller": "Request controller",
    "route": "Route / endpoint definitions",
    "model": "Data model",
    "schema": "Schema definitions",
    "middleware": "Middleware",
    "service": "Service-layer logic",
    "repository": "Data-access repository",
    "component": "UI component",
    "view": "View / page",
    "hook": "Reusable hook",
    "util": "Utility helpers",
    "auth": "Authentication logic",
    "database": "Database access",
    "api": "API integration",
    "client": "Client wrapper",
    "types": "Type / interface definitions",
    "state": "State management",
    "config": "Configuration / constants",
    "test": "Test suite",
    "entrypoint": "Application entry point",
}


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    if not text:
        return ""
    # cut at first sentence boundary
    m = re.search(r"(.+?[.!?])(\s|$)", text)
    sentence = m.group(1) if m else text
    if len(sentence) > _MAX_LEN:
        sentence = sentence[: _MAX_LEN - 1].rstrip() + "…"
    return sentence.strip()


def heuristic_description(node: FileNode, res: ExtractResult) -> str:
    """Best-effort one-liner from docstring/comment or file structure."""
    if res.docstring:
        sent = _first_sentence(res.docstring)
        if sent:
            return sent

    role = _ROLE_PHRASES.get(node.file_type)
    symbols = res.defined_symbols[:3]
    sym_part = ""
    if symbols:
        sym_part = " defining " + ", ".join(symbols)
        if len(res.defined_symbols) > 3:
            sym_part += f" (+{len(res.defined_symbols) - 3} more)"

    if role:
        return f"{role}{sym_part}.".strip()

    lang = node.language.capitalize()
    if symbols:
        return f"{lang} module{sym_part}.".strip()
    return f"{lang} file ({node.file_type})."


def describe_all(nodes_with_extracts) -> None:
    """Populate ``node.description`` in place for each (node, extract) pair."""
    for node, res in nodes_with_extracts:
        if not node.description:
            node.description = heuristic_description(node, res)
