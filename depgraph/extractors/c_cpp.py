"""C / C++ dependency extraction (regex — no compiler needed).

Captures quoted ``#include "..."`` directives (the internal, project-relative
ones), top-level definitions (functions, classes, structs, enums, unions,
namespaces) and the presence of inheritance. Angle-bracket includes
(``#include <vector>``) name system/third-party headers and are dropped, exactly
as stdlib imports are dropped for the other languages. Include paths are resolved
against the file tree in ``graph_builder._resolve_c``.
"""

from __future__ import annotations

import re

from . import ExtractResult, Import

# #include "path/to/header.h"  — quoted form is the internal/project include.
_INC_QUOTED_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)

# Type-like definitions: class / struct / enum [class] / union / namespace Name.
_TYPE_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?"
    r"(?:class|struct|union|namespace|enum(?:\s+class)?)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)

# Function *definitions* (with a body ``{``), not declarations ending in ``;``.
# Captures the last identifier before the parameter list, so ``Foo::bar`` -> bar.
_FUNC_RE = re.compile(
    r"^[A-Za-z_][\w\s\*&:<>,~]*?\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?\{",
    re.MULTILINE,
)

# ``class X : public Base`` / ``struct X : Base`` — inheritance present.
_INHERIT_RE = re.compile(r"\b(?:class|struct)\s+[A-Za-z_]\w*\s*:\s*[^{;]*\{")

# Leading comment block (/* ... */ or a run of // lines) as the file's docstring.
_BLOCK_DOC_RE = re.compile(r"\A\s*/\*[*!]?(.*?)\*/", re.DOTALL)
_LINE_DOC_RE = re.compile(r"\A((?:\s*//[^\n]*\n)+)")

# Keywords that _FUNC_RE would otherwise mistake for a function name.
_NOT_FUNCS = {
    "if", "for", "while", "switch", "return", "sizeof", "catch", "do", "else",
    "case", "new", "delete", "throw", "and", "or", "not",
}


def _leading_doc(text: str) -> str:
    m = _BLOCK_DOC_RE.match(text)
    if m:
        body = m.group(1)
        lines = [re.sub(r"^\s*\*\s?", "", ln).strip() for ln in body.splitlines()]
        return " ".join(ln for ln in lines if ln).strip()
    m = _LINE_DOC_RE.match(text)
    if m:
        lines = [ln.lstrip("/ ").strip() for ln in m.group(1).splitlines()]
        return " ".join(ln for ln in lines if ln).strip()
    return ""


def extract(text: str) -> ExtractResult:
    res = ExtractResult()
    res.docstring = _leading_doc(text)

    for m in _INC_QUOTED_RE.finditer(text):
        inc = m.group(1).strip()
        if inc:
            res.imports.append(Import(module=inc, is_relative=True))

    for m in _TYPE_RE.finditer(text):
        res.defined_symbols.append(m.group(1))
    for m in _FUNC_RE.finditer(text):
        name = m.group(1)
        if name not in _NOT_FUNCS:
            res.defined_symbols.append(name)

    res.has_inheritance = bool(_INHERIT_RE.search(text))
    return res
