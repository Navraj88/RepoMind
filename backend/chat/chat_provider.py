"""
chat_provider.py
----------------
Streams a chat answer grounded in retrieved code chunks.
Uses Groq chat completions SDK.
"""

from __future__ import annotations
from typing import List, Generator

from groq import Groq

from config import GROQ_API_KEY, CHAT_MODEL

client = Groq(api_key=GROQ_API_KEY)

_MAX_CONTEXT_CHUNKS = 12
_MAX_CONTEXT_CHARS = 12000
_MAX_CHUNK_CHARS = 1200
_MAX_HISTORY_TURNS = 6
_MAX_HISTORY_CHARS = 1600

_ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "model": "assistant",  # legacy Gemini role from older frontend payloads
    "system": "system",
}

_SYSTEM_PROMPT = """You are an expert code assistant that answers questions about a software codebase.

You will be given RETRIEVED CODE CHUNKS from the codebase, each annotated with its file path and line numbers.
Use ONLY the information in these chunks to answer the user's question.
If the answer cannot be determined from the provided chunks, say so honestly.

When the question is about how a module works, trace end-to-end flow:
  - where it is defined
  - where and how it is invoked from other files
  - key data passed in/out
  - sequence of execution across files
If references are partial, explicitly say what is inferred vs directly shown.
Prefer explaining relationships between files/functions (caller -> callee, imports, data flow)
when multiple related chunks are present.

When referencing code:
  - Always cite the file path and line range.
  - Wrap inline code with backticks.
  - Use fenced code blocks for multi-line examples.
  - Synthesise a coherent explanation when multiple chunks are relevant.

Be concise but technically precise. Assume the user is a developer."""


def _format_context(chunks: List[dict]) -> str:
    parts = []
    for i, chunk in enumerate(chunks, 1):
        header = (
            f"[Chunk {i}] File: {chunk['rel_path']} "
            f"(lines {chunk['start_line']}–{chunk['end_line']}, "
            f"language: {chunk['language']}, "
            f"symbol: {chunk.get('symbol_name', 'unknown')})"
        )
        parts.append(f"{header}\n```{chunk['language']}\n{chunk['content']}\n```")
    return "\n\n".join(parts)


def _trim_chunk_content(content: str) -> str:
    text = content.strip()
    if len(text) <= _MAX_CHUNK_CHARS:
        return text
    return text[:_MAX_CHUNK_CHARS] + "\n// ... truncated for token budget ..."


def _select_chunks_for_llm(chunks: List[dict]) -> List[dict]:
    # Keep highest-ranked chunks first, then enforce strict size budget.
    ranked = sorted(
        chunks,
        key=lambda c: float(c.get("rrf_score", 0.0)),
        reverse=True,
    )
    picked: List[dict] = []
    used_chars = 0
    seen = set()

    for chunk in ranked:
        cid = chunk.get("chunk_id")
        if not cid or cid in seen:
            continue
        trimmed_content = _trim_chunk_content(str(chunk.get("content", "")))
        candidate = dict(chunk)
        candidate["content"] = trimmed_content
        estimated = len(trimmed_content) + 180  # header + formatting buffer
        if picked and used_chars + estimated > _MAX_CONTEXT_CHARS:
            break
        picked.append(candidate)
        seen.add(cid)
        used_chars += estimated
        if len(picked) >= _MAX_CONTEXT_CHUNKS:
            break

    return picked or ranked[: min(len(ranked), 4)]


def _trim_history(history: List[dict] | None) -> List[dict]:
    if not history:
        return []
    trimmed = history[-_MAX_HISTORY_TURNS:]
    out = []
    for turn in trimmed:
        role = _ROLE_MAP.get(str(turn.get("role", "user")).lower(), "user")
        parts = turn.get("parts")
        if isinstance(parts, list) and parts:
            content = str(parts[0])
        else:
            content = str(turn.get("content", ""))
        if not content:
            continue
        if len(content) > _MAX_HISTORY_CHARS:
            content = content[-_MAX_HISTORY_CHARS:]
        out.append({"role": role, "content": content})
    return out


def stream_answer(
    query: str,
    chunks: List[dict],
    history: List[dict] | None = None,
) -> Generator[str, None, None]:
    llm_chunks = _select_chunks_for_llm(chunks)
    context = _format_context(llm_chunks)
    user_message = f"RETRIEVED CONTEXT:\n{context}\n\nQUESTION: {query}"

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    for turn in _trim_history(history):
        messages.append(turn)

    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
        stream=True,
    )

    for chunk in response:
        delta = chunk.choices[0].delta.content or ""
        if delta:
            yield delta


def get_answer(
    query: str,
    chunks: List[dict],
    history: List[dict] | None = None,
) -> str:
    return "".join(stream_answer(query, chunks, history))
