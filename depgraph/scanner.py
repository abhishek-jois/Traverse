"""Repository scanner.

Walks a project directory and produces one *node record* per source file. Each
record carries only lightweight metadata (path, type, size, mtime, content hash)
— never the full content. This is what keeps the graph cheap to hold in context.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from typing import Iterable

# Directories we never descend into.
EXCLUDED_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules", "bower_components",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "venv", ".venv", "env", ".env.d", "virtualenv",
    "site-packages", "dist-packages", ".tox", ".eggs",
    "vendor", "vendors", "tmp", "temp", "logs",
    "dist", "build", "out", ".next", ".nuxt", "target",
    ".idea", ".vscode", ".gradle",
    "coverage", ".nyc_output",
    "migrations", "alembic",  # auto-generated DB migration files
    ".depgraph",  # our own output
}

# Files that are noise even though they look like source.
EXCLUDED_FILE_GLOBS = [
    "*.min.js", "*.min.css", "*.map",
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "*.pyc", "*.pyo", "*.so", "*.o", "*.a", "*.dll", "*.dylib",
    "*.d.ts",  # type stubs add edges but little meaning
    "*_pb2.py", "*_pb2_grpc.py",           # protobuf generated files
    "*.generated.ts", "*.generated.js",    # code-gen output
]

# Extensions we parse for dependencies (the "code" languages).
CODE_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".h++": "cpp",
}

# Extensions we still track as nodes (so they are *visible*) but do not parse.
CONFIG_EXTENSIONS = {
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".conf",
}
DOC_EXTENSIONS = {".md", ".rst", ".txt"}

# A generous cap so we never try to AST-parse a giant generated file.
MAX_PARSE_BYTES = 1_500_000


@dataclass
class FileNode:
    """Lightweight metadata about a single file — the graph's node payload."""

    path: str                       # repo-relative, forward-slash normalised
    abspath: str
    language: str                   # python | javascript | typescript | config | doc | other
    file_type: str = "other"        # semantic role: controller, model, config, ...
    size: int = 0
    mtime: float = 0.0
    sha256: str = ""
    description: str = ""           # filled in later by describe.py
    always_include: bool = False    # cross-cutting (config/env/constants)
    symbols: list[str] = field(default_factory=list)    # exported/defined names
    http_routes: list[str] = field(default_factory=list)  # HTTP paths this file exposes
    http_calls: list[str] = field(default_factory=list)   # HTTP URLs this file calls

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "file_type": self.file_type,
            "size": self.size,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "description": self.description,
            "always_include": self.always_include,
            "symbols": self.symbols,
            "http_routes": self.http_routes,
            "http_calls": self.http_calls,
        }


def _is_excluded_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in EXCLUDED_FILE_GLOBS)


def _looks_binary(abspath: str) -> bool:
    """Cheap binary sniff: a NUL byte in the first 1KB."""
    try:
        with open(abspath, "rb") as fh:
            return b"\x00" in fh.read(1024)
    except OSError:
        return True


def classify_file_type(rel_path: str) -> tuple[str, bool]:
    """Infer a semantic role for a file and whether it is cross-cutting.

    Returns ``(file_type, always_include)``. Heuristics are deliberately simple
    and path/name based — good enough to colour the graph and bias retrieval.
    """
    p = rel_path.lower()
    name = os.path.basename(p)

    # Cross-cutting config / constants. Only true config-format files are
    # always-include; *code* files with config-ish names (settings_get.py is
    # an API endpoint) are typed "config" but not force-loaded.
    config_markers = ("config", "settings", "constants", "env", ".env",
                      "secrets", "credentials")
    if name.startswith(".env") or os.path.splitext(name)[1] in CONFIG_EXTENSIONS:
        return "config", True
    if any(m in name for m in config_markers):
        return "config", False

    # Tests.
    if (name.startswith("test_") or name.endswith("_test.py")
            or ".test." in name or ".spec." in name
            or "/tests/" in f"/{p}" or "/__tests__/" in f"/{p}"):
        return "test", False

    # Common architectural roles by directory or name fragment.
    role_markers = [
        ("controller", "controller"),
        ("route", "route"), ("router", "route"), ("endpoint", "route"),
        ("model", "model"), ("entity", "model"), ("schema", "schema"),
        ("middleware", "middleware"),
        ("service", "service"),
        ("repository", "repository"), ("dao", "repository"),
        ("component", "component"), ("view", "view"), ("page", "view"),
        ("hook", "hook"),
        ("util", "util"), ("helper", "util"), ("lib", "util"),
        ("auth", "auth"),
        ("db", "database"), ("database", "database"), ("migration", "database"),
        ("api", "api"), ("client", "client"),
        ("type", "types"), ("interface", "types"),
        ("store", "state"), ("reducer", "state"), ("context", "state"),
    ]
    surrounded = f"/{p}"
    for marker, role in role_markers:
        if marker in name or f"/{marker}" in surrounded or f"{marker}s/" in surrounded:
            return role, False

    # Entry points.
    if name in ("main.py", "app.py", "index.js", "index.ts", "server.py",
                "server.js", "server.ts", "__main__.py", "manage.py", "cli.py"):
        return "entrypoint", False

    ext = os.path.splitext(name)[1]
    if ext in DOC_EXTENSIONS:
        return "doc", False
    return "other", False


def _hash_and_stat(abspath: str) -> tuple[str, int, float]:
    h = hashlib.sha256()
    size = 0
    try:
        with open(abspath, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
                size += len(chunk)
        mtime = os.path.getmtime(abspath)
    except OSError:
        return "", 0, 0.0
    return h.hexdigest(), size, mtime


def _stat_only(abspath: str) -> tuple[int, float]:
    """Size + mtime without reading the file — the fast path for sync."""
    try:
        st = os.stat(abspath)
    except OSError:
        return 0, 0.0
    return st.st_size, st.st_mtime


def fill_hash(node: FileNode) -> FileNode:
    """Compute and store the sha256 (and exact size) for a stat-only node."""
    node.sha256, node.size, node.mtime = _hash_and_stat(node.abspath)
    return node


def language_for(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    if ext in CODE_EXTENSIONS:
        return CODE_EXTENSIONS[ext]
    if ext in CONFIG_EXTENSIONS:
        return "config"
    if ext in DOC_EXTENSIONS:
        return "doc"
    return None


def scan(root: str, *, include_docs: bool = False,
         errors: list[str] | None = None,
         stat_only: bool = False) -> list[FileNode]:
    """Walk ``root`` and return a :class:`FileNode` per tracked file.

    Code files (py/js/ts) are always included. Config files are included
    because they are cross-cutting and must stay *visible*. Docs are skipped
    unless ``include_docs`` is set. Unreadable directories are recorded in
    ``errors`` (if given) so they never fail silently.

    With ``stat_only`` the files are not read or hashed — only size+mtime are
    recorded (``sha256`` left blank). This is the cheap sweep used by the
    incremental sync to discover which files actually changed.
    """
    root = os.path.abspath(root)
    nodes: list[FileNode] = []

    def _on_error(exc: OSError) -> None:
        if errors is not None:
            errors.append(f"{exc.filename}: {exc.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error):
        # Prune excluded / hidden directories in place. A `pyvenv.cfg` marks a
        # virtualenv regardless of its name (e.g. `tmp/fastapi_poc_env/`).
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDED_DIRS
            and not (d.startswith(".") and d != ".")
            and not os.path.exists(os.path.join(dirpath, d, "pyvenv.cfg"))
        ]
        for fname in filenames:
            if _is_excluded_file(fname):
                continue
            lang = language_for(fname)
            if lang is None:
                continue
            if lang == "doc" and not include_docs:
                continue

            abspath = os.path.join(dirpath, fname)
            if os.path.islink(abspath):
                continue
            if lang in ("python", "javascript", "typescript") and _looks_binary(abspath):
                continue

            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            file_type, always = classify_file_type(rel)
            if stat_only:
                sha = ""
                size, mtime = _stat_only(abspath)
            else:
                sha, size, mtime = _hash_and_stat(abspath)
            nodes.append(FileNode(
                path=rel,
                abspath=abspath,
                language=lang,
                file_type=file_type,
                size=size,
                mtime=mtime,
                sha256=sha,
                always_include=always,
            ))

    nodes.sort(key=lambda n: n.path)
    return nodes


def read_text(node: FileNode) -> str:
    """Read a file's text, tolerating encoding issues and oversized files."""
    if node.size > MAX_PARSE_BYTES:
        return ""
    try:
        with open(node.abspath, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""
