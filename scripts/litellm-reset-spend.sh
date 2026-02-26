#!/bin/bash
# Reset LiteLLM virtual key spend at midnight EST.
# Also sends daily Telegram report and verifies health after reset.
set -e

ENV_FILE="$HOME/.openclaw/.env"
OPENCLAW_JSON="$HOME/.openclaw/openclaw.json"
LITELLM_URL="http://localhost:4000"
CLAWCONDUCTOR_URL="http://localhost:8765"

# Load env vars (LITELLM_MASTER_KEY, CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN, etc.)
set -a
source "$ENV_FILE"
set +a

# Helper: send a Telegram message (non-fatal if it fails)
_telegram() {
    local text="$1"
    if [ -n "$CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN" ] && [ -n "$CLAWCONDUCTOR_TELEGRAM_CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${CLAWCONDUCTOR_TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CLAWCONDUCTOR_TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${text}" \
            -d "parse_mode=Markdown" > /dev/null 2>&1 || true
    fi
}

# --- Step 1: Grab yesterday's daily report BEFORE resetting ---
echo "$(date): Fetching daily report..."
DAILY_REPORT=$(curl -s "${CLAWCONDUCTOR_URL}/admin/daily-report" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('report','(report unavailable)'))" 2>/dev/null || echo "(report unavailable)")

# --- Step 2: Get current virtual key ---
KEY=$(python3 -c "import json; d=json.load(open('$OPENCLAW_JSON')); print(d['models']['providers']['litellm']['apiKey'])")

if [ -z "$KEY" ]; then
    echo "$(date): ERROR - could not read virtual key from openclaw.json" >&2
    _telegram "⚠️ Midnight reset FAILED: could not read virtual key from openclaw.json"
    exit 1
fi

# --- Step 3: Reset LiteLLM spend to 0 ---
RESPONSE=$(curl -s -X POST "$LITELLM_URL/key/update" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"$KEY\", \"spend\": 0}")

if echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('spend') == 0.0" 2>/dev/null; then
    echo "$(date): spend reset to 0 for key ${KEY: -6}"
else
    echo "$(date): ERROR - unexpected response: $RESPONSE" >&2
    _telegram "⚠️ Midnight reset FAILED: LiteLLM key update returned unexpected response"
    exit 1
fi

# --- Step 4: Restart LiteLLM to clear in-memory budget cache ---
XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart litellm.service
echo "$(date): LiteLLM restarted to clear budget cache"

# --- Step 5: Wait for LiteLLM healthy, then reset ClawConductor fallback ---
RESET_OK=false
for i in $(seq 1 12); do
    sleep 10
    if curl -sf "${LITELLM_URL}/health/readiness" > /dev/null 2>&1; then
        echo "$(date): LiteLLM healthy after restart"

        # Reset ClawConductor budget fallback state
        RESET_RESPONSE=$(curl -s -X POST "${CLAWCONDUCTOR_URL}/admin/reset-fallback?lane=all")
        echo "$(date): ClawConductor fallback reset: $RESET_RESPONSE"

        # Reset in-memory metrics (so today's counters start fresh)
        curl -s -X POST "${CLAWCONDUCTOR_URL}/admin/reset-metrics" > /dev/null 2>&1 || true
        echo "$(date): ClawConductor metrics reset"

        RESET_OK=true
        break
    fi
done

if [ "$RESET_OK" = false ]; then
    echo "$(date): WARNING - LiteLLM did not become healthy within 120s" >&2
    DAILY_REPORT="${DAILY_REPORT}

⚠️ Midnight reset: LiteLLM failed to come back healthy within 120s!"
    _telegram "$DAILY_REPORT"
    exit 1
fi

# --- Step 6: Post-reset health check ---
sleep 2
HEALTH=$(curl -s "${CLAWCONDUCTOR_URL}/admin/status" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('health','unknown'))" 2>/dev/null || echo "unknown")
echo "$(date): Post-reset health: $HEALTH"

if [ "$HEALTH" = "nominal" ]; then
    RESET_STATUS="✓ Midnight reset: spend zeroed, fallback cleared, health nominal"
else
    RESET_STATUS="⚠️ Midnight reset: health is ${HEALTH} after reset — check ClawConductor!"
    _telegram "⚠️ Post-reset health check FAILED: status is '${HEALTH}'. Fallback may not have cleared."
fi

# --- Step 7: Send daily digest to Telegram ---
FULL_REPORT="${DAILY_REPORT}

${RESET_STATUS}"
echo "$(date): Sending daily report to Telegram..."
_telegram "$FULL_REPORT"
echo "$(date): Daily report sent."
