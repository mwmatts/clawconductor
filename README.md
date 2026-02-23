# ClawConductor

Lightweight escalation middleware that sits between OpenClaw and LiteLLM. Evaluates Groups A-E escalation triggers and routes tasks to the correct model lane.

## Escalation Groups

| Group | Trigger |
|-------|---------|
| **A** | Explicit task class flag (`plan`, `design`, `architecture`, `debugging`, `strategy`, `synthesis`, `research`) |
| **B** | `consecutive_tool_failures >= 2` |
| **C** | `missing_required_input`, `conflicting_constraints`, or `requires_tradeoff_reasoning` |
| **D** | `validation_failed` on retry |
| **E** | `irreversible_change`, `security_sensitive`, or `high_downstream_cost` |

## Lanes

- **Routing lane** — default lane for standard tasks (e.g. `claude-sonnet-4-6`)
- **Escalation lane** — stronger model for triggered tasks (e.g. `claude-opus-4-6`)

A loop guard enforces **one escalation per `task_id`** to prevent infinite escalation loops.

## Install

```bash
pip install -e ".[dev]"
```

## Config

Edit `conductor.yaml` to set models and LiteLLM virtual keys:

```yaml
routing_lane:
  model: anthropic/claude-sonnet-4-6

escalation_lane:
  model: anthropic/claude-opus-4-6

litellm_keys:
  routing: sk-routing-YOUR-KEY
  escalation: sk-escalation-YOUR-KEY
```

## Usage

```python
from clawconductor.router import route
from clawconductor.loop_guard import LoopGuard
from clawconductor.key_selector import select_key
from clawconductor.logger import setup_logging, log_decision

setup_logging()
guard = LoopGuard()

ctx = {
    "task_id": "task-42",
    "task_class": "debugging",
    "consecutive_tool_failures": 0,
    "signals": [],
}

decision = route(ctx, loop_guard=guard)
log_decision(decision)

key = select_key(decision.lane)
# Pass decision.model + key to LiteLLM
```

## Tests

```bash
pytest
```
