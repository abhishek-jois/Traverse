"""Rust dependency extraction (regex — no compiler needed).

Two edge sources:
  * ``mod foo;`` — a file-backed submodule declaration. Resolves to the sibling
    ``foo.rs`` or ``foo/mod.rs`` (strong, file-level).
  * ``use crate::a::b`` / ``use super::a`` — path references into the crate.
    Resolved best-effort by matching the path suffix against a file.

A ``mod`` declaration is encoded as an ``Import`` whose ``module`` is a bare
identifier (no ``::``); a ``use`` is encoded with its full ``::`` path. The
resolver (``graph_builder._resolve_rust``) distinguishes them by that.
"""

from __future__ import annotations

import re

from . import ExtractResult, Import

_MOD_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+([A-Za-z_]\w*)\s*;", re.MULTILINE)
_USE_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?use\s+([A-Za-z_][\w:]*)", re.MULTILINE)
_FN_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_]\w*)", re.MULTILINE)
_TYPE_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_]\w*)", re.MULTILINE)
_DOC_RE = re.compile(r"\A((?:\s*//[/!][^\n]*\n)+)")


def extract(text: str) -> ExtractResult:
    res = ExtractResult()
    m = _DOC_RE.match(text)
    if m:
        res.docstring = " ".join(
            ln.lstrip("/!  ").strip() for ln in m.group(1).splitlines()
        ).strip()

    for m in _MOD_RE.finditer(text):
        name = m.group(1)
        res.imports.append(Import(module=name, is_relative=True,
                                  symbols=[name], local_names=[name]))

    for m in _USE_RE.finditer(text):
        path = m.group(1)
        # Only crate-internal references can resolve to internal files.
        if path.split("::", 1)[0] not in ("crate", "super", "self"):
            continue
        leaf = path.rsplit("::", 1)[-1]
        res.imports.append(Import(module=path, is_relative=True,
                                  symbols=[leaf], local_names=[leaf]))

    for m in _FN_RE.finditer(text):
        res.defined_symbols.append(m.group(1))
    for m in _TYPE_RE.finditer(text):
        res.defined_symbols.append(m.group(1))

    counts: dict[str, int] = {}
    for imp in res.imports:
        for ln in imp.local_names:
            counts[ln] = max(0, len(re.findall(rf"\b{re.escape(ln)}\b", text)) - 1)
    res.usage_counts = counts
    return res
