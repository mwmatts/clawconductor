"""ClawConductor proxy server.

Sits between OpenClaw and LiteLLM. Receives OpenAI-compatible requests,
extracts signals, calls route(), rewrites the model field to a tier alias,
and forwards to LiteLLM.

Run with:
    uvicorn clawconductor.proxy:app --port 8765
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
from collections import defaultdict
from typing import Any, AsyncIterator, Dict

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from .classifier import GROUP_A_FLAGS
from .key_selector import select_key
from .loop_guard import LoopGuard
from .router import route

logger = logging.getLogger("clawconductor.proxy")

app = FastAPI(title="ClawConductor Proxy")

# --- Shared state (process lifetime) ---
_loop_guard = LoopGuard()
_failure_counts: Dict[str, int] = defaultdict(int)  # task_id -> consecutive failures
_last_patched_model: str = ""  # cache to avoid redundant gateway calls

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


# --- Telegram escalation notification ---

_TELEGRAM_BOT_TOKEN = os.environ.get("CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.environ.get("CLAWCONDUCTOR_TELEGRAM_CHAT_ID", "")


async def _notify_escalation(actual_model: str, triggered_groups: set, reason: str) -> None:
    """Fire-and-forget: send a Telegram message when a request is escalated."""
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return
    group_labels = {
        "A": "task keyword",
        "B": "consecutive failures",
        "C": "conflicting constraints",
        "D": "validation failed on retry",
        "E": "high-stakes action",
    }
    group_desc = ", ".join(group_labels.get(g, g) for g in sorted(triggered_groups))
    text = (
        f"⚡ *Escalating to {actual_model}*\n"
        f"Reason: {reason}\n"
        f"Triggers: {group_desc}"
    )
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": _TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=3,
            )
    except Exception as e:
        logger.warning("Telegram escalation notify failed (non-fatal): %s", e)


# --- Session model patching ---

async def _patch_session_model(model_name: str) -> None:
    """Fire-and-forget: update OpenClaw status bar via sessions.patch gateway call."""
    global _last_patched_model
    if model_name == _last_patched_model:
        return
    try:
        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "/home/matt/.npm-global/bin/openclaw", "gateway", "call", "sessions.patch",
                        "--params", f'{{"key":"agent:main:main","model":"litellm/{model_name}"}}',
                    ],
                    capture_output=True,
                    timeout=2,
                ),
            ),
            timeout=3,
        )
        _last_patched_model = model_name
        logger.info("Patched session model to litellm/%s", model_name)
    except Exception as e:
        logger.warning("sessions.patch failed (non-fatal): %s", e)


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
    url: str,
    headers: dict,
    body: dict,
) -> AsyncIterator[bytes]:
    client = httpx.AsyncClient()
    try:
        async with client.stream("POST", url, headers=headers, json=body, timeout=120) as r:
            async for chunk in r.aiter_bytes():
                yield chunk
    finally:
        await client.aclose()


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

    # Notify on escalation (fire-and-forget)
    if decision.lane == "escalation":
        tier_display = _config.get("tier_display_models", {})
        actual_model = tier_display.get(decision.tier, decision.tier)
        asyncio.create_task(
            _notify_escalation(actual_model, decision.triggered_groups, decision.reason)
        )

    # Rewrite model field
    forwarded_body = {**body, "model": model_alias}

    # Forward to LiteLLM
    upstream = f"{_upstream_url}/v1/chat/completions"
    forward_headers = {"Content-Type": "application/json"}
    # Use per-lane virtual key if configured; fall back to caller's key
    lane_key = select_key(decision.lane, config_path="conductor.yaml")
    if lane_key:
        forward_headers["Authorization"] = f"Bearer {lane_key}"
    else:
        auth = request.headers.get("Authorization")
        if auth:
            forward_headers["Authorization"] = auth

    stream = forwarded_body.get("stream", False)

    try:
        if stream:
            return StreamingResponse(
                _stream_response(upstream, forward_headers, forwarded_body),
                media_type="text/event-stream",
            )
        else:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    upstream,
                    headers=forward_headers,
                    json=forwarded_body,
                    timeout=120,
                )
                _failure_counts[task_id] = 0  # reset on success
                response_data = r.json()
                # Rewrite model field and patch OpenClaw status bar
                tier_display = _config.get("tier_display_models", {})
                actual_model = tier_display.get(decision.tier)
                if actual_model and isinstance(response_data, dict):
                    response_data["model"] = actual_model
                    asyncio.create_task(_patch_session_model(actual_model))
                return response_data

    except httpx.TimeoutException:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.RequestError as e:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "upstream": _upstream_url}
