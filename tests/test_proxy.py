"""Tests for clawconductor proxy — key selection and health endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import clawconductor.proxy as proxy_mod
from clawconductor.proxy import app

# ---------------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------------

_KEYS = {
    "routing": "sk-routing-key",
    "escalation": "sk-escalation-key",
    "fallback": "sk-fallback-key",
}

_TEST_CONFIG = {
    "upstream_url": "http://localhost:4000",
    "routing_lane": {"tier": "lightweight"},
    "escalation_lane": {"tier": "advanced"},
    "tiers": {
        "lightweight": "tier/lightweight",
        "standard": "tier/standard",
        "advanced": "tier/advanced",
    },
    "tier_display_models": {
        "lightweight": "claude-haiku-4-5",
        "standard": "claude-sonnet-4-6",
        "advanced": "claude-sonnet-4-6",
    },
    "budget_fallback": {
        "model": "gemini-2.5-flash",
        "display_name": "Gemini 2.5 Flash",
    },
    "litellm_keys": _KEYS,
    "context_token_limit": 40000,
}

_BODY = {
    "model": "test-model",
    "messages": [{"role": "user", "content": "hello"}],
}


def _make_upstream_mock(captured_headers: dict):
    """Return a mock httpx.AsyncClient that records the Authorization header from post()."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""
    resp.json.return_value = {
        "id": "test-resp",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }

    async def _post(url, *, headers=None, json=None, timeout=None):
        captured_headers.update(headers or {})
        return resp

    mock_client = MagicMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

def test_health_returns_required_fields(monkeypatch):
    """GET /health returns status, upstream, routing_lane, and escalation_lane."""
    monkeypatch.setattr(proxy_mod, "_load_config", lambda *_: _TEST_CONFIG)
    monkeypatch.setattr(proxy_mod.events, "init", lambda: None)
    monkeypatch.setattr(proxy_mod.events, "record", lambda *a, **kw: None)

    with TestClient(app) as client:
        r = client.get("/health")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "upstream" in data
    assert "routing_lane" in data
    assert "escalation_lane" in data


# ---------------------------------------------------------------------------
# Lane key selection
# ---------------------------------------------------------------------------

def test_normal_key_used_when_fallback_inactive(monkeypatch):
    """Routing lane key is forwarded to upstream when budget fallback is inactive."""
    monkeypatch.setattr(proxy_mod, "_load_config", lambda *_: _TEST_CONFIG)
    monkeypatch.setattr(proxy_mod.events, "init", lambda: None)
    monkeypatch.setattr(proxy_mod.events, "record", lambda *a, **kw: None)
    monkeypatch.setattr(proxy_mod, "_patch_session_model", AsyncMock())
    monkeypatch.setitem(proxy_mod._budget_fallback_active, "routing", False)

    captured: dict = {}
    with patch("clawconductor.proxy.httpx.AsyncClient", return_value=_make_upstream_mock(captured)):
        with TestClient(app) as client:
            r = client.post("/v1/chat/completions", json=_BODY)

    assert r.status_code == 200
    assert captured.get("Authorization") == "Bearer sk-routing-key"


def test_fallback_key_used_when_budget_fallback_active(monkeypatch):
    """Fallback key is forwarded to upstream when routing lane is in budget fallback."""
    monkeypatch.setattr(proxy_mod, "_load_config", lambda *_: _TEST_CONFIG)
    monkeypatch.setattr(proxy_mod.events, "init", lambda: None)
    monkeypatch.setattr(proxy_mod.events, "record", lambda *a, **kw: None)
    monkeypatch.setattr(proxy_mod, "_patch_session_model", AsyncMock())
    monkeypatch.setitem(proxy_mod._budget_fallback_active, "routing", True)

    captured: dict = {}
    with patch("clawconductor.proxy.httpx.AsyncClient", return_value=_make_upstream_mock(captured)):
        with TestClient(app) as client:
            r = client.post("/v1/chat/completions", json=_BODY)

    assert r.status_code == 200
    assert captured.get("Authorization") == "Bearer sk-fallback-key"
