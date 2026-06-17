"""Persistence: graph JSON + per-file extraction cache.

The graph is stored as NetworkX node-link JSON. The cache stores each file's
:class:`ExtractResult` keyed by content hash, so an incremental rebuild only
re-parses files whose ``sha256`` changed.
"""

from __future__ import annotations

import json
import os
import time

import networkx as nx

from .extractors import ExtractResult, Import

OUT_DIRNAME = ".depgraph"


def out_dir(root: str) -> str:
    return os.path.join(os.path.abspath(root), OUT_DIRNAME)


def cache_dir(root: str) -> str:
    return os.path.join(out_dir(root), "cache")


def ensure_dirs(root: str) -> None:
    os.makedirs(cache_dir(root), exist_ok=True)


# --------------------------------------------------------------------------
# Graph
# --------------------------------------------------------------------------

def save_graph(g: nx.DiGraph, root: str, *, meta: dict | None = None) -> str:
    ensure_dirs(root)
    data = nx.node_link_data(g, edges="links")
    data["meta"] = {
        "root": os.path.abspath(root),
        "generated_at": time.time(),
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        **(meta or {}),
    }
    path = os.path.join(out_dir(root), "graph.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return path


def load_graph(root: str) -> tuple[nx.DiGraph, dict]:
    path = os.path.join(out_dir(root), "graph.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    meta = data.pop("meta", {})
    g = nx.node_link_graph(data, directed=True, edges="links")
    return g, meta


def graph_exists(root: str) -> bool:
    return os.path.exists(os.path.join(out_dir(root), "graph.json"))


# --------------------------------------------------------------------------
# Extraction cache
# --------------------------------------------------------------------------

def _result_to_dict(res: ExtractResult) -> dict:
    return {
        "imports": [imp.__dict__ for imp in res.imports],
        "defined_symbols": res.defined_symbols,
        "usage_counts": res.usage_counts,
        "docstring": res.docstring,
        "has_inheritance": res.has_inheritance,
        "error": res.error,
    }


def _result_from_dict(d: dict) -> ExtractResult:
    res = ExtractResult(
        imports=[Import(**imp) for imp in d.get("imports", [])],
        defined_symbols=d.get("defined_symbols", []),
        usage_counts=d.get("usage_counts", {}),
        docstring=d.get("docstring", ""),
        has_inheritance=d.get("has_inheritance", False),
        error=d.get("error", ""),
    )
    return res


def cache_get(root: str, sha256: str) -> ExtractResult | None:
    if not sha256:
        return None
    path = os.path.join(cache_dir(root), f"{sha256}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return _result_from_dict(json.load(fh))
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(root: str, sha256: str, res: ExtractResult) -> None:
    if not sha256:
        return
    ensure_dirs(root)
    path = os.path.join(cache_dir(root), f"{sha256}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_result_to_dict(res), fh)
