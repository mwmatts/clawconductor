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
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import events, metrics as metrics_mod
from .classifier import GROUP_A_FLAGS
from .key_selector import select_key
from .loop_guard import LoopGuard
from .router import route

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger("clawconductor.proxy")

# --- Shared state (process lifetime) ---
_loop_guard = LoopGuard()
_failure_counts: Dict[str, int] = defaultdict(int)  # task_id -> consecutive failures
_last_patched_model: str = ""  # cache to avoid redundant gateway calls
_context_tokens: int = 0  # latest prompt_tokens from last response (best estimate)
_last_escalation_at: float = 0.0  # timestamp of last escalation notification sent
_budget_fallback_active: Dict[str, bool] = {}  # lane -> currently using budget fallback
_budget_notified: Dict[str, bool] = {}  # lane -> notification sent this budget period
_budget_fallback_since: Dict[str, str] = {}  # lane -> ISO timestamp when fallback started
_watchdog_alerted: set[tuple[str, str]] = set()  # (lane, started_ts) already alerted

_ESCALATION_COOLDOWN = 60  # seconds between escalation notifications
_FALLBACK_STUCK_THRESHOLD = 1800  # 30 minutes
_HEARTBEAT_INTERVAL = 1800  # 30 minutes

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
    events.init()
    events.record("startup", reason=f"ClawConductor started. Upstream: {_upstream_url}")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _startup()
    watchdog_task = asyncio.create_task(_fallback_watchdog())
    try:
        yield
    finally:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="ClawConductor Proxy", lifespan=_lifespan)


# --- Telegram notifications ---

_TELEGRAM_BOT_TOKEN = os.environ.get("CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.environ.get("CLAWCONDUCTOR_TELEGRAM_CHAT_ID", "")
_LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


async def _send_telegram(text: str) -> None:
    if not _TELEGRAM_BOT_TOKEN or not _TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": _TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=3,
            )
    except Exception as e:
        logger.warning("Telegram send failed (non-fatal): %s", e)


async def _notify_escalation(actual_model: str, triggered_groups: set, reason: str, task_class: str = "") -> None:
    """Fire-and-forget: send a Telegram message when a request is escalated."""
    group_descriptions = {
        "A": f'Keyword "{task_class}" detected' if task_class else "Task keyword detected",
        "B": "Consecutive tool failures",
        "C": "Conflicting constraints detected",
        "D": "Validation failed on retry",
        "E": "High-stakes action detected",
    }
    primary_group = sorted(triggered_groups)[0] if triggered_groups else "A"
    description = group_descriptions.get(primary_group, "Escalation triggered")
    await _send_telegram(f"⚡ {description} — switching to smarter model")


# --- Budget fallback helpers ---

def _is_budget_error(status_code: int, error_text: str) -> bool:
    """Check if a response is a budget-exceeded error (LiteLLM returns 400 or 429)."""
    return status_code in (400, 429) and "budget" in error_text.lower()


def _mark_budget_fallback(lane: str) -> bool:
    """Mark lane as in fallback. Returns True if notification should be sent."""
    if not _budget_fallback_active.get(lane):
        _budget_fallback_since[lane] = datetime.now(timezone.utc).isoformat()
    _budget_fallback_active[lane] = True
    if _budget_notified.get(lane):
        return False
    _budget_notified[lane] = True
    return True


def _clear_budget_fallback(lane: str) -> bool:
    """Clear fallback state. Returns True if we were in fallback (restored notification)."""
    was_active = _budget_fallback_active.get(lane, False)
    _budget_fallback_active[lane] = False
    _budget_notified[lane] = False
    _budget_fallback_since.pop(lane, None)
    return was_active


async def _fetch_lane_budget(lane: str) -> str:
    """Query LiteLLM for the actual max_budget of a lane's virtual key."""
    try:
        lane_key = select_key(lane, keys=_config.get("litellm_keys", {}))
        if not lane_key or not _LITELLM_MASTER_KEY:
            return ""
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_upstream_url}/key/info",
                headers={"Authorization": f"Bearer {_LITELLM_MASTER_KEY}"},
                params={"key": lane_key},
                timeout=3,
            )
            if r.status_code == 200:
                info = r.json()
                budget = info.get("info", {}).get("max_budget")
                if budget is not None:
                    return f"${budget:.2f}/day"
    except Exception:
        pass
    return ""


async def _notify_budget_cap(lane: str, actual_model: str, task_id: str) -> None:
    """Fire-and-forget: notify user that a lane's daily budget cap was hit."""
    lane_budget = await _fetch_lane_budget(lane)
    budget_str = f" ({lane_budget})" if lane_budget else ""
    await _send_telegram(
        f"💸 Budget cap hit: {lane} lane{budget_str}\n"
        f"Switching to cheaper model until midnight UTC.\n"
        f"Last model: {actual_model} | ID: {task_id[:6]}"
    )


async def _notify_budget_restored(lane: str) -> None:
    """Fire-and-forget: notify user that the paid model is back after budget reset."""
    await _send_telegram(f"✅ Budget restored: {lane} lane\nSwitching back to standard model.")


# --- Fallback-stuck watchdog ---

async def _fallback_watchdog() -> None:
    """Check every 5 min whether any lane has been in fallback too long."""
    while True:
        await asyncio.sleep(300)
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ts = time.monotonic()
        for lane, since_iso in list(_budget_fallback_since.items()):
            if not _budget_fallback_active.get(lane):
                continue
            try:
                since_dt = datetime.fromisoformat(since_iso)
                elapsed = (datetime.now(timezone.utc) - since_dt).total_seconds()
            except Exception:
                continue
            alert_key = (lane, since_iso)
            if elapsed >= _FALLBACK_STUCK_THRESHOLD and alert_key not in _watchdog_alerted:
                _watchdog_alerted.add(alert_key)
                minutes = int(elapsed // 60)
                logger.warning("Fallback stuck: %s lane has been in Gemini fallback for %d min", lane, minutes)
                events.record(
                    "fallback_stuck",
                    lane=lane,
                    model=_config.get("budget_fallback", {}).get("display_name", "gemini-2.5-flash"),
                    reason=f"Lane stuck in fallback for {minutes} min",
                )
                asyncio.create_task(_send_telegram(
                    f"⚠️ *Fallback stuck*: {lane} lane has been on Gemini for {minutes} min.\n"
                    f"Started: {since_iso[:16]} UTC\n"
                    f"Run: `curl -X POST http://localhost:8765/admin/reset-fallback?lane={lane}`"
                ))


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


# --- Routing metadata injection ---

def _inject_routing_metadata(
    messages: list, model_name: str, tier: str, context_tokens: int, limit: int
) -> list:
    """Prepend a one-line routing metadata block to the system message."""
    pct = int(context_tokens / limit * 100) if limit else 0
    meta = (
        f"[ClawConductor routing metadata — authoritative for this request: "
        f"actual_model={model_name}, tier={tier}, "
        f"context={context_tokens // 1000}k/{limit // 1000}k ({pct}%). "
        f"Disregard any other model references in this context when self-reporting.]"
    )
    messages = list(messages)
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "system":
            existing = msg.get("content") or ""
            messages[i] = {**msg, "content": f"{meta}\n{existing}"}
            return messages
    return [{"role": "system", "content": meta}] + messages


def _context_warning(context_tokens: int, limit: int) -> str | None:
    """Return a warning string if context is near the compaction threshold."""
    if not limit or context_tokens <= 0:
        return None
    pct = context_tokens / limit
    used = context_tokens // 1000
    cap = limit // 1000
    if pct >= 0.90:
        return (
            f"\n\n⚠️ *Context at {int(pct * 100)}% ({used}k/{cap}k tokens).* "
            f"Compaction imminent — session will auto-compact soon."
        )
    if pct >= 0.75:
        return (
            f"\n\n📊 *Context at {int(pct * 100)}% ({used}k/{cap}k tokens).* "
            f"Approaching compaction threshold."
        )
    return None


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

    xcc = body.get("x_clawconductor") or {}
    signals = xcc.get("signals", []) if isinstance(xcc, dict) else []
    validation_failed = xcc.get("validation_failed", False) if isinstance(xcc, dict) else False
    retry_count = xcc.get("retry_count", 0) if isinstance(xcc, dict) else 0
    xcc_task_id = xcc.get("task_id") if isinstance(xcc, dict) else None

    return {
        "task_id": xcc_task_id or task_id,
        "signals": signals if isinstance(signals, list) else [],
        "validation_failed": bool(validation_failed),
        "retry_count": int(retry_count),
        "max_retries": 2,
        "consecutive_tool_failures": _failure_counts[task_id],
        **({"task_class": task_class} if task_class else {}),
    }


# --- Forwarding ---

async def _stream_response(
    url: str,
    headers: dict,
    body: dict,
    context_limit: int = 0,
) -> AsyncIterator[bytes]:
    global _context_tokens
    client = httpx.AsyncClient()
    tail_buffer = b""
    MAX_TAIL = 4096
    try:
        async with client.stream("POST", url, headers=headers, json=body, timeout=120) as r:
            if r.status_code >= 400:
                error_body = await r.aread()
                raise httpx.HTTPStatusError(
                    f"Upstream {r.status_code}: {error_body.decode('utf-8', errors='ignore')}",
                    request=r.request, response=r,
                )
            async for chunk in r.aiter_bytes():
                yield chunk
                tail_buffer = (tail_buffer + chunk)[-MAX_TAIL:]
    finally:
        await client.aclose()

    try:
        for line in tail_buffer.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("data:") and "[DONE]" not in line:
                data_str = line[5:].strip()
                if data_str:
                    chunk_data = json.loads(data_str)
                    usage = chunk_data.get("usage") or {}
                    if "prompt_tokens" in usage:
                        _context_tokens = usage["prompt_tokens"]
    except Exception:
        pass

    if context_limit:
        warning = _context_warning(_context_tokens, context_limit)
        if warning:
            warn_payload = json.dumps({
                "id": "claw-ctx-warn",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": warning}, "finish_reason": None}],
            })
            yield f"data: {warn_payload}\n\n".encode()


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    global _context_tokens
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    task_id = _task_id_from_request(body)
    ctx = _build_ctx(body, task_id)
    decision = route(ctx, config=_config, loop_guard=_loop_guard)

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

    tier_display = _config.get("tier_display_models", {})
    actual_model = tier_display.get(decision.tier, decision.tier)

    # --- Metrics + event recording ---
    if decision.lane == "escalation":
        metrics_mod.metrics.record_escalation(decision.triggered_groups)
        events.record(
            "escalation",
            lane=decision.lane,
            tier=decision.tier,
            model=actual_model,
            groups=decision.triggered_groups,
            reason=decision.reason,
            task_id=task_id,
            trace_id=decision.trace_id,
        )
    else:
        metrics_mod.metrics.record_routing()

    # Heartbeat: record current model/lane every 30 min
    if metrics_mod.metrics.needs_heartbeat(_HEARTBEAT_INTERVAL):
        metrics_mod.metrics.mark_heartbeat()
        events.record(
            "heartbeat",
            lane=decision.lane,
            tier=decision.tier,
            model=actual_model,
            reason="periodic heartbeat",
        )

    # Notify on escalation (debounced)
    if decision.lane == "escalation":
        global _last_escalation_at
        now = time.monotonic()
        if now - _last_escalation_at >= _ESCALATION_COOLDOWN:
            _last_escalation_at = now
            asyncio.create_task(
                _notify_escalation(actual_model, decision.triggered_groups, decision.reason, task_class=ctx.get("task_class", ""))
            )

    forwarded_body = {**body, "model": model_alias}
    context_limit = _config.get("context_token_limit", 40000)

    injected_messages = _inject_routing_metadata(
        forwarded_body.get("messages", []),
        model_name=actual_model,
        tier=decision.tier,
        context_tokens=_context_tokens,
        limit=context_limit,
    )
    forwarded_body = {**forwarded_body, "messages": injected_messages}

    stream = forwarded_body.get("stream", False)
    if stream:
        forwarded_body = {**forwarded_body, "stream_options": {"include_usage": True}}

    upstream = f"{_upstream_url}/v1/chat/completions"
    forward_headers = {"Content-Type": "application/json"}
    lane_key = select_key(decision.lane, keys=_config.get("litellm_keys", {}))
    if lane_key:
        forward_headers["Authorization"] = f"Bearer {lane_key}"
    else:
        auth = request.headers.get("Authorization")
        if auth:
            forward_headers["Authorization"] = auth

    fb_model = _config.get("budget_fallback", {}).get("model", "gemini-2.5-flash")
    fb_key = select_key("fallback", keys=_config.get("litellm_keys", {}))
    fb_headers = {"Content-Type": "application/json"}
    if fb_key:
        fb_headers["Authorization"] = f"Bearer {fb_key}"

    try:
        if _budget_fallback_active.get(decision.lane, False):
            logger.info("Lane %s in budget fallback — routing direct to %s", decision.lane, fb_model)
            fb_body = {**forwarded_body, "model": fb_model}
            if stream:
                return StreamingResponse(
                    _stream_response(upstream, fb_headers, fb_body, context_limit),
                    media_type="text/event-stream",
                )
            else:
                async with httpx.AsyncClient() as client:
                    r = await client.post(upstream, headers=fb_headers, json=fb_body, timeout=120)

        elif stream:
            async def _gen_with_fallback():
                try:
                    async for chunk in _stream_response(upstream, forward_headers, forwarded_body, context_limit):
                        yield chunk
                except httpx.HTTPStatusError as e:
                    if _is_budget_error(e.response.status_code, str(e)):
                        if _mark_budget_fallback(decision.lane):
                            asyncio.create_task(_notify_budget_cap(decision.lane, actual_model, task_id))
                            events.record(
                                "budget_fallback",
                                lane=decision.lane,
                                model=fb_model,
                                reason=f"Budget 429 on {decision.lane} lane — streaming fallback",
                                task_id=task_id,
                            )
                        logger.info("Budget 429 on %s lane — streaming via fallback %s", decision.lane, fb_model)
                        fb_body = {**forwarded_body, "model": fb_model}
                        async for chunk in _stream_response(upstream, fb_headers, fb_body, context_limit):
                            yield chunk
                    else:
                        raise

            return StreamingResponse(_gen_with_fallback(), media_type="text/event-stream")
        else:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    upstream,
                    headers=forward_headers,
                    json=forwarded_body,
                    timeout=120,
                )
                if _is_budget_error(r.status_code, r.text):
                    if _mark_budget_fallback(decision.lane):
                        asyncio.create_task(_notify_budget_cap(decision.lane, actual_model, task_id))
                        events.record(
                            "budget_fallback",
                            lane=decision.lane,
                            model=fb_model,
                            reason=f"Budget 429 on {decision.lane} lane — non-streaming retry",
                            task_id=task_id,
                        )
                    logger.info("Budget 429 on %s lane — retrying with fallback %s", decision.lane, fb_model)
                    fb_body = {**forwarded_body, "model": fb_model}
                    r = await client.post(upstream, headers=fb_headers, json=fb_body, timeout=120)

        if not stream:
            if r.status_code >= 400:
                _failure_counts[task_id] += 1
                raise HTTPException(status_code=r.status_code, detail=r.text)
            _failure_counts[task_id] = 0
            response_data = r.json()

            usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
            if usage and "prompt_tokens" in usage:
                _context_tokens = usage["prompt_tokens"]

            if actual_model and isinstance(response_data, dict):
                response_data["model"] = actual_model
                asyncio.create_task(_patch_session_model(actual_model))

                warning = _context_warning(_context_tokens, context_limit)
                if warning:
                    choices = response_data.get("choices", [])
                    if choices and isinstance(choices[0].get("message", {}).get("content"), str):
                        response_data["choices"][0]["message"]["content"] += warning

            return response_data

    except httpx.TimeoutException:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=504, detail="Upstream timeout")
    except httpx.RequestError as e:
        _failure_counts[task_id] += 1
        raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@app.post("/admin/reset-fallback")
async def reset_fallback(lane: str = "all") -> dict:
    """Reset budget fallback state for a lane. Called by cron or manual bump script."""
    valid = {"routing", "escalation", "all"}
    if lane not in valid:
        raise HTTPException(status_code=400, detail=f"lane must be one of {valid}")
    lanes = ["routing", "escalation"] if lane == "all" else [lane]
    reset = []
    for l in lanes:
        if _budget_fallback_active.get(l):
            _clear_budget_fallback(l)
            asyncio.create_task(_notify_budget_restored(l))
            events.record("budget_restored", lane=l, reason="Budget reset via admin endpoint")
            reset.append(l)
    logger.info("Fallback reset for lanes: %s", reset or "none active")
    return {"status": "ok", "reset": reset}


@app.get("/admin/status")
async def admin_status() -> dict:
    """Live health snapshot: current model per lane, fallback state, today's counts."""
    snap = metrics_mod.metrics.snapshot()
    tier_display = _config.get("tier_display_models", {})
    fb_display = _config.get("budget_fallback", {}).get("display_name", "gemini-2.5-flash")

    routing_fb = _budget_fallback_active.get("routing", False)
    escalation_fb = _budget_fallback_active.get("escalation", False)

    if routing_fb and escalation_fb:
        health = "degraded_all_fallback"
    elif routing_fb:
        health = "degraded_routing_fallback"
    elif escalation_fb:
        health = "degraded_escalation_fallback"
    else:
        health = "nominal"

    def _lane_info(lane: str, normal_tier: str, in_fallback: bool) -> dict:
        normal_model = tier_display.get(normal_tier, normal_tier)
        return {
            "active_model": fb_display if in_fallback else normal_model,
            "in_fallback": in_fallback,
            "fallback_since": _budget_fallback_since.get(lane) if in_fallback else None,
            "requests_today": snap["routing_requests"] if lane == "routing" else snap["escalation_requests"],
            "last_request_at": snap["last_request_at"].get(lane),
        }

    routing_tier = _config.get("routing_lane", {}).get("tier", "lightweight")
    escalation_tier = _config.get("escalation_lane", {}).get("tier", "advanced")

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "lanes": {
            "routing": _lane_info("routing", routing_tier, routing_fb),
            "escalation": _lane_info("escalation", escalation_tier, escalation_fb),
        },
        "escalations_today": snap["escalation_triggers"],
        "context_tokens_last": _context_tokens,
    }


@app.get("/admin/history")
async def admin_history(
    days: int = 1,
    event_type: str | None = None,
    lane: str | None = None,
    format: str = "json",
) -> Any:
    """Query persistent event history.

    format: "json" (default) | "table" | "csv"
    """
    rows = events.query(days=days, event_type=event_type, lane=lane)
    if format == "table":
        return {"table": events.format_table(rows), "count": len(rows)}
    if format == "csv":
        return StreamingResponse(
            iter([events.to_csv(rows)]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=clawconductor-events-{days}d.csv"},
        )
    return {"events": rows, "count": len(rows)}


@app.get("/admin/export")
async def admin_export(days: int = 7) -> StreamingResponse:
    """Download event history as CSV."""
    rows = events.query(days=days, limit=10000)
    return StreamingResponse(
        iter([events.to_csv(rows)]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=clawconductor-events-{days}d.csv"},
    )


@app.get("/admin/daily-report")
async def admin_daily_report(date: str | None = None) -> dict:
    """Return a plain-text daily summary (Telegram-ready)."""
    summary = events.daily_summary(date)
    day = summary.get("date", "unknown")
    counts = summary.get("counts", {})
    triggers = summary.get("escalation_triggers", {})
    fallback_rows = summary.get("fallback_rows", [])

    lines = [f"ClawConductor Report — {day}", ""]

    # Request counts (from persistent DB — survives restarts)
    escalations = counts.get("escalation", 0)
    fallbacks = counts.get("budget_fallback", 0)
    restores = counts.get("budget_restored", 0)
    lines.append(f"Escalations: {escalations}")
    lines.append(f"Fallback events: {fallbacks}  |  Restores: {restores}")

    if triggers:
        parts = "  ".join(f"Group {k}×{v}" for k, v in sorted(triggers.items()))
        lines.append(f"Escalation triggers: {parts}")
    else:
        lines.append("Escalation triggers: none")

    lines.append("")

    # Fallback timeline
    if fallback_rows:
        lines.append("Fallback timeline:")
        for row in fallback_rows:
            ts = row.get("ts", "")[:16]
            t = row.get("type", "")
            lane = row.get("lane", "")
            icon = "⚠️" if "fallback" in t else "✅"
            lines.append(f"  {icon} {ts}  {lane}  {t}")
    else:
        lines.append("Fallback events: none ✓")

    # Current status
    lines.append("")
    routing_fb = _budget_fallback_active.get("routing", False)
    escalation_fb = _budget_fallback_active.get("escalation", False)
    if routing_fb or escalation_fb:
        active = [l for l, v in [("routing", routing_fb), ("escalation", escalation_fb)] if v]
        lines.append(f"⚠️ Currently in fallback: {', '.join(active)}")
    else:
        lines.append("Status now: nominal ✓")

    return {"report": "\n".join(lines), "date": day}


@app.post("/admin/reset-metrics")
async def admin_reset_metrics() -> dict:
    """Reset in-memory daily counters (called by midnight cron after daily report is sent)."""
    metrics_mod.metrics.reset()
    events.reset_today()
    new_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    logger.info("Metrics reset for new day: %s", new_date)
    return {"status": "ok", "new_date": new_date}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "upstream": _upstream_url}
