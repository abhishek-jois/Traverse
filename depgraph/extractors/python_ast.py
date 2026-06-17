"""Python dependency extraction using the standard-library ``ast`` module.

This is the most accurate extractor we have because Python ships its own
parser. We capture imports (with the local names they bind), class base
classes, how often each imported name is referenced, and the module docstring.
"""

from __future__ import annotations

import ast

from . import ExtractResult, Import


def extract(text: str) -> ExtractResult:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return ExtractResult(error=f"syntax: {exc}")

    res = ExtractResult()
    res.docstring = (ast.get_docstring(tree) or "").strip()

    # local bound name -> Import object, so we can attribute usage and bases.
    binding_to_import: dict[str, Import] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imp = Import(module=alias.name, level=0, symbols=[],
                             local_names=[local], is_relative=False)
                res.imports.append(imp)
                binding_to_import[local] = imp
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imp = Import(module=module, level=node.level or 0,
                         is_relative=(node.level or 0) > 0)
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                imp.symbols.append(alias.name)
                imp.local_names.append(local)
                binding_to_import[local] = imp
            res.imports.append(imp)

    # Top-level definitions (the file's "exports") and inheritance.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            res.defined_symbols.append(node.name)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    res.defined_symbols.append(tgt.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = _name_of(base)
                if base_name and base_name in binding_to_import:
                    res.has_inheritance = True
                    binding_to_import[base_name].base_classes.append(base_name)

    # Usage counts: every Name load referencing an imported binding.
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in binding_to_import:
                counts[node.id] = counts.get(node.id, 0) + 1
        elif isinstance(node, ast.Attribute):
            root = _root_name(node)
            if root and root in binding_to_import:
                counts[root] = counts.get(root, 0) + 1
    res.usage_counts = counts
    return res


def _name_of(node: ast.expr) -> str | None:
    """Get the leftmost identifier of a base-class expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node)
    return None


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None
