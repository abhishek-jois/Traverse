"""Optional AI-generated one-line descriptions via the Anthropic API.

Entirely optional: if the ``anthropic`` package is missing or ``ANTHROPIC_API_KEY``
is unset, the build silently keeps the heuristic descriptions. Results are cached
by content hash so we never pay to re-summarise an unchanged file.
"""

from __future__ import annotations

import json
import os

from . import store
from .scanner import FileNode, read_text

DEFAULT_MODEL = os.environ.get("DEPGRAPH_LLM_MODEL", "claude-haiku-4-5-20251001")
BATCH = 12


def is_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except Exception:
        return False


def _desc_cache_dir(root: str) -> str:
    d = os.path.join(store.cache_dir(root), "desc")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_read(root: str, sha: str) -> str | None:
    if not sha:
        return None
    p = os.path.join(_desc_cache_dir(root), f"{sha}.txt")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return None
    return None


def _cache_write(root: str, sha: str, text: str) -> None:
    if not sha:
        return
    with open(os.path.join(_desc_cache_dir(root), f"{sha}.txt"), "w", encoding="utf-8") as fh:
        fh.write(text.strip())


def _snippet(node: FileNode) -> str:
    text = read_text(node)
    lines = text.splitlines()[:40]
    return "\n".join(lines)[:1500]


def annotate(root: str, nodes: list[FileNode]) -> int:
    """Replace heuristic descriptions with LLM one-liners. Returns count updated."""
    if not is_available():
        return 0
    import anthropic

    client = anthropic.Anthropic()
    pending = []
    for node in nodes:
        cached = _cache_read(root, node.sha256)
        if cached:
            node.description = cached
        else:
            pending.append(node)

    updated = 0
    for i in range(0, len(pending), BATCH):
        chunk = pending[i:i + BATCH]
        files_blob = "\n\n".join(
            f"### FILE: {n.path}\n{_snippet(n)}" for n in chunk
        )
        prompt = (
            "For each file below, write ONE concise line (max 15 words) describing "
            "what the file does. Respond ONLY with a JSON object mapping the exact "
            "file path to its description.\n\n" + files_blob
        )
        try:
            msg = client.messages.create(
                model=DEFAULT_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            raw = raw[raw.find("{"): raw.rfind("}") + 1]
            mapping = json.loads(raw)
        except Exception:
            continue
        for n in chunk:
            desc = mapping.get(n.path)
            if isinstance(desc, str) and desc.strip():
                n.description = desc.strip()
                _cache_write(root, n.sha256, n.description)
                updated += 1
    return updated
