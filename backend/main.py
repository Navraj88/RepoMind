"""
main.py
-------
FastAPI application exposing:

  POST /ingest          - Clone a GitHub repo, chunk it, embed, store in Qdrant
  POST /ingest/cancel   - Cancel a running ingestion
  GET  /ingest/status   - SSE stream of ingestion progress
  POST /chat            - Hybrid search + Gemini answer (SSE streamed)
  GET  /health          - Quick health check
  GET  /collection/info - Stats about the current Qdrant collection
  POST /collection/reset- Drop and recreate the collection
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from ingestion.github_fetcher import fetch_repository, list_repository_branches
from ingestion.tree_sitter_chunker import chunk_file
from ingestion.embedder import embed_chunks
from retrieval.qdrant_store import store
from retrieval.hybrid_search import hybrid_search
from retrieval.code_graph import graph_index
from chat.chat_provider import stream_answer
from config import TOP_K

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Codebase RAG API",
    description="Chat with any GitHub repository using Tree-sitter + Qdrant + Gemini",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Ingestion state
# ---------------------------------------------------------------------------

_ingestion_state: dict = {
    "status": "idle",
    "repo_url": "",
    "total_files": 0,
    "processed_files": 0,
    "total_chunks": 0,
    "current_file": "",
    "error": "",
}

_progress_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

# Threading event — set this to signal the ingestion loop to stop
_cancel_event = threading.Event()


def _update_state(**kwargs):
    _ingestion_state.update(kwargs)
    try:
        _progress_queue.put_nowait(dict(_ingestion_state))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    repo_url: str = Field(..., description="GitHub repo URL or owner/repo shorthand")
    branch: str | None = Field(None, description="Optional branch to ingest")
    recreate_collection: bool = Field(False)


class ChatRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the codebase")
    top_k: int = Field(TOP_K, ge=1, le=20)
    filter_lang: str | None = Field(None)
    history: list[dict] | None = Field(None)


# ---------------------------------------------------------------------------
# Background ingestion task
# ---------------------------------------------------------------------------

def _run_ingestion(repo_url: str, branch: str | None, recreate: bool):
    """Runs in a thread pool. Checks _cancel_event between each file."""
    _cancel_event.clear()

    try:
        _update_state(
            status="running", repo_url=repo_url, error="",
            processed_files=0, total_chunks=0, current_file=""
        )

        store.create_collection(recreate=recreate)

        all_files = list(fetch_repository(repo_url, branch=branch))
        _update_state(total_files=len(all_files))

        graph_chunks: list[dict] = []

        for file_info in all_files:

            # Check for cancel signal before each file
            if _cancel_event.is_set():
                _update_state(status="cancelled", current_file="")
                return

            _update_state(current_file=file_info["rel_path"])

            try:
                chunks = chunk_file(file_info)
                if not chunks:
                    continue
                embeddings = embed_chunks(chunks)
                store.upsert_chunks(chunks, embeddings)
                graph_chunks.extend(
                    {
                        "chunk_id": c.chunk_id,
                        "rel_path": c.rel_path,
                        "content": c.content,
                        "symbol_name": c.symbol_name,
                    }
                    for c in chunks
                )
                _update_state(
                    total_chunks=_ingestion_state["total_chunks"] + len(chunks),
                    processed_files=_ingestion_state["processed_files"] + 1,
                )
            except Exception as e:
                print(f"[WARN] Skipping {file_info['rel_path']}: {e}", flush=True)

        graph_index.build_from_chunks(graph_chunks, repo=repo_url, branch=branch or "")
        _update_state(status="done", current_file="")

    except Exception as e:
        _update_state(status="error", error=str(e))
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/collection/info")
def collection_info():
    return {
        "collection": store.collection,
        "exists": store.collection_exists(),
        "chunk_count": store.count(),
    }


@app.post("/collection/reset")
def reset_collection():
    store.create_collection(recreate=True)
    graph_index.clear(persist=True)
    _update_state(
        status="idle", total_files=0, processed_files=0,
        total_chunks=0, current_file="", repo_url="", error=""
    )
    return {"message": "Collection reset successfully"}


@app.get("/graph/info")
def graph_info():
    return graph_index.graph_stats()


@app.post("/ingest")
async def ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    if _ingestion_state["status"] == "running":
        raise HTTPException(409, "Ingestion already in progress. Cancel it first.")

    background_tasks.add_task(
        asyncio.to_thread, _run_ingestion, req.repo_url, req.branch, req.recreate_collection
    )
    return {"message": "Ingestion started", "repo_url": req.repo_url, "branch": req.branch}


@app.get("/ingest/branches")
def ingest_branches(repo_url: str = Query(..., description="GitHub URL or owner/repo")):
    try:
        branches = list_repository_branches(repo_url)
    except Exception as e:
        raise HTTPException(400, f"Failed to list branches: {e}")
    return {"repo_url": repo_url, "branches": branches}


@app.post("/ingest/cancel")
def cancel_ingestion():
    """Signal the running ingestion to stop after the current file."""
    if _ingestion_state["status"] != "running":
        raise HTTPException(400, "No ingestion is currently running.")
    _cancel_event.set()
    return {"message": "Cancel signal sent. Stopping after current file."}


@app.get("/ingest/status")
async def ingest_status():
    """SSE stream of ingestion progress events."""
    async def event_stream() -> AsyncGenerator[str, None]:
        yield f"data: {json.dumps(_ingestion_state)}\n\n"

        while True:
            try:
                update = await asyncio.wait_for(_progress_queue.get(), timeout=2.0)
                yield f"data: {json.dumps(update)}\n\n"
                if update.get("status") in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                if _ingestion_state["status"] in ("done", "error", "idle", "cancelled"):
                    yield f"data: {json.dumps(_ingestion_state)}\n\n"
                    break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat")
async def chat(req: ChatRequest):
    """Hybrid search + Gemini streaming answer via SSE."""
    if not store.collection_exists() or store.count() == 0:
        raise HTTPException(400, "No codebase ingested yet. Please ingest a repo first.")

    async def answer_stream() -> AsyncGenerator[str, None]:
        try:
            chunks = hybrid_search(
                query=req.query,
                top_k=req.top_k,
                filter_lang=req.filter_lang,
            )

            sources = [
                {
                    "rel_path":    c["rel_path"],
                    "start_line":  c["start_line"],
                    "end_line":    c["end_line"],
                    "symbol_name": c.get("symbol_name", ""),
                    "rrf_score":   c.get("rrf_score", 0),
                }
                for c in chunks
            ]
            yield f"data: {json.dumps({'type': 'sources', 'data': sources})}\n\n"

            for text_chunk in stream_answer(req.query, chunks, req.history):
                payload = json.dumps({"type": "token", "data": text_chunk})
                yield f"data: {payload}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'data': str(e)})}\n\n"

    return StreamingResponse(
        answer_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )