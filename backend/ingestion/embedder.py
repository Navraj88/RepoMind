"""
embedder.py
-----------
Generates dense vector embeddings using Google Gemini gemini-embedding-001.
Uses google-genai SDK directly — same pattern as working RAG project.
"""

import time
from typing import List

from google import genai

from config import GEMINI_API_KEY
from ingestion.tree_sitter_chunker import CodeChunk

client = genai.Client(api_key=GEMINI_API_KEY)

_RETRY_DELAY = 3
_MODEL = "models/gemini-embedding-001"


def _build_embed_text(chunk: CodeChunk) -> str:
    header = f"File: {chunk.rel_path}"
    if chunk.symbol_name and chunk.symbol_name != chunk.node_type:
        header += f"  Symbol: {chunk.symbol_name}"
    return f"{header}\n\n{chunk.content}"


def _embed_single(text: str) -> List[float]:
    for attempt in range(3):
        try:
            result = client.models.embed_content(
                model=_MODEL,
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            if attempt < 2:
                time.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                raise RuntimeError(f"Embedding failed after 3 attempts: {e}") from e


def embed_chunks(chunks: List[CodeChunk]) -> List[List[float]]:
    embeddings = []
    for chunk in chunks:
        text = _build_embed_text(chunk)
        vec = _embed_single(text)
        embeddings.append(vec)
    return embeddings


def embed_query(query: str) -> List[float]:
    return _embed_single(query)
