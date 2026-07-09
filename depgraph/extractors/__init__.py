"""Per-file dependency extraction.

Each extractor turns one file's source into a language-agnostic
:class:`ExtractResult` describing what it imports, what it defines, and how
heavily it uses each imported name. ``graph_builder`` later resolves the raw
import strings into edges between actual file nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..scanner import FileNode, read_text


@dataclass
class Import:
    """A single import/require statement, before cross-file resolution."""

    module: str                                   # raw target, e.g. ".models", "../db", "express"
    level: int = 0                                # number of leading dots (relative depth)
    symbols: list[str] = field(default_factory=list)      # imported names ([] for bare import)
    local_names: list[str] = field(default_factory=list)  # names bound in this file
    is_relative: bool = False
    base_classes: list[str] = field(default_factory=list) # imported names used as a class base
    is_reexport: bool = False


@dataclass
class ExtractResult:
    imports: list[Import] = field(default_factory=list)
    defined_symbols: list[str] = field(default_factory=list)   # classes/functions/exports
    usage_counts: dict[str, int] = field(default_factory=dict)  # local name -> reference count
    docstring: str = ""                                         # module docstring / leading comment
    has_inheritance: bool = False
    error: str = ""                                            # non-empty if parsing failed
    http_routes: list[str] = field(default_factory=list)       # HTTP paths this file exposes
    http_calls: list[str] = field(default_factory=list)        # HTTP URLs this file calls


def extract(node: FileNode, text: str | None = None) -> ExtractResult:
    """Dispatch to the right extractor based on the file's language."""
    if text is None:
        text = read_text(node)
    if not text.strip():
        return ExtractResult()

    if node.language == "python":
        from . import python_ast
        return python_ast.extract(text)
    if node.language in ("javascript", "typescript"):
        from . import js_ts
        return js_ts.extract(text)
    if node.language == "go":
        from . import go_lang
        return go_lang.extract(text)
    if node.language == "rust":
        from . import rust_lang
        return rust_lang.extract(text)
    if node.language == "java":
        from . import java_lang
        return java_lang.extract(text)
    if node.language in ("c", "cpp"):
        from . import c_cpp
        return c_cpp.extract(text)
    return ExtractResult()
