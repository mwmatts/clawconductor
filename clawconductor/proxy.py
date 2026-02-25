"""ClawConductor proxy server.

Sits between OpenClaw and LiteLLM. Receives OpenAI-compatible requests,
extracts signals, calls route(), rewrites the model field to a tier alias,
and forwards to LiteLLM.

Run with:
    uvicorn clawconductor.proxy:app --port 8765
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from typing import Any, AsyncIterator, Dict

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .classifier import GROUP_A_FLAGS
from .loop_guard import LoopGuard
from .router import route

logger = logging.getLogger("clawconductor.proxy")

app = FastAPI(title="ClawConductor Proxy")

# --- Shared state (process lifetime) ---
_loop_guard = LoopGuard()
_failure_counts: Dict[str, int] = defaultdict(int)  # task_id -> consecutive failures

# --- Config ---
_config: dict = {}
_upstream_url: str = "http://localhost:4000"


def _load_config(path: str = "conductor.yaml") -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _startup() -> None:
    global _config, _upstream_url
    _config = _load_config()
    _upstream_url = _config.get("upstream_url", "http://localhost:4000").rstrip("/")
    logger.info("ClawConductor proxy started. Upstream: %s", _upstream_url)


app.add_event_handler("startup", _startup)


# --- Signal extraction ---

def _task_id_from_request(body: dict) -> str:
    """Stable hash of last message content + model field."""
    last_msg = ""
    messages = body.get("messages", [])
    if messages:
        last_msg = messages[-1].get("content", "") or ""
    raw = f"{body.get('model', '')}:{last_msg}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _task_class_from_messages(messages: list) -> str | None:
    """Scan last user message for Group A keywords."""
    if not messages:
        return None
    last_content = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            last_content = content.lower()
            break
    if not last_content:
        return None
    for keyword in GROUP_A_FLAGS:
        if keyword in last_content:
            return keyword
    return None


def _build_ctx(body: dict, task_id: str) -> dict:
    messages = body.get("messages", [])
    task_class = _task_class_from_messages(messages)
    return {
        "task_id": task_id,
        "signals": [],
        "retry_count": 0,
        "max_retries": 2,
        "consecutive_tool_failures": _failure_counts[task_id],
        **({"task_class": task_class} if task_class else {}),
    }


# --- Forwarding ---

async def _stream_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    body: dict,
) -> AsyncIterator[bytes]:
    async with client.stream(method, url, headers=headers, json=body, timeout=120) as r:
        async for chunk in r.aiter_bytes():
            yield chunk


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    task_id = _task_id_from_request(body)
    ctx = _build_ctx(body, task_id)

    decision = route(ctx, config=_config, loop_guard=_loop_guard)

    # Resolve tier alias — use LiteLLM virtual model name
    tier_aliases = {
        "lightweight": "tier/lightweight",
        "standard": "tier/standard",
        "advanced": "tier/advanced",
    }
    model_alias = tier_aliases.get(decision.tier, "tier/standard")

    logger.info(
        "task_id=%s lane=%s tier=%s model_alias=%s triggered=%s",
        task_id, decision.lane, decision.tier, model_alias,
        sorted(decision.triggered_groups),
    )

    # Rewrite model field
    forwarded_body = {**body, "model": model_alias}

    # Forward to LiteLLM
    upstream = f"{_upstream_url}/v1/chat/completions"
    forward_headers = {"Content-Type": "application/json"}
    # Pass through Authorization if present
    auth = request.headers.get("Authorization")
    if auth:
        forward_headers["Authorization"] = auth

    stream = forwarded_body.get("stream", False)

    try:
        async with httpx.AsyncClient() as client:
            if stream:
                return StreamingResponse(
                    _stream_response(client, "POST", upstream, forward_headers, forwarded_body),
                    media_type="text/event-stream",
                )
            else:
                r = await client.post(
                    upstream,
                    headers=forward_headers,
                    json=forwarded_body,
                    timeout=120,
                )
                _failure_counts[task_id] = 0  # reset on success
                return r.json()

    except httpx.TimeoutException:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.RequestError as e:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "upstream": _upstream_url}
