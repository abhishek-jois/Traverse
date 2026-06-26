"""Go dependency extraction (regex — no compiler needed).

Captures single and grouped ``import`` declarations, top-level ``func`` and
``type`` definitions. A Go import path names a *package directory*; resolution
matches the import-path suffix against an internal directory and links to the
``.go`` files in it (see ``graph_builder._resolve_go``). Standard-library imports
(``fmt``, ``net/http`` …) simply never match an internal directory and drop out.
"""

from __future__ import annotations

import re

from . import ExtractResult, Import

_SINGLE_IMPORT_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
_IMPORT_BLOCK_RE = re.compile(r"import\s*\((.*?)\)", re.DOTALL)
_BLOCK_LINE_RE = re.compile(r'(?:([\w.]+)\s+)?"([^"]+)"')
_FUNC_RE = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", re.MULTILINE)
_TYPE_RE = re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface|func|\w)", re.MULTILINE)
_DOC_RE = re.compile(r"\A((?:\s*//[^\n]*\n)+)")


def _add(res: ExtractResult, path: str, alias: str | None) -> None:
    leaf = path.rsplit("/", 1)[-1]
    local = alias or leaf
    res.imports.append(Import(module=path, symbols=[leaf], local_names=[local]))


def extract(text: str) -> ExtractResult:
    res = ExtractResult()
    m = _DOC_RE.match(text)
    if m:
        res.docstring = " ".join(
            ln.lstrip("/ ").strip() for ln in m.group(1).splitlines()
        ).strip()

    block_spans: list[tuple[int, int]] = []
    for blk in _IMPORT_BLOCK_RE.finditer(text):
        block_spans.append(blk.span())
        for line in blk.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            lm = _BLOCK_LINE_RE.search(line)
            if lm:
                _add(res, lm.group(2), lm.group(1))

    for sm in _SINGLE_IMPORT_RE.finditer(text):
        # Skip single-import matches that fall inside an import(...) block.
        if any(a <= sm.start() < b for a, b in block_spans):
            continue
        _add(res, sm.group(1), None)

    for m in _FUNC_RE.finditer(text):
        res.defined_symbols.append(m.group(1))
    for m in _TYPE_RE.finditer(text):
        res.defined_symbols.append(m.group(1))

    counts: dict[str, int] = {}
    for imp in res.imports:
        for ln in imp.local_names:
            counts[ln] = max(0, len(re.findall(rf"\b{re.escape(ln)}\b", text)) - 1)
    res.usage_counts = counts
    return res
