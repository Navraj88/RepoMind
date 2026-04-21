"""
code_graph.py
-------------
Builds and serves a lightweight code relationship graph for graph-based RAG.

Nodes:
  - chunk_id (code chunk)
  - symbol names (implicitly via symbol_to_chunks index)

Edges (chunk -> chunk):
  - call:    current chunk references symbol defined in target chunk
  - import:  current chunk likely imports target file/module
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")
_CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PY_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))")
_JS_IMPORT_RE = re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]")
_JS_REQUIRE_RE = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)")
_SKIP_CALL_WORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "await", "typeof",
    "print", "len", "str", "int", "float", "dict", "list", "set", "map", "filter",
}


class CodeGraphIndex:
    def __init__(self):
        self._graph_path = Path(__file__).resolve().parents[1] / ".cache" / "code_graph.json"
        self.clear()
        self._load_from_disk()

    def clear(self, persist: bool = False):
        self.repo = ""
        self.branch = ""
        self.symbol_to_chunks: dict[str, list[str]] = {}
        self.chunk_to_symbols: dict[str, list[str]] = {}
        self.chunk_to_path: dict[str, str] = {}
        self.path_to_chunks: dict[str, list[str]] = {}
        self.edges: dict[str, list[dict[str, Any]]] = {}
        if persist:
            self._save_to_disk()

    def _save_to_disk(self):
        self._graph_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo": self.repo,
            "branch": self.branch,
            "symbol_to_chunks": self.symbol_to_chunks,
            "chunk_to_symbols": self.chunk_to_symbols,
            "chunk_to_path": self.chunk_to_path,
            "path_to_chunks": self.path_to_chunks,
            "edges": self.edges,
        }
        self._graph_path.write_text(json.dumps(payload), encoding="utf-8")

    def _load_from_disk(self):
        if not self._graph_path.exists():
            return
        try:
            payload = json.loads(self._graph_path.read_text(encoding="utf-8"))
            self.repo = str(payload.get("repo", ""))
            self.branch = str(payload.get("branch", ""))
            self.symbol_to_chunks = {
                str(k): [str(x) for x in v]
                for k, v in dict(payload.get("symbol_to_chunks", {})).items()
            }
            self.chunk_to_symbols = {
                str(k): [str(x) for x in v]
                for k, v in dict(payload.get("chunk_to_symbols", {})).items()
            }
            self.chunk_to_path = {
                str(k): str(v) for k, v in dict(payload.get("chunk_to_path", {})).items()
            }
            self.path_to_chunks = {
                str(k): [str(x) for x in v]
                for k, v in dict(payload.get("path_to_chunks", {})).items()
            }
            self.edges = {
                str(k): list(v) for k, v in dict(payload.get("edges", {})).items()
            }
        except Exception:
            self.clear()

    @staticmethod
    def _extract_calls(content: str) -> list[str]:
        matches = []
        for m in _CALL_RE.finditer(content):
            sym = m.group(1)
            if sym in _SKIP_CALL_WORDS:
                continue
            if _SYMBOL_RE.match(sym):
                matches.append(sym)
        return list(dict.fromkeys(matches))

    @staticmethod
    def _extract_import_targets(content: str) -> list[str]:
        targets: list[str] = []
        for line in content.splitlines():
            py = _PY_IMPORT_RE.search(line)
            if py:
                mod = py.group(1) or py.group(2)
                if mod:
                    targets.append(mod)
            js = _JS_IMPORT_RE.search(line)
            if js:
                targets.append(js.group(1))
            req = _JS_REQUIRE_RE.search(line)
            if req:
                targets.append(req.group(1))
        return list(dict.fromkeys(targets))

    @staticmethod
    def _path_aliases(rel_path: str) -> set[str]:
        p = Path(rel_path)
        stem = p.stem
        no_ext = str(p).replace(p.suffix, "")
        aliases = {stem.lower(), no_ext.replace("\\", "/").lower()}
        aliases.add(str(p).replace("\\", "/").lower())
        return aliases

    def build_from_chunks(self, chunks: list[dict], repo: str = "", branch: str = ""):
        self.clear()
        self.repo = repo
        self.branch = branch

        symbol_to_chunks: dict[str, list[str]] = defaultdict(list)
        chunk_to_symbols: dict[str, list[str]] = {}
        chunk_to_path: dict[str, str] = {}
        path_to_chunks: dict[str, list[str]] = defaultdict(list)
        path_alias_to_chunks: dict[str, list[str]] = defaultdict(list)
        edges: dict[str, list[dict[str, Any]]] = defaultdict(list)

        calls_by_chunk: dict[str, list[str]] = {}
        imports_by_chunk: dict[str, list[str]] = {}

        for chunk in chunks:
            cid = str(chunk.get("chunk_id", ""))
            rel_path = str(chunk.get("rel_path", ""))
            content = str(chunk.get("content", ""))
            symbol = str(chunk.get("symbol_name", "")).strip()
            if not cid or not rel_path:
                continue

            chunk_to_path[cid] = rel_path
            path_to_chunks[rel_path].append(cid)

            for alias in self._path_aliases(rel_path):
                path_alias_to_chunks[alias].append(cid)

            symbols = []
            if symbol and _SYMBOL_RE.match(symbol):
                symbols.append(symbol)
                symbol_to_chunks[symbol].append(cid)
            chunk_to_symbols[cid] = symbols

            calls_by_chunk[cid] = self._extract_calls(content)
            imports_by_chunk[cid] = self._extract_import_targets(content)

        for cid, calls in calls_by_chunk.items():
            source_path = chunk_to_path.get(cid, "")
            for sym in calls:
                for target_id in symbol_to_chunks.get(sym, []):
                    if target_id == cid:
                        continue
                    target_path = chunk_to_path.get(target_id, "")
                    edges[cid].append({
                        "to": target_id,
                        "type": "call",
                        "symbol": sym,
                        "same_file": source_path == target_path,
                    })

        for cid, imports in imports_by_chunk.items():
            for mod in imports:
                norm = mod.strip().lower().replace("\\", "/")
                target_ids = path_alias_to_chunks.get(norm, [])
                # Heuristic: map package.module -> module.py stem
                if not target_ids and "." in norm:
                    tail = norm.split(".")[-1]
                    target_ids = path_alias_to_chunks.get(tail, [])
                for target_id in target_ids[:3]:
                    if target_id == cid:
                        continue
                    edges[cid].append({
                        "to": target_id,
                        "type": "import",
                        "module": mod,
                    })

        self.symbol_to_chunks = dict(symbol_to_chunks)
        self.chunk_to_symbols = chunk_to_symbols
        self.chunk_to_path = chunk_to_path
        self.path_to_chunks = dict(path_to_chunks)
        self.edges = dict(edges)
        self._save_to_disk()

    def graph_stats(self) -> dict[str, Any]:
        edge_count = sum(len(v) for v in self.edges.values())
        return {
            "repo": self.repo,
            "branch": self.branch,
            "nodes": len(self.chunk_to_path),
            "edges": edge_count,
            "symbols": len(self.symbol_to_chunks),
        }

    def expand_related_chunk_ids(
        self,
        seed_chunk_ids: list[str],
        seed_symbols: list[str] | None = None,
        max_depth: int = 2,
        max_nodes: int = 24,
    ) -> list[str]:
        if not seed_chunk_ids and not seed_symbols:
            return []

        queue = deque()
        dist: dict[str, int] = {}

        for cid in seed_chunk_ids:
            if cid in self.chunk_to_path:
                dist[cid] = 0
                queue.append(cid)

        for sym in seed_symbols or []:
            for cid in self.symbol_to_chunks.get(sym, []):
                if cid not in dist:
                    dist[cid] = 0
                    queue.append(cid)

        while queue and len(dist) < max_nodes:
            cur = queue.popleft()
            cur_depth = dist[cur]
            if cur_depth >= max_depth:
                continue
            for edge in self.edges.get(cur, []):
                nxt = str(edge.get("to", ""))
                if not nxt or nxt in dist:
                    continue
                dist[nxt] = cur_depth + 1
                queue.append(nxt)
                if len(dist) >= max_nodes:
                    break

        ordered = sorted(dist.items(), key=lambda x: (x[1], x[0]))
        return [cid for cid, _ in ordered if cid not in seed_chunk_ids]


graph_index = CodeGraphIndex()

