# ClawConductor
p align="center">  <img src="logo.svg" width="96" alt="ClawConductor">
</p>

<p align="center">
  <strong>Spend less on every AI session. Get better answers when it counts. Works automatically.</strong>
</p>

<p align="center">
  <!-- badges go here once CI and license are confirmed -->
</p>

---

ClawConductor is an intelligent model routing proxy built specifically for [OpenClaw](https://github.com/anthropics/openclaw) and designed to work with [LiteLLM](https://github.com/BerriAI/litellm). It automatically sends simple requests to a cheap model and only escalates to a powerful model when the task actually warrants it — saving you money on every session without you thinking about it.

**Who it's for:** Anyone using OpenClaw who pays for API calls and wants to spend less while getting better answers on the tasks that matter. You don't need to be a developer to benefit — once it's set up, it's invisible.

**What it requires:** OpenClaw and LiteLLM already installed and running. A minimum of two models configured — one per tier — though the more tiers you fill, the more value you get.

**What makes it flexible:** ClawConductor doesn't care which models you use. Every major provider (Anthropic, Google, OpenAI, xAI) has cheap or free low-end models, capable mid-tier models, and powerful high-end models. You decide what goes in each slot based on your credits, budget, and priorities — ClawConductor handles the rest.

---

## Why It Exists

Every request you send through OpenClaw costs money. Most of those requests — checking a file, rephrasing a sentence, answering a simple question — don't need a powerful model. But without routing, every request goes to whatever model you've configured, whether it needs it or not.

ClawConductor fixes this by intercepting every request and asking: does this actually need a powerful model? If not, it routes to a cheap or free model. If yes — a complex task, a repeated failure, a high-stakes action — it escalates automatically. You get the right model for the job every time, without thinking about it.

The result is real, measurable savings. In a typical session, routing lane traffic (the bulk of requests) runs at roughly 4x cheaper than escalation traffic. Tasks that genuinely need a powerful model still get one — they just don't drag the price of everything else up with them.

---

## Documentation

The README covers everything you need to get up and running. For deeper reference material see the wiki:

- [Programmatic Signal Injection Guide](../../wiki/Programmatic-Signal-Injection-Guide) — how to inject Groups C, D, and E signals from your agent
- [Classifier Reference](../../wiki/Classifier-Reference) — how each group is evaluated, how to extend trigger words, phrase matching
- [LiteLLM Configuration Reference](../../wiki/LiteLLM-Configuration-Reference) — provider-specific config examples for Anthropic, Google, OpenAI, xAI, and mixed stacks
- [Troubleshooting](../../wiki/Troubleshooting) — common failure modes, diagnostics, and fixes

---



If you have OpenClaw and LiteLLM already running, this is the fast path to get ClawConductor in place.

```bash
# 1. Clone and install
git clone https://github.com/mwmatts/clawconductor.git
cd clawconductor
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Start the proxy
uvicorn clawconductor.proxy:app --port 8765 --host 127.0.0.1
```

Then point OpenClaw at ClawConductor instead of LiteLLM directly — in `~/.openclaw/openclaw.json`:

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

That's it. ClawConductor is now intercepting every request and routing it automatically.

> For full configuration — tier setup, virtual keys, escalation tuning, systemd service — see the [Setup](#setup) section below.

---

## Real-World Cost Savings

Here's what a real ClawConductor session looks like from the LiteLLM spend data:

```
┌─────────────────────┬──────────────────────────┬───────┐
│        Lane         │           Key            │ Spent │
├─────────────────────┼──────────────────────────┼───────┤
│ Routing (Haiku)     │ clawconductor-routing    │ $1.43 │
├─────────────────────┼──────────────────────────┼───────┤
│ Escalation (Sonnet) │ clawconductor-escalation │ $1.34 │
├─────────────────────┼──────────────────────────┼───────┤
│ Direct / legacy     │ openclaw-main            │ $0.71 │
└─────────────────────┴──────────────────────────┴───────┘
```

The routing lane handled the bulk of requests at Haiku prices (~$0.80 per million input tokens). If all of that traffic had gone through Sonnet (~$3.00 per million input tokens), the same $1.43 in routing spend would have cost roughly $5–6 instead.

**Estimated saving on routing lane traffic alone: 60–65%.**

The escalation lane ($1.34) is close in dollar terms because Sonnet costs 4x more per token and escalated tasks tend to be heavier. But that spend only covers the requests that genuinely needed it — everything else ran cheap.

> 📝 Your actual savings will vary depending on which models you configure, how you stack your tiers, and what kinds of tasks you run. The numbers above reflect a real session using Claude Haiku (routing) and Claude Sonnet (escalation).

**What is the "Direct / legacy" lane?** The `openclaw-main` key represents requests sent directly to LiteLLM before ClawConductor was fully in place — bypassing routing entirely. Once all traffic flows through ClawConductor this key goes to zero. If you're still seeing spend here it means part of your stack is pointing directly at LiteLLM rather than at ClawConductor on port 8765. Check your OpenClaw config and any other tools that may be calling LiteLLM directly.

---

- **Routes** every request to a lightweight model by default
- **Escalates** automatically to a more capable model when task complexity triggers are detected
- **Notifies** you via Telegram when escalation happens, with the reason (optional — see Dependencies)
- **Updates** the OpenClaw TUI status bar to show the real model in use after every response
- **Logs** every routing decision as structured JSON for observability
- **Tracks** spend separately per lane using LiteLLM virtual keys

---

## Architecture

```
OpenClaw (TUI agent)
  │
  ▼
ClawConductor (configurable port, default 8765)     ← you are here
  │  - Classifies request (Groups A-E)
  │  - Selects per-lane virtual key
  │  - Rewrites model field to tier alias
  │  - Fires Telegram notification on escalation or budget cap
  │  - Falls back to free model when budget exceeded
  │  - Logs routing decision
  │  - Patches OpenClaw status bar
  │
  ▼
LiteLLM (configurable port, default 4000)
  │  - Resolves tier alias → actual model
  │  - Tracks per-key spend and enforces budget caps
  │
  ├──► Your configured models (lightweight → advanced)   ← normal path
  │
  └──► Your configured free fallback model               ← budget exhausted path
```

---

## Model Tiers

ClawConductor has three routing tiers plus a guaranteed free fallback beneath all of them. The tiers are just slots — you decide what model goes in each one.

| Tier | Role | Examples |
|---|---|---|
| `tier/lightweight` | Default lane — handles all normal requests | Gemini Flash, Claude Haiku, Grok Mini |
| `tier/standard` | Mid lane — optional middle escalation step | Gemini Pro, Claude Sonnet, GPT-4o |
| `tier/advanced` | Escalation lane — complex or high-stakes tasks | Claude Opus, GPT-5, Gemini Ultra |
| *(free fallback)* | Safety net — used only if all tiers fail or exhaust budget | Any free-tier model (e.g. Gemini Flash free tier) |

**Minimum to be useful:** 2 tiers filled (lightweight + advanced). With only one model there is nothing to route between.

**The free fallback** sits below all three tiers and is not part of normal routing. It exists solely to ensure OpenClaw never goes down — if every tier exhausts its budget or becomes unreachable, requests fall through to this model automatically.

---

## Budget Fallback

When a lane's daily spend limit is hit, LiteLLM returns an error instead of processing the request. ClawConductor catches this and automatically switches that lane to a free fallback model so the session keeps running without interruption.

### How it works

1. Request goes to LiteLLM on the normal lane key
2. LiteLLM returns HTTP 400 with `budget_exceeded` in the error body
3. ClawConductor marks that lane as in fallback mode
4. A Telegram notification fires:
   ```
   💸 Budget cap hit: routing lane ($2.50/day)
   Switching to free model until midnight UTC.
   Last model: claude-haiku-4-5 | ID: a3f8c2
   ```
5. The request is immediately retried with the fallback key and free model — no response is dropped
6. All subsequent requests on that lane go directly to the free model (no wasted paid API calls)

### Recovering

The lane returns to the paid model when either:

- **Budget is bumped manually** — use the `bump-budget` script:
  ```bash
  bump-budget routing 5.00     # set routing lane to $5.00
  bump-budget escalation 5.00  # set escalation lane to $5.00
  bump-budget all 5.00         # set both lanes
  ```
  This updates the LiteLLM key budget AND resets the ClawConductor fallback state. A recovery notification fires:
  ```
  ✅ Budget restored: routing lane
  Switching back to standard model.
  ```

- **Daily reset at midnight** — the `litellm-reset-spend.timer` cron job resets spend to zero and calls `/admin/reset-fallback?lane=all` automatically.

### Dedicated fallback key

The fallback uses a separate LiteLLM virtual key (`CLAWCONDUCTOR_FALLBACK_KEY`) configured with only the free model and no budget limit. This keeps fallback traffic isolated from the per-lane spend tracking.

---

## How to Stack Your Tiers

The tiers are provider-agnostic. How you fill them depends on your situation:

**Cost-first stack** — fill all tiers with free or cheap models from any provider. Route normally and escalate without ever paying much. Example: Gemini Flash → Gemini Pro → Haiku.

**Credit-burn stack** — you have credits on a powerful model (e.g. GPT-5) but pay out of pocket for your mid-tier (e.g. Sonnet). Put the credit model at `tier/advanced` so escalations burn credits first. When credits run out, LiteLLM's fallback kicks it over to the paid model. Example: Haiku → GPT-5 (credits) → Sonnet (paid fallback).

**Safety-net stack** — fill all three tiers with your preferred models, then configure a free model below everything as the guaranteed fallback. Even if all budgets are exhausted, OpenClaw keeps running.

---

## Escalation Groups

ClawConductor evaluates five groups of triggers on every request. If any group fires, the request is escalated to the stronger model. Groups A and B fire automatically from natural language and agent behaviour. Groups C, D, and E require signals to be injected programmatically into the request context by your agent or tooling.

---

### Group A — Complex Task Keywords

**What it does:** Scans the user's message for words that signal the task needs deeper reasoning. Cheap fast models tend to give shallow or incorrect answers on these — escalating saves you the cost of a bad result and a retry.

The default word list is a starting point. Your agent's vocabulary will differ depending on how you use it — a developer, a researcher, and a business analyst will all phrase complex requests differently. Pick a template below that fits your use case, or mix and match words across templates into your own list.

**Default trigger words:** `plan`, `design`, `architecture`, `debug`, `debugging`, `strategy`, `synthesis`, `research`, `refactor`, `optimize`, `migrate`, `audit`, `review`, `diagnose`, `evaluate`, `analyze`, `compare`, `recommend`

---

#### Trigger Word Templates

> 📝 These are example starting points. Copy the one closest to your use case and add or remove words to match how you actually talk to your agent. The word list is configured in `conductor.yaml` under `trigger_words` — see the Setup section for where this fits in the config file.

---

**Template 1 — Software Developer**
Best for: coding agents, code review, refactoring, infrastructure work.
```yaml
trigger_words:
  - debug, debugging, refactor, optimize, migrate, audit, review
  - architecture, design, plan, strategy, diagnose, performance
  - security, test, coverage, profile, trace, regression
  - implement, scaffold, integrate, deprecate, patch
```

---

**Template 2 — DevOps / Infrastructure**
Best for: deployment agents, incident response, system configuration.
```yaml
trigger_words:
  - deploy, rollback, provision, configure, diagnose, incident
  - monitor, alert, escalate, triage, investigate, remediate
  - migrate, failover, restore, backup, audit, harden
  - capacity, bottleneck, latency, throughput, outage
```

---

**Template 3 — Research & Analysis**
Best for: research agents, report generation, data analysis, writing.
```yaml
trigger_words:
  - analyze, research, synthesize, evaluate, compare, contrast
  - recommend, assess, critique, forecast, model, predict
  - summarize, explain, justify, argue, investigate, explore
  - tradeoff, implication, consequence, impact, significance
```

---

**Template 4 — Business & Operations**
Best for: non-technical power users managing projects, decisions, planning.
```yaml
trigger_words:
  - plan, strategy, prioritize, decide, recommend, evaluate
  - risk, tradeoff, impact, budget, forecast, justify
  - improve, optimize, streamline, review, assess, propose
  - stakeholder, outcome, objective, milestone, bottleneck
```

---

**Template 5 — General / Layperson**
Best for: personal assistants and general-purpose agents used by non-technical users who may not use precise technical language but are still asking complex questions.
```yaml
trigger_words:
  - figure out, help me understand, what should I, how do I
  - is it better to, what's the difference, walk me through
  - I'm not sure, I don't know how, can you explain, why is it
  - what would happen if, help me decide, what are my options
  - something is wrong, it's not working, I think I messed up
```

> 💡 The layperson template uses phrases rather than single words. This requires phrase matching support in `classifier.py` — single-word matching will not catch these. Check your classifier implementation before using this template.

---

**Examples of messages that fire Group A (default list):**
- *"Debug why this function returns None intermittently"* → `debug` fires
- *"Design a schema for a multi-tenant SaaS app"* → `design` fires
- *"What's our strategy for handling rate limits across agents?"* → `strategy` fires
- *"Refactor this module to remove the circular dependency"* → `refactor` fires
- *"Audit the permissions on these API keys"* → `audit` fires
- *"Compare these two approaches and recommend one"* → `compare`, `recommend` fire

**Examples that do NOT fire Group A:**
- *"What does this function do?"* — explanation, not deep reasoning
- *"Rename this variable to something clearer"* — simple edit
- *"Summarize this file"* — lightweight task
- *"What time is it in Tokyo?"* — factual lookup

---

### Group B — Repeated Tool Failures

**What it does:** Tracks consecutive tool failures per `task_id`. If the lightweight model has already failed at the same task twice, it is unlikely to succeed on a third attempt. Escalate rather than burn tokens on repeated failure.

**Triggers:** 2 or more consecutive tool failures on the same `task_id`

**Examples of when Group B fires:**
- A file-writing tool fails twice because the model keeps generating a malformed path
- A code execution tool fails twice because the generated code has a syntax error the model can't self-correct
- An API call tool fails twice because the model is constructing the wrong request shape

**Why the threshold is 2:** One failure can be transient (network hiccup, malformed output). Two failures in a row on the same task is a signal the model is stuck, not unlucky.

---

### Group C — Ambiguous or Conflicting Requirements

**What it does:** Fires when the task context signals that the request has missing information, contradictory constraints, or requires the model to reason through tradeoffs. These situations need judgment — a lightweight model will often pick an arbitrary path rather than surface the conflict.

**Trigger signals:** `missing_required_input`, `conflicting_constraints`, `requires_tradeoff_reasoning`

**Examples of when Group C fires:**
- A task asks the agent to write to a file path that hasn't been defined yet → `missing_required_input`
- A task says "make it faster" and "don't change the algorithm" → `conflicting_constraints`
- A task asks the agent to choose between two valid but incompatible approaches → `requires_tradeoff_reasoning`

> ⚠️ These signals must be injected programmatically into the request context by your agent or orchestration layer — ClawConductor does not auto-detect ambiguity from natural language yet.

---

### Group D — Validation Failed on Retry

**What it does:** Fires when an output has already been checked, failed validation, and the agent is attempting the task again. Rather than letting the same model repeat the same mistake, escalate to a stronger model for the retry.

**Triggers:** `validation_failed = true` AND `retry_count >= 1`

**Examples of when Group D fires:**
- A code generation task produced output that failed a lint check, and the agent is retrying
- A structured data extraction task returned malformed JSON that failed schema validation
- A summarization task was flagged as too long by a downstream length check, and the agent is trying again

**Why this matters:** Without Group D, a failed task just retries on the same model with the same context — often producing the same bad output. Escalating on a validated failure breaks the loop.

> ⚠️ Must be injected programmatically into the request context.

---

### Group E — High-Stakes Actions

**What it does:** Fires when the action about to be taken is irreversible, touches security-sensitive systems, or has significant downstream consequences. The cost of a mistake on these far exceeds the cost of escalation.

**Trigger signals:** `irreversible_change`, `security_sensitive`, `high_downstream_cost`

**Examples of when Group E fires:**
- Deleting files or database records → `irreversible_change`
- Modifying API keys, secrets, or access control rules → `security_sensitive`
- Triggering a deployment, payment, or external notification → `high_downstream_cost`
- Overwriting a production config file → `irreversible_change` + `high_downstream_cost`

> ⚠️ Must be injected programmatically into the request context. Your agent or tool layer is responsible for tagging actions before they reach ClawConductor.

---

### Loop Guard

To prevent runaway escalation costs, ClawConductor enforces **one escalation per `task_id`**. Once a task has been escalated, all subsequent requests for that task stay on the escalation lane for its full duration — without re-triggering the notification or re-evaluating triggers. A complex multi-step task escalates once and stays escalated. It does not bounce between tiers mid-task.

### Retry Cap

If `retry_count` exceeds `max_retries` (default: 2), the request is routed back to `tier/lightweight` unconditionally — regardless of any active escalation triggers. This acts as a circuit breaker to prevent a stuck task from burning through your escalation budget indefinitely.

---

## Known Limitations

**These are current constraints to be aware of before deploying:**

Groups C, D, and E (ambiguous requirements, validation failures, high-stakes actions) require signals to be injected programmatically into the request context. ClawConductor cannot detect these conditions from natural language automatically — your agent or orchestration layer must tag them explicitly. Groups A and B work automatically with no changes needed.

The trigger word list in Group A currently lives in `classifier.py` rather than in `conductor.yaml`. This means adding or changing trigger words requires editing source code rather than config. This is a known limitation and will be moved to config in a future release.

The layperson trigger word template uses phrase matching rather than single-word matching. Single-word matching (the current default in `classifier.py`) will not catch multi-word phrases like "help me decide" or "something is wrong." Verify your classifier implementation supports phrase matching before using that template.

The free fallback safety net (a guaranteed model below all tiers that fires if all tier budgets are exhausted) has not been verified as implemented. If OpenClaw going down during a budget exhaustion event is unacceptable for your use case, confirm this is in place before relying on it in production.

---

## Roadmap

Short-term items being tracked:

- Move `trigger_words` from `classifier.py` into `conductor.yaml` so word lists are configurable without touching source code
- Verify and implement the guaranteed free fallback model below all tiers
- Add phrase matching support to `classifier.py` to enable the layperson trigger template
- Natural language auto-detection for Groups C, D, and E (currently requires programmatic injection)

---



| Dependency | Purpose |
|---|---|
| [OpenClaw](https://github.com/anthropics/openclaw) | **Required.** The AI agent TUI that sends requests through ClawConductor |
| [LiteLLM](https://github.com/BerriAI/litellm) | **Required.** Model proxy that resolves tier aliases to actual models, handles retries and fallbacks |
| [FastAPI](https://fastapi.tiangolo.com/) + [uvicorn](https://www.uvicorn.org/) | Async HTTP server for the proxy |
| [httpx](https://www.python-httpx.org/) | Async HTTP client for forwarding requests |
| [PyYAML](https://pyyaml.org/) | Config file parsing |
| Telegram Bot API | **Optional.** Escalation notifications via Telegram. If not configured, escalations are logged locally but no notification is sent — ClawConductor runs normally without it. |

**Runtime requirements:**
- Python 3.12+
- LiteLLM running (default port 4000, configurable)
- OpenClaw configured to point at ClawConductor (default port 8765, configurable)
- `openclaw` binary accessible for status bar patching

---

## Setup

### Prerequisites
Before proceeding, confirm the following are already working:
- OpenClaw is installed and you can run a session
- LiteLLM is running and reachable

### 1. Clone and install

```bash
git clone https://github.com/mwmatts/clawconductor.git
cd clawconductor
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure LiteLLM tier aliases

Add the following to your `litellm-config.yaml`.

> 📝 The models below are examples using Claude Haiku and Sonnet. Substitute any LiteLLM-supported models to match your setup. The fallback model should ideally be free-tier to guarantee OpenClaw stays running even if all budgets are exhausted.

```yaml
model_list:
  - model_name: tier/lightweight
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001  # replace with your preferred lightweight model
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: tier/standard
    litellm_params:
      model: anthropic/claude-sonnet-4-6           # replace with your preferred standard model
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: tier/advanced
    litellm_params:
      model: anthropic/claude-sonnet-4-6           # replace with your preferred advanced model
      api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  fallbacks:
    - {"tier/lightweight": ["your-free-fallback-model"]}   # replace with your preferred free fallback
    - {"tier/standard": ["your-free-fallback-model"]}
    - {"tier/advanced": ["your-free-fallback-model"]}
```

### 3. Create LiteLLM virtual keys

Create separate virtual keys for each lane so spend is tracked independently.

> 📝 The budget values below (`2.50`, `1d`) are examples — set limits that match your usage and risk tolerance.

```bash
# Routing lane key
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"key_alias":"clawconductor-routing","models":["tier/lightweight","tier/standard"],"max_budget":2.50,"budget_duration":"1d"}'

# Escalation lane key
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"key_alias":"clawconductor-escalation","models":["tier/advanced"],"max_budget":2.50,"budget_duration":"1d"}'

# Fallback key — free model only, no budget limit
curl -X POST http://localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"key_alias":"clawconductor-fallback","models":["gemini-2.5-flash"]}'
```

### 4. Set environment variables

Add the following to your env file (e.g. `~/.openclaw/.env`):

```bash
CLAWCONDUCTOR_ROUTING_KEY=sk-your-routing-key
CLAWCONDUCTOR_ESCALATION_KEY=sk-your-escalation-key
CLAWCONDUCTOR_FALLBACK_KEY=sk-your-fallback-key

# Optional — Telegram escalation notifications
# If omitted, escalations are logged locally only
CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN=your-bot-token
CLAWCONDUCTOR_TELEGRAM_CHAT_ID=your-chat-id
```

### 5. Configure conductor.yaml

> 📝 Tier names (`lightweight`, `standard`, `advanced`) map to whatever models you configured in LiteLLM. Display model names are cosmetic only — they appear in the OpenClaw status bar and logs. The `trigger_words` list is where you paste your chosen template from the Escalation Groups section above.

```yaml
upstream_url: http://localhost:4000  # your LiteLLM port

routing_lane:
  tier: lightweight

escalation_lane:
  tier: advanced

tiers:
  lightweight: tier/lightweight
  standard: tier/standard
  advanced: tier/advanced

tier_display_models:
  lightweight: claude-haiku-4-5       # cosmetic display name — set to match your actual model
  standard: claude-sonnet-4-6
  advanced: claude-sonnet-4-6

context_token_limit: 40000

budget_fallback:
  model: gemini-2.5-flash             # replace with your preferred free fallback model
  display_name: Gemini 2.5 Flash

litellm_keys:
  routing: os.environ/CLAWCONDUCTOR_ROUTING_KEY
  escalation: os.environ/CLAWCONDUCTOR_ESCALATION_KEY
  fallback: os.environ/CLAWCONDUCTOR_FALLBACK_KEY

trigger_words:                        # paste your chosen template here, or use the defaults
  - plan, design, architecture, debug, debugging, strategy
  - synthesis, research, refactor, optimize, migrate, audit
  - review, diagnose, evaluate, analyze, compare, recommend
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

In `~/.openclaw/openclaw.json`, set the LiteLLM provider base URL to point at ClawConductor instead of LiteLLM directly:

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

**Expected healthy output from the health endpoint:**
```json
{"status": "ok", "upstream": "http://localhost:4000", "routing_lane": "tier/lightweight", "escalation_lane": "tier/advanced"}
```

**Expected log entry after your first request:**
```json
{"timestamp": "...", "component": "clawconductor", "task_id": "...", "triggered_groups": [], "escalation_decision": false, "lane": "routing", "tier": "lightweight", "reason": "no triggers fired"}
```

If the health endpoint returns an error or the log shows no entries after sending a message, check that LiteLLM is reachable on its configured port and that your virtual keys are valid.

---

## Key Files

| File | Purpose |
|---|---|
| `clawconductor/proxy.py` | FastAPI proxy server — entry point for all requests |
| `clawconductor/classifier.py` | Groups A-E escalation trigger logic. Trigger words are configured in `conductor.yaml` |
| `clawconductor/router.py` | Routing decisions — which lane, which tier, why |
| `clawconductor/key_selector.py` | Per-lane LiteLLM virtual key resolution |
| `clawconductor/logger.py` | Structured JSON decision logging + cost logging |
| `clawconductor/loop_guard.py` | One-escalation-per-task enforcement |
| `conductor.yaml` | Runtime configuration — models, tiers, trigger words |

---

## Contributing

ClawConductor is a personal project — it's not actively seeking outside contributors or pull requests at this time.

That said, if you're using it and run into a bug, have a question, or want to suggest something, opening a GitHub issue is welcome. No guarantees on response time, but it's the right place to leave feedback.

---



```bash
cd clawconductor
source .venv/bin/activate
pytest tests/ -v
```

42 tests covering classifier, router, loop guard, and logger.

---

## Observability

Every routing decision is logged as a JSON line to `~/.openclaw/logs/clawconductor.log`. This is your primary tool for understanding why a request was routed the way it was — whether it escalated, which group fired, and what lane it ended up on.

**Example — normal request, no escalation:**
```json
{
  "timestamp": "2026-02-25T14:00:00+00:00",
  "trace_id": "abc123",
  "component": "clawconductor",
  "task_id": "a1b2c3d4e5f6",
  "triggered_groups": [],
  "escalation_decision": false,
  "lane": "routing",
  "tier": "lightweight",
  "reason": "no triggers fired"
}
```

**Example — escalated request, Group A fired:**
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
