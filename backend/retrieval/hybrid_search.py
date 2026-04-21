"""
hybrid_search.py
----------------
Combines dense (semantic) and sparse (BM25) search results using
Reciprocal Rank Fusion (RRF).

RRF formula:  score(d) = Σ  1 / (k + rank_i(d))
    where k=60 (standard constant), rank_i is the rank of doc d
    in retrieval list i.

Why RRF instead of weighted score sum?
  - Score scales differ wildly between cosine similarity and BM25
  - RRF only needs rank positions, so it's robust and parameter-free
  - Empirically matches or beats weighted fusion on code retrieval
"""

from __future__ import annotations

import re
from typing import List, Optional

from ingestion.embedder import embed_query
from retrieval.qdrant_store import store
from retrieval.code_graph import graph_index
from config import TOP_K, DENSE_WEIGHT, SPARSE_WEIGHT

_RRF_K = 60   # standard RRF constant
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{2,}$")


def _rrf_merge(
    dense_results: List[dict],
    sparse_results: List[dict],
    top_k: int,
) -> List[dict]:
    """
    Merge two ranked lists with RRF.
    Each item must have a "chunk_id" key for deduplication.
    """
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}

    for rank, item in enumerate(dense_results):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + DENSE_WEIGHT / (_RRF_K + rank + 1)
        payloads[cid] = item

    for rank, item in enumerate(sparse_results):
        cid = item["chunk_id"]
        scores[cid] = scores.get(cid, 0.0) + SPARSE_WEIGHT / (_RRF_K + rank + 1)
        payloads[cid] = item

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    results = []
    for cid, rrf_score in ranked[:top_k]:
        item = dict(payloads[cid])
        item["rrf_score"] = round(rrf_score, 6)
        results.append(item)

    return results


def hybrid_search(
    query: str,
    top_k: int = TOP_K,
    filter_lang: Optional[str] = None,
) -> List[dict]:
    """
    Full hybrid search pipeline:
      1. Embed query (dense)
      2. BM25 sparse search in Qdrant
      3. Dense cosine search in Qdrant
      4. RRF fusion
      5. Return top_k ranked chunks

    Each returned dict contains the full chunk payload plus 'rrf_score'.
    """
    # Fetch more candidates per sub-retriever to improve RRF quality
    candidate_k = min(top_k * 3, 50)
    max_expanded = min(max(top_k * 2, top_k + 4), 18)

    query_vector = embed_query(query)

    dense_hits = store.dense_search(
        query_vector=query_vector,
        top_k=candidate_k,
        filter_lang=filter_lang,
    )

    sparse_hits = store.sparse_search(
        query=query,
        top_k=candidate_k,
        filter_lang=filter_lang,
    )

    primary = _rrf_merge(dense_hits, sparse_hits, top_k)
    if not primary:
        return primary

    # Expand context beyond isolated chunks:
    # - add nearby chunks from the same file
    # - add cross-file symbol mentions (caller/callee hints)
    expanded: List[dict] = list(primary)
    seen_ids = {c["chunk_id"] for c in primary}

    # 1) Same-file context for top files
    top_paths = []
    for chunk in primary:
        rel_path = chunk.get("rel_path")
        if rel_path and rel_path not in top_paths:
            top_paths.append(rel_path)
        if len(top_paths) >= 3:
            break

    for rel_path in top_paths:
        siblings = store.list_chunks_by_path(rel_path, limit=6)
        siblings = sorted(siblings, key=lambda c: int(c.get("start_line", 0)))
        for sibling in siblings:
            cid = sibling.get("chunk_id")
            if not cid or cid in seen_ids:
                continue
            sibling["rrf_score"] = 0.0
            expanded.append(sibling)
            seen_ids.add(cid)
            if len(expanded) >= max_expanded:
                return expanded

    # 2) Cross-file symbol mention context
    symbols = []
    for chunk in primary:
        symbol = str(chunk.get("symbol_name", "")).strip()
        if symbol and _SYMBOL_RE.match(symbol) and symbol not in symbols:
            symbols.append(symbol)
        if len(symbols) >= 6:
            break

    for symbol in symbols:
        mentions = store.sparse_search(query=symbol, top_k=6, filter_lang=filter_lang)
        for mention in mentions:
            cid = mention.get("chunk_id")
            if not cid or cid in seen_ids:
                continue
            mention["rrf_score"] = mention.get("rrf_score", 0.0)
            expanded.append(mention)
            seen_ids.add(cid)
            if len(expanded) >= max_expanded:
                return expanded

    # 3) Graph-based expansion using caller/callee/import edges
    seed_chunk_ids = [c.get("chunk_id", "") for c in primary if c.get("chunk_id")]
    graph_related_ids = graph_index.expand_related_chunk_ids(
        seed_chunk_ids=seed_chunk_ids,
        seed_symbols=symbols,
        max_depth=2,
        max_nodes=min(top_k * 3, 24),
    )
    graph_chunks = store.get_chunks_by_chunk_ids(graph_related_ids, limit=min(top_k, 8))
    for gc in graph_chunks:
        cid = gc.get("chunk_id")
        if not cid or cid in seen_ids:
            continue
        gc["rrf_score"] = 0.0
        gc["retrieval_hint"] = "graph_related"
        expanded.append(gc)
        seen_ids.add(cid)
        if len(expanded) >= max_expanded:
            break

    return expanded
