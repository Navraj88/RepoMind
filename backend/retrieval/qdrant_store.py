"""
qdrant_store.py
---------------
Manages everything Qdrant-related:
  - Collection creation / re-creation
  - Upserting chunks (dense vectors + sparse BM25 vectors + payload)
  - Dense-only vector search (used internally by hybrid_search.py)
  - Sparse-only keyword search
  - Payload-based filtering

Qdrant's built-in sparse vector support is used for BM25/SPLADE-style
keyword matching, which is combined with dense search in hybrid_search.py.

We use the `qdrant_client` Python SDK with in-process BM25 tokenisation
(no external SPLADE model needed) via SparseVector.
"""

from __future__ import annotations

from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PointStruct,
    SparseVector,
    SearchRequest,
    NamedVector,
    NamedSparseVector,
    Filter,
    FieldCondition,
    MatchValue,
)

from config import (
    QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION, EMBEDDING_DIM, TOP_K
)
from ingestion.tree_sitter_chunker import CodeChunk

# -----------------------------------------------------------------------
# BM25-style sparse vectoriser (no external model required)
# -----------------------------------------------------------------------
import math
import re
from collections import Counter


def _tokenise(text: str) -> List[str]:
    """Very lightweight tokeniser: lowercase alphanumeric tokens."""
    return re.findall(r"[a-z0-9_]+", text.lower())


# A fixed global vocabulary built incrementally at upsert time
_vocab: dict[str, int] = {}
_idf: dict[int, float] = {}     # token_id → IDF score
_doc_count = 0                   # total docs ever upserted


def _get_or_add_token(token: str) -> int:
    if token not in _vocab:
        _vocab[token] = len(_vocab)
    return _vocab[token]


def _bm25_sparse_vector(text: str, k1: float = 1.5, b: float = 0.75) -> SparseVector:
    """
    Produce a sparse BM25 vector for *text*.
    IDF is approximated with log(1 + N/df) where N = total docs seen.
    For a production system you'd pre-compute IDF over the whole corpus;
    here we use per-call TF multiplied by a smoothed IDF estimate.
    """
    tokens = _tokenise(text)
    if not tokens:
        return SparseVector(indices=[], values=[])
    tf = Counter(tokens)
    doc_len = len(tokens)
    avg_doc_len = 200   # rough estimate for code chunks

    indices, values = [], []
    for token, freq in tf.items():
        tid = _get_or_add_token(token)
        # Smoothed IDF: pretend we've seen each token at least twice
        N = max(_doc_count, 1)
        df = 2   # conservative floor
        idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
        # BM25 TF normalisation
        tf_norm = (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / avg_doc_len))
        score = idf * tf_norm
        if score > 0:
            indices.append(tid)
            values.append(float(score))

    return SparseVector(indices=indices, values=values)


# -----------------------------------------------------------------------
# Qdrant client wrapper
# -----------------------------------------------------------------------

class QdrantStore:
    def __init__(self):
        self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, prefer_grpc=False)
        self.collection = QDRANT_COLLECTION

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def create_collection(self, recreate: bool = False):
        """Create (or recreate) the Qdrant collection with dense + sparse vectors."""
        existing = [c.name for c in self.client.get_collections().collections]

        if self.collection in existing:
            if recreate:
                self.client.delete_collection(self.collection)
            else:
                return   # already exists, nothing to do

        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                "dense": VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(
                    index=SparseIndexParams(on_disk=False)
                )
            },
        )

    def collection_exists(self) -> bool:
        existing = [c.name for c in self.client.get_collections().collections]
        return self.collection in existing

    def count(self) -> int:
        if not self.collection_exists():
            return 0
        return self.client.count(self.collection).count

    # ------------------------------------------------------------------
    # Upsert
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        chunks: List[CodeChunk],
        embeddings: List[List[float]],
        batch_size: int = 100,
    ):
        """Upsert chunks with their dense and sparse vectors."""
        global _doc_count
        points = []

        for chunk, dense_vec in zip(chunks, embeddings):
            sparse_vec = _bm25_sparse_vector(chunk.content)
            _doc_count += 1

            points.append(
                PointStruct(
                    id=abs(hash(chunk.chunk_id)) % (2**63),  # Qdrant needs uint64
                    vector={
                        "dense":  dense_vec,
                        "sparse": sparse_vec,
                    },
                    payload={
                        "chunk_id":    chunk.chunk_id,
                        "rel_path":    chunk.rel_path,
                        "language":    chunk.language,
                        "content":     chunk.content,
                        "start_line":  chunk.start_line,
                        "end_line":    chunk.end_line,
                        "node_type":   chunk.node_type,
                        "symbol_name": chunk.symbol_name,
                    },
                )
            )

        for i in range(0, len(points), batch_size):
            self.client.upsert(
                collection_name=self.collection,
                points=points[i: i + batch_size],
            )

    # ------------------------------------------------------------------
    # Dense search
    # ------------------------------------------------------------------

    def dense_search(
        self,
        query_vector: List[float],
        top_k: int = TOP_K,
        filter_lang: Optional[str] = None,
    ) -> List[dict]:
        query_filter = None
        if filter_lang:
            query_filter = Filter(
                must=[FieldCondition(key="language", match=MatchValue(value=filter_lang))]
            )

        hits = self.client.search(
            collection_name=self.collection,
            query_vector=NamedVector(name="dense", vector=query_vector),
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        return [{"score": h.score, **h.payload} for h in hits]

    # ------------------------------------------------------------------
    # Sparse (BM25) search
    # ------------------------------------------------------------------

    def sparse_search(
        self,
        query: str,
        top_k: int = TOP_K,
        filter_lang: Optional[str] = None,
    ) -> List[dict]:
        sparse_vec = _bm25_sparse_vector(query)
        if not sparse_vec.indices:
            return []

        query_filter = None
        if filter_lang:
            query_filter = Filter(
                must=[FieldCondition(key="language", match=MatchValue(value=filter_lang))]
            )

        hits = self.client.search(
            collection_name=self.collection,
            query_vector=NamedSparseVector(name="sparse", vector=sparse_vec),
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )
        return [{"score": h.score, **h.payload} for h in hits]

    def list_chunks_by_path(self, rel_path: str, limit: int = 10) -> List[dict]:
        """Fetch chunks belonging to a specific file path."""
        query_filter = Filter(
            must=[FieldCondition(key="rel_path", match=MatchValue(value=rel_path))]
        )
        records, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [dict(r.payload or {}) for r in records if r.payload]

    def get_chunk_by_chunk_id(self, chunk_id: str) -> Optional[dict]:
        """Fetch one chunk payload by chunk_id."""
        query_filter = Filter(
            must=[FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id))]
        )
        records, _ = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=query_filter,
            limit=1,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        payload = records[0].payload or {}
        return dict(payload)

    def get_chunks_by_chunk_ids(self, chunk_ids: List[str], limit: int = 30) -> List[dict]:
        """Fetch multiple chunks by payload chunk_id values."""
        out: List[dict] = []
        seen = set()
        for cid in chunk_ids:
            if cid in seen:
                continue
            payload = self.get_chunk_by_chunk_id(cid)
            if payload:
                out.append(payload)
                seen.add(cid)
            if len(out) >= limit:
                break
        return out


# Module-level singleton
store = QdrantStore()
