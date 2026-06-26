"""Query-scoped symbol slicing — turn a selected file into a compact "answer pack".

The retrieval graph tells us *which* files matter for a query. But handing the
agent a list of file paths costs one ``Read`` turn per file, and each whole file
then re-processes as cache-read on every subsequent turn. The A/B runs showed
this turn-and-reprocess overhead — not fresh context — is what makes the graph
lose on small/medium repos.

This module closes that gap: for each selected file we find the top-level symbols
most relevant to the query and return *just those slices* inline (plus a one-line
outline of the rest and the file header). The agent can answer without extra
reads, and the inlined payload is small enough to stay cheap even when it
re-processes each turn.

Symbol extraction is language-aware:
  * Python uses the stdlib ``ast`` (exact).
  * Brace-family languages (JS/TS/Java/C/C++/C#/Go/Rust/Swift/Kotlin/PHP/Scala …)
    use a dependency-free brace matcher — good enough to bound a slice, and the
    outline always carries line ranges so the agent can read the exact range if a
    slice looks off. (Tree-sitter would be more precise; it can be slotted into
    ``_symbols_for`` later without touching the rest of the pipeline.)

Anything we cannot parse returns ``None`` — the caller falls back to listing the
path so the agent reads it the old way.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .retrieve import STOPWORDS, tokenize

CHARS_PER_TOKEN = 4
HEADER_LINES = 20          # how many leading lines (imports / module setup) to show

_PY_EXTS = (".py", ".pyi")
_BRACE_EXTS = (
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".java", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".cs", ".go", ".rs", ".swift", ".kt", ".kts", ".scala",
    ".php", ".m", ".mm",
)


def est_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _query_terms(query: str) -> set[str]:
    terms = set(tokenize(query)) - STOPWORDS
    return terms or set(tokenize(query))


@dataclass
class _Sym:
    name: str
    kind: str        # "def" | "async def" | "class" | "fn" | "class"/"struct"/… (brace)
    start: int       # 1-based, includes decorators so the slice is self-contained
    end: int


def _score(sym: _Sym, qterms: set[str]) -> int:
    return len(set(tokenize(sym.name)) & qterms)


# --------------------------------------------------------------------------
# Python (exact, via ast)
# --------------------------------------------------------------------------

def _python_symbols(text: str) -> list[_Sym] | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    syms: list[_Sym] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min(start, min(d.lineno for d in node.decorator_list))
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        kind = ("class" if isinstance(node, ast.ClassDef)
                else "async def" if isinstance(node, ast.AsyncFunctionDef)
                else "def")
        syms.append(_Sym(node.name, kind, start, end))
    return syms


# --------------------------------------------------------------------------
# Brace-family languages (heuristic, dependency-free)
# --------------------------------------------------------------------------

# Lines that open a block but are NOT definitions.
_CTRL_RE = re.compile(
    r"^\s*(if|for|while|switch|catch|else|do|try|finally|return|case|default|with|when|guard)\b"
)
# An explicit definition keyword anywhere on the line.
_DEFKW_RE = re.compile(
    r"\b(function|class|interface|struct|enum|trait|impl|namespace|module|func|fn|def|record|object)\b"
)
# A callable-looking signature: identifier immediately followed by "(".
_NAMECALL_RE = re.compile(r"[A-Za-z_$][\w$]*\s*\(")
# An arrow function (JS/TS): `const x = (...) => {` / `(...) => {`.
_ARROW_RE = re.compile(r"=>")
_KIND_RE = re.compile(r"\b(class|interface|struct|enum|trait|impl|namespace|module|record|object)\b")
_NAME_AFTER_KW_RE = re.compile(
    r"\b(?:function|class|interface|struct|enum|trait|impl|namespace|module|func|fn|def|record|object|type)\s+([A-Za-z_$][\w$]*)"
)
_NAME_BEFORE_PAREN_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_NAME_ASSIGN_RE = re.compile(r"\b(?:const|let|var|val)\s+([A-Za-z_$][\w$]*)\s*=")
_COMMENT_PREFIXES = ("//", "#", "*", "/*", "<!--")

# Words that are language keywords / common types, never a real symbol name —
# used to skip false "name before (" matches like Go's `func (s *T) Method()`.
_KEYWORDS = frozenset({
    "function", "func", "fn", "def", "class", "interface", "struct", "enum",
    "trait", "impl", "namespace", "module", "record", "object", "type",
    "return", "if", "for", "while", "switch", "new", "await", "async", "yield",
    "public", "private", "protected", "internal", "static", "final", "abstract",
    "const", "let", "var", "val", "export", "default", "void", "int", "string",
    "bool", "float", "double", "char", "long", "short", "byte", "unsigned",
})


def _brace_name(line: str) -> str:
    m = _NAME_AFTER_KW_RE.search(line)        # `class Foo`, `func Foo`, `type Foo`
    if m:
        return m.group(1)
    m = _NAME_ASSIGN_RE.search(line)          # `const foo = ... =>`
    if m:
        return m.group(1)
    for m in _NAME_BEFORE_PAREN_RE.finditer(line):   # first non-keyword `name(`
        if m.group(1) not in _KEYWORDS:
            return m.group(1)
    return "?"


def _brace_kind(line: str) -> str:
    m = _KIND_RE.search(line)
    return m.group(1) if m else "fn"


def _block_end(lines: list[str], start: int, lookahead: int = 5) -> int | None:
    """Return the 0-based index of the line closing the block that opens at/after
    ``start``, or ``None`` if no ``{`` block opens within ``lookahead`` lines."""
    brace_line = None
    for k in range(start, min(start + lookahead + 1, len(lines))):
        if "{" in lines[k]:
            brace_line = k
            break
    if brace_line is None:
        return None
    depth = 0
    for k in range(brace_line, len(lines)):
        depth += lines[k].count("{") - lines[k].count("}")
        if depth <= 0:
            return k
    return len(lines) - 1


def _brace_symbols(text: str) -> list[_Sym] | None:
    lines = text.split("\n")
    syms: list[_Sym] = []
    depth = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.lstrip()
        is_code = stripped and not stripped.startswith(_COMMENT_PREFIXES)
        if (depth == 0 and is_code and not _CTRL_RE.match(line)
                and (_DEFKW_RE.search(line) or _NAMECALL_RE.search(line)
                     or _ARROW_RE.search(line))):
            end = _block_end(lines, i)
            if end is not None and end >= i:
                syms.append(_Sym(_brace_name(line), _brace_kind(line), i + 1, end + 1))
                i = end + 1          # a balanced top-level block nets depth 0
                continue
        depth += line.count("{") - line.count("}")
        if depth < 0:
            depth = 0
        i += 1
    return syms or None


def _symbols_for(abspath: str, text: str) -> list[_Sym] | None:
    low = abspath.lower()
    if low.endswith(_PY_EXTS):
        return _python_symbols(text)
    if low.endswith(_BRACE_EXTS):
        return _brace_symbols(text)
    return None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

@dataclass
class SliceResult:
    text: str          # the formatted block to inline (no path header)
    tokens: int        # estimated token cost of ``text``
    mode: str          # "whole" | "sliced"


def slice_file(abspath: str, query: str, budget_tokens: int) -> SliceResult | None:
    """Return an inline answer-pack block for ``abspath``, or ``None`` to fall back.

    If the whole file fits in ``budget_tokens`` it is returned verbatim. Otherwise
    we return the file header plus the query-relevant symbol bodies that fit the
    budget, with a one-line outline of every top-level symbol so the agent can read
    a specific range if a slice looks incomplete.
    """
    try:
        with open(abspath, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    if not text.strip():
        return None

    total = est_tokens(text)
    if total <= budget_tokens:
        return SliceResult(text=text, tokens=total, mode="whole")

    syms = _symbols_for(abspath, text)
    if not syms:
        return None

    lines = text.splitlines()
    qterms = _query_terms(query)

    # Header: imports / module-level setup above the first symbol, capped.
    first_start = min(s.start for s in syms)
    head_end = min(first_start - 1, HEADER_LINES)
    header = "\n".join(lines[:head_end]) if head_end > 0 else ""

    # Reserve budget for the non-body framing (the outline line, the header, and
    # the per-symbol markers) so the *total* block stays within budget.
    outline_base = " ".join(f"{s.name}[{s.start}-{s.end}]" for s in syms)
    used = est_tokens(header) + est_tokens(outline_base) + 60

    # Most query-relevant symbols first; ties broken toward smaller bodies so the
    # budget covers more of them. A zero-score symbol is only included if nothing
    # else matched (so the agent still gets *something* substantive).
    ranked = sorted(syms, key=lambda s: (_score(s, qterms), -(s.end - s.start)),
                    reverse=True)

    # (symbol, body_text, truncated?) for each chosen slice.
    chosen: list[tuple[_Sym, str, bool]] = []
    chosen_names: set[str] = set()
    for s in ranked:
        if _score(s, qterms) == 0 and chosen:
            break
        avail = budget_tokens - used
        if avail <= 0:
            break
        seg_lines = lines[s.start - 1:s.end]
        seg = "\n".join(seg_lines)
        seg_tok = est_tokens(seg)
        truncated = False
        if seg_tok > avail:
            if chosen:
                continue                 # too big now — try a smaller symbol instead
            # Lead symbol bigger than the whole budget: keep a head of it that
            # fits and point at the line range for the rest, rather than blowing
            # the budget on one giant body.
            head_lines: list[str] = []
            t = 0
            for ln in seg_lines:
                lt = est_tokens(ln)
                if head_lines and t + lt > avail:
                    break
                head_lines.append(ln)
                t += lt
            seg = "\n".join(head_lines) + (
                f"\n    # … truncated — read L{s.start}-{s.end} for the full body")
            seg_tok = est_tokens(seg)
            truncated = True
        chosen.append((s, seg, truncated))
        chosen_names.add(s.name)
        used += seg_tok
        if used >= budget_tokens:
            break

    if not chosen:
        return None                      # nothing useful to inline — let caller list the path

    outline = " ".join(
        (f"{s.name}✓[{s.start}-{s.end}]" if s.name in chosen_names
         else f"{s.name}[{s.start}-{s.end}]")
        for s in sorted(syms, key=lambda s: s.start)
    )

    block: list[str] = [f"outline ({len(lines)} lines, ✓=shown): {outline}"]
    if header:
        block.append(f"# head [L1-{head_end}]:\n{header}")
    for s, seg, truncated in sorted(chosen, key=lambda c: c[0].start):
        tag = " (truncated)" if truncated else ""
        block.append(f"# {s.kind} {s.name} [L{s.start}-{s.end}]{tag}:\n{seg}")

    out = "\n".join(block)
    return SliceResult(text=out, tokens=est_tokens(out), mode="sliced")
