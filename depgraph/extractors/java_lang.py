"""Java dependency extraction (regex — no compiler needed).

Captures the package declaration, internal imports (``import a.b.C;``), top-level
type declarations (class/interface/enum/record) and ``extends``/``implements``
inheritance. Java package directories mirror import paths, so resolution is a
straightforward dotted-path → file match (see ``graph_builder._resolve_java``).
"""

from __future__ import annotations

import re

from . import ExtractResult, Import

_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", re.MULTILINE)
_TYPE_DECL_RE = re.compile(
    r"\b(?:public|private|protected|final|abstract|sealed|static|\s)*"
    r"\b(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)",
)
_EXTENDS_RE = re.compile(
    r"\b(?:class|interface)\s+[\w$]+(?:<[^>]*>)?\s+(?:extends|implements)\s+([\w$.,<>\s]+?)\s*\{",
)
_DOC_RE = re.compile(r"^\s*/\*\*?(.*?)\*/", re.DOTALL)


def extract(text: str) -> ExtractResult:
    res = ExtractResult()
    m = _DOC_RE.match(text)
    if m:
        res.docstring = " ".join(
            re.sub(r"^\s*\*\s?", "", ln).strip() for ln in m.group(1).splitlines()
        ).strip()

    binding_to_import: dict[str, Import] = {}
    for m in _IMPORT_RE.finditer(text):
        mod = m.group(1)
        # Drop the JDK / common third-party stdlib — never internal files.
        if mod.startswith(("java.", "javax.", "jakarta.")):
            continue
        leaf = mod.rsplit(".", 1)[-1]
        is_wild = leaf == "*"
        syms = [] if is_wild else [leaf]
        imp = Import(module=mod, symbols=syms, local_names=list(syms))
        res.imports.append(imp)
        if not is_wild:
            binding_to_import[leaf] = imp

    for m in _TYPE_DECL_RE.finditer(text):
        res.defined_symbols.append(m.group(2))

    for m in _EXTENDS_RE.finditer(text):
        for base in re.split(r"[,\s]+", m.group(1)):
            base = base.split("<")[0].split(".")[-1].strip()
            if base in binding_to_import:
                res.has_inheritance = True
                binding_to_import[base].base_classes.append(base)

    counts: dict[str, int] = {}
    for leaf in binding_to_import:
        counts[leaf] = max(0, len(re.findall(rf"\b{re.escape(leaf)}\b", text)) - 1)
    res.usage_counts = counts
    return res
