# ClawConductor

ClawConductor is an intelligent model routing proxy that sits between [OpenClaw](https://github.com/anthropics/openclaw) and [LiteLLM](https://github.com/BerriAI/litellm). It automatically routes AI requests to the right model based on task complexity — using a fast, cheap model by default and escalating to a more capable model only when the task warrants it.

---

## Why It Exists

Running every request through a powerful model like Claude Sonnet is expensive and often unnecessary. A simple question doesn't need the same model as a complex debugging session. But manually switching models mid-conversation is tedious and easy to forget.

ClawConductor solves this transparently. It intercepts every request, evaluates what kind of task it is, and routes it to the appropriate model — all without the user or the agent needing to think about it. When it escalates, it tells you why.

---

## What It Does

- **Routes** every request to a lightweight model (Haiku) by default
- **Escalates** automatically to a more capable model (Sonnet) when task complexity triggers are detected
- **Notifies** you via Telegram when escalation happens, with the reason
- **Updates** the OpenClaw TUI status bar to show the real model in use after every response
- **Logs** every routing decision as structured JSON for observability
- **Tracks** spend separately per lane using LiteLLM virtual keys

---

## Architecture

```
OpenClaw (TUI agent)
  │
  ▼
ClawConductor (port 8765)          ← you are here
  │  - Classifies request (Groups A-E)
  │  - Selects per-lane virtual key
  │  - Rewrites model field to tier alias
  │  - Fires Telegram notification on escalation
  │  - Logs routing decision
  │  - Patches OpenClaw status bar
  │
  ▼
LiteLLM (port 4000)
  │  - Resolves tier alias → actual model
  │  - Handles retries and fallbacks
  │
  ▼
Anthropic API (Haiku / Sonnet)
  │  fallback ▼
  Gemini 2.5 Flash (if Anthropic unreachable)
```

---

## Escalation Groups

ClawConductor evaluates five groups of triggers on every request. If any group fires, the request is escalated to the stronger model.

### Group A — Complex Task Keywords
Fires when the user's message contains words that signal the task needs deeper reasoning. A fast cheap model will likely give a poor answer on these — escalate to Sonnet.

**Trigger words:** `plan`, `design`, `architecture`, `debug`, `debugging`, `strategy`, `synthesis`, `research`

---

### Group B — Repeated Tool Failures
Fires when the same task has failed 2 or more times in a row. If Haiku keeps failing at something, stop trying with a weaker model and let Sonnet take over — it's more likely to recover.

**Triggers:** 2 or more consecutive tool failures on the same `task_id`

---

### Group C — Ambiguous or Conflicting Requirements
Fires when the task has missing information, contradictory constraints, or requires weighing tradeoffs. These situations require judgment, not just speed.

**Trigger signals:** `missing_required_input`, `conflicting_constraints`, `requires_tradeoff_reasoning`

> ⚠️ These signals must be injected programmatically into the request context — not auto-detected from natural language yet.

---

### Group D — Validation Failed on Retry
Fires when an output was checked, failed validation, and the agent is trying again. If the first attempt failed, a smarter model should take over rather than repeating the same mistake.

**Triggers:** `validation_failed = true` AND `retry_count >= 1`

> ⚠️ Must be injected programmatically into the request context.

---

### Group E — High-Stakes Actions
Fires when the action is irreversible, security-sensitive, or has significant downstream consequences. You don't want a cheap model making these calls.

**Trigger signals:** `irreversible_change`, `security_sensitive`, `high_downstream_cost`

> ⚠️ Must be injected programmatically into the request context.

---

### Loop Guard
To prevent runaway escalation, ClawConductor enforces **one escalation per `task_id`**. If a task has already been escalated, subsequent requests for the same task stay on the escalation lane without re-triggering the notification.

### Retry Cap
If `retry_count` exceeds `max_retries` (default: 2), the request is routed to `tier/lightweight` unconditionally as a fallback — regardless of escalation triggers.

---

## Model Tiers

| Tier | Model | Used For |
|---|---|---|
| `tier/lightweight` | claude-haiku-4-5 | Default routing lane — all normal requests |
| `tier/standard` | claude-sonnet-4-6 | Available, currently unused |
| `tier/advanced` | claude-sonnet-4-6 | Escalation lane — complex tasks |

All tiers fall back to `gemini-2.5-flash` automatically if Anthropic is unreachable.

---

## Dependencies

| Dependency | Purpose |
|---|---|
| [OpenClaw](https://github.com/anthropics/openclaw) | The AI agent TUI that sends requests through ClawConductor |
| [LiteLLM](https://github.com/BerriAI/litellm) | Model proxy that resolves tier aliases to actual models, handles retries and fallbacks |
| [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) | Async HTTP server for the proxy |
| [httpx](https://www.python-httpx.org/) | Async HTTP client for forwarding requests |
| [PyYAML](https://pyyaml.org/) | Config file parsing |
| Telegram Bot API | Escalation notifications (optional but recommended) |

**Runtime requirements:**
- Python 3.12+
- LiteLLM running on port 4000 with tier aliases configured
- OpenClaw configured with `baseUrl: http://localhost:8765`
- `openclaw` binary accessible for status bar patching

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/mwmatts/clawconductor.git
cd clawconductor
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure LiteLLM tier aliases

Add the following to your `litellm-config.yaml`:

```yaml
model_list:
  - model_name: tier/lightweight
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: tier/standard
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: tier/advanced
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  fallbacks:
    - {"tier/lightweight": ["gemini-2.5-flash"]}
    - {"tier/standard": ["gemini-2.5-flash"]}
    - {"tier/advanced": ["gemini-2.5-flash"]}
```

### 3. Create LiteLLM virtual keys

Create separate virtual keys for each lane so spend is tracked independently:

```bash
# Routing lane key
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"key_alias":"clawconductor-routing","models":["tier/lightweight","tier/standard"],"max_budget":2.50,"budget_duration":"1d"}'

# Escalation lane key
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"key_alias":"clawconductor-escalation","models":["tier/advanced"],"max_budget":2.50,"budget_duration":"1d"}'
```

### 4. Set environment variables

Add the following to your env file (e.g. `~/.openclaw/.env`):

```bash
CLAWCONDUCTOR_ROUTING_KEY=sk-your-routing-key
CLAWCONDUCTOR_ESCALATION_KEY=sk-your-escalation-key

# Optional — Telegram escalation notifications
CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN=your-bot-token
CLAWCONDUCTOR_TELEGRAM_CHAT_ID=your-chat-id
```

### 5. Configure conductor.yaml

```yaml
upstream_url: http://localhost:4000

routing_lane:
  tier: lightweight

escalation_lane:
  tier: advanced

tiers:
  lightweight: tier/lightweight
  standard: tier/standard
  advanced: tier/advanced

tier_display_models:
  lightweight: claude-haiku-4-5
  standard: claude-sonnet-4-6
  advanced: claude-sonnet-4-6

litellm_keys:
  routing: os.environ/CLAWCONDUCTOR_ROUTING_KEY
  escalation: os.environ/CLAWCONDUCTOR_ESCALATION_KEY
```

### 6. Install and start the systemd service

```bash
systemctl --user daemon-reload
systemctl --user enable clawconductor.service
systemctl --user start clawconductor.service
```

Example service file (`~/.config/systemd/user/clawconductor.service`):

```ini
[Unit]
Description=ClawConductor Escalation Proxy
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/youruser/clawconductor
EnvironmentFile=/home/youruser/.openclaw/.env
ExecStart=/home/youruser/clawconductor/.venv/bin/uvicorn clawconductor.proxy:app --port 8765 --host 127.0.0.1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

> ⚠️ The `openclaw` binary must be referenced by its full path in `proxy.py` — it is not on the systemd service PATH by default.

### 7. Point OpenClaw at ClawConductor

In `~/.openclaw/openclaw.json`, set the LiteLLM provider base URL:

```json
{
  "models": {
    "providers": {
      "litellm": {
        "baseUrl": "http://localhost:8765",
        "api": "openai-completions"
      }
    }
  }
}
```

### 8. Verify

```bash
# Check the service is running
systemctl --user status clawconductor.service

# Check the health endpoint
curl http://localhost:8765/health

# Watch the routing log
tail -f ~/.openclaw/logs/clawconductor.log
```

---

## Key Files

| File | Purpose |
|---|---|
| `clawconductor/proxy.py` | FastAPI proxy server — entry point for all requests |
| `clawconductor/classifier.py` | Groups A-E escalation trigger logic |
| `clawconductor/router.py` | Routing decisions — which lane, which tier, why |
| `clawconductor/key_selector.py` | Per-lane LiteLLM virtual key resolution |
| `clawconductor/logger.py` | Structured JSON decision logging + cost logging |
| `clawconductor/loop_guard.py` | One-escalation-per-task enforcement |
| `conductor.yaml` | Runtime configuration |

---

## Tests

```bash
cd clawconductor
source .venv/bin/activate
pytest tests/ -v
```

42 tests covering classifier, router, loop guard, and logger.

---

## Observability

Every routing decision is logged as a JSON line to `~/.openclaw/logs/clawconductor.log`:

```json
{
  "timestamp": "2026-02-25T14:00:00+00:00",
  "trace_id": "abc123",
  "component": "clawconductor",
  "task_id": "a1b2c3d4e5f6",
  "triggered_groups": ["A"],
  "escalation_decision": true,
  "lane": "escalation",
  "tier": "advanced",
  "reason": "triggered groups: ['A']"
}
```
