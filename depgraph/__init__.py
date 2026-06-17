"""Dependency Graph Retrieval — smarter context for smarter code.

Build a lightweight weighted dependency graph of a codebase where nodes hold
file *metadata* (not content) and edges hold weighted relationships. A query
traverses the graph to load only the handful of files that matter — so the AI
always knows every file exists, but only loads what is relevant.

See the architecture spec in ``input.md``.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
