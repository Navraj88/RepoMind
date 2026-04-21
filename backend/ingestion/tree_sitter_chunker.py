"""
tree_sitter_chunker.py
----------------------
Uses Tree-sitter to parse source files into an AST and extract
semantically meaningful chunks (functions, classes, methods, etc.).

Strategy:
  1. Parse the file with the appropriate language grammar.
  2. Walk the AST looking for "top-level declaration" node types.
  3. For each declaration, extract its source text + line range.
  4. If a declaration is too large (> MAX_CHUNK_LINES), recursively
     split it by its child declarations, then fall back to line-window
     splitting if still too large.
  5. Tiny leaf nodes (< MIN_CHUNK_LINES) are merged with siblings.
  6. Attach rich metadata to every chunk for later retrieval.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional

try:
    from tree_sitter import Language, Parser
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
    import tree_sitter_java as tsjava
    import tree_sitter_go as tsgo
    import tree_sitter_rust as tsrust
    import tree_sitter_cpp as tscpp
    import tree_sitter_c as tsc
    import tree_sitter_c_sharp as tscsharp
    import tree_sitter_ruby as tsruby
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

from config import MAX_CHUNK_LINES, MIN_CHUNK_LINES, CHUNK_OVERLAP_LINES


# ---------------------------------------------------------------------------
# Language → grammar binding
# ---------------------------------------------------------------------------

def _get_language(lang_name: str) -> Optional[object]:
    if not TREE_SITTER_AVAILABLE:
        return None
    mapping = {
        "python":     (tspython,     "python"),
        "javascript": (tsjavascript, "javascript"),
        "typescript": (tstypescript.language_typescript, None),  # special
        "java":       (tsjava,       "java"),
        "go":         (tsgo,         "go"),
        "rust":       (tsrust,       "rust"),
        "cpp":        (tscpp,        "cpp"),
        "c":          (tsc,          "c"),
        "c_sharp":    (tscsharp,     "c_sharp"),
        "ruby":       (tsruby,       "ruby"),
    }
    entry = mapping.get(lang_name)
    if entry is None:
        return None
    module, attr = entry
    if attr is None:
        # e.g. typescript exports the Language object directly
        return Language(module())
    return Language(module.language())


# Node types that represent "a meaningful top-level chunk" per language
_CHUNK_NODE_TYPES: dict[str, list[str]] = {
    "python": [
        "function_definition", "async_function_definition",
        "class_definition", "decorated_definition",
    ],
    "javascript": [
        "function_declaration", "arrow_function", "class_declaration",
        "method_definition", "export_statement",
        "lexical_declaration",   # const foo = () => {}
    ],
    "typescript": [
        "function_declaration", "arrow_function", "class_declaration",
        "method_definition", "interface_declaration",
        "type_alias_declaration", "export_statement",
        "lexical_declaration",
    ],
    "java": [
        "class_declaration", "interface_declaration",
        "method_declaration", "constructor_declaration",
        "enum_declaration", "annotation_type_declaration",
    ],
    "go": [
        "function_declaration", "method_declaration",
        "type_declaration", "short_var_declaration",
    ],
    "rust": [
        "function_item", "impl_item", "struct_item",
        "enum_item", "trait_item", "mod_item",
    ],
    "cpp": [
        "function_definition", "class_specifier",
        "struct_specifier", "namespace_definition",
    ],
    "c": [
        "function_definition", "struct_specifier",
        "typedef_declaration",
    ],
    "c_sharp": [
        "class_declaration", "interface_declaration",
        "method_declaration", "constructor_declaration",
        "namespace_declaration",
    ],
    "ruby": [
        "method", "singleton_method", "class", "module",
    ],
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CodeChunk:
    chunk_id: str           # SHA-256 of (rel_path + start_line)
    rel_path: str           # e.g. "src/utils.py"
    language: str           # grammar name
    content: str            # raw source text of the chunk
    start_line: int         # 1-indexed
    end_line: int           # 1-indexed, inclusive
    node_type: str          # AST node type or "window"
    symbol_name: str        # best-effort function/class name
    metadata: dict = field(default_factory=dict)

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    def to_dict(self) -> dict:
        return {
            "chunk_id":    self.chunk_id,
            "rel_path":    self.rel_path,
            "language":    self.language,
            "content":     self.content,
            "start_line":  self.start_line,
            "end_line":    self.end_line,
            "node_type":   self.node_type,
            "symbol_name": self.symbol_name,
            **self.metadata,
        }


def _make_id(rel_path: str, start_line: int) -> str:
    raw = f"{rel_path}:{start_line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core chunker
# ---------------------------------------------------------------------------

class TreeSitterChunker:
    def __init__(self):
        self._parsers: dict[str, object] = {}

    def _get_parser(self, language: str):
        if language in self._parsers:
            return self._parsers[language]
        if not TREE_SITTER_AVAILABLE:
            return None
        lang = _get_language(language)
        if lang is None:
            return None
        parser = Parser(lang)
        self._parsers[language] = parser
        return parser

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def chunk_file(self, file_info: dict) -> List[CodeChunk]:
        """
        Main entry point.  *file_info* is a dict with keys:
          rel_path, language, content
        Returns a list of CodeChunk objects.
        """
        language = file_info["language"]
        content = file_info["content"]
        rel_path = file_info["rel_path"]

        # Markdown / plain text → simple window chunking, no AST needed
        if language == "markdown":
            return self._window_chunks(content, rel_path, language)

        parser = self._get_parser(language)
        if parser is None:
            # Tree-sitter not installed for this language → fall back
            return self._window_chunks(content, rel_path, language)

        tree = parser.parse(bytes(content, "utf-8"))
        lines = content.splitlines()

        target_types = set(_CHUNK_NODE_TYPES.get(language, []))
        chunks = self._extract_nodes(
            tree.root_node, lines, rel_path, language, target_types, depth=0
        )

        # If the entire file was not covered (e.g. top-level statements),
        # add a catch-all window chunk for uncovered lines.
        covered = set()
        for c in chunks:
            covered.update(range(c.start_line, c.end_line + 1))
        uncovered_lines = [l for i, l in enumerate(lines, 1) if i not in covered]
        if uncovered_lines:
            remainder = "\n".join(uncovered_lines)
            if len(remainder.strip()) > 50:
                window_chunks = self._window_chunks(
                    remainder, rel_path, language, node_type="module_level"
                )
                chunks.extend(window_chunks)

        return chunks if chunks else self._window_chunks(content, rel_path, language)

    # ------------------------------------------------------------------
    # AST traversal
    # ------------------------------------------------------------------

    def _extract_nodes(
        self,
        node,
        lines: List[str],
        rel_path: str,
        language: str,
        target_types: set,
        depth: int,
    ) -> List[CodeChunk]:
        chunks: List[CodeChunk] = []

        for child in node.children:
            node_type = child.type
            start_line = child.start_point[0] + 1   # tree-sitter is 0-indexed
            end_line = child.end_point[0] + 1
            line_count = end_line - start_line + 1

            if node_type in target_types:
                # Extract source lines for this node
                node_lines = lines[start_line - 1: end_line]
                node_text = "\n".join(node_lines)
                symbol = self._extract_symbol_name(child)

                if line_count <= MAX_CHUNK_LINES:
                    # Perfect size — emit as a single chunk
                    chunk = CodeChunk(
                        chunk_id=_make_id(rel_path, start_line),
                        rel_path=rel_path,
                        language=language,
                        content=node_text,
                        start_line=start_line,
                        end_line=end_line,
                        node_type=node_type,
                        symbol_name=symbol,
                    )
                    if chunk.line_count >= MIN_CHUNK_LINES:
                        chunks.append(chunk)
                else:
                    # Too large → try to recurse into children first
                    sub = self._extract_nodes(
                        child, lines, rel_path, language, target_types, depth + 1
                    )
                    if sub:
                        chunks.extend(sub)
                    else:
                        # No sub-declarations found → window split
                        chunks.extend(
                            self._window_chunks(
                                node_text, rel_path, language,
                                base_line=start_line, node_type=node_type,
                                symbol_name=symbol
                            )
                        )
            else:
                # Not a target node type but might contain target children
                if depth < 4:   # don't recurse forever
                    chunks.extend(
                        self._extract_nodes(
                            child, lines, rel_path, language, target_types, depth + 1
                        )
                    )

        return chunks

    # ------------------------------------------------------------------
    # Symbol name extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_symbol_name(node) -> str:
        """Try to find the identifier/name child of a declaration node."""
        for child in node.children:
            if child.type in ("identifier", "name", "type_identifier",
                              "property_identifier", "field_identifier"):
                return child.text.decode("utf-8") if child.text else ""
        return node.type

    # ------------------------------------------------------------------
    # Fallback: sliding-window chunking
    # ------------------------------------------------------------------

    def _window_chunks(
        self,
        content: str,
        rel_path: str,
        language: str,
        base_line: int = 1,
        node_type: str = "window",
        symbol_name: str = "",
    ) -> List[CodeChunk]:
        """Split *content* into fixed-size line windows with overlap."""
        lines = content.splitlines()
        chunks = []
        step = MAX_CHUNK_LINES - CHUNK_OVERLAP_LINES
        i = 0
        while i < len(lines):
            window = lines[i: i + MAX_CHUNK_LINES]
            text = "\n".join(window)
            start = base_line + i
            end = start + len(window) - 1
            if len(text.strip()) >= 20:
                chunks.append(CodeChunk(
                    chunk_id=_make_id(rel_path, start),
                    rel_path=rel_path,
                    language=language,
                    content=text,
                    start_line=start,
                    end_line=end,
                    node_type=node_type,
                    symbol_name=symbol_name,
                ))
            i += step
            if i >= len(lines):
                break
        return chunks


# Module-level singleton
chunker = TreeSitterChunker()


def chunk_file(file_info: dict) -> List[CodeChunk]:
    """Convenience wrapper around the module singleton."""
    return chunker.chunk_file(file_info)
