#!/bin/bash
# Reset LiteLLM virtual key spend at midnight EST
set -e

ENV_FILE="$HOME/.openclaw/.env"
OPENCLAW_JSON="$HOME/.openclaw/openclaw.json"
LITELLM_URL="http://localhost:4000"

# Load env vars
set -a
source "$ENV_FILE"
set +a

# Get current virtual key from openclaw.json
KEY=$(python3 -c "import json; d=json.load(open('$OPENCLAW_JSON')); print(d['models']['providers']['litellm']['apiKey'])")

if [ -z "$KEY" ]; then
    echo "$(date): ERROR - could not read virtual key from openclaw.json" >&2
    exit 1
fi

# Reset spend to 0
RESPONSE=$(curl -s -X POST "$LITELLM_URL/key/update" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"key\": \"$KEY\", \"spend\": 0}")

if echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('spend') == 0.0" 2>/dev/null; then
    echo "$(date): spend reset to 0 for key ${KEY: -6}"
else
    echo "$(date): ERROR - unexpected response: $RESPONSE" >&2
    exit 1
fi

# Restart LiteLLM to clear in-memory budget cache
XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart litellm.service
echo "$(date): LiteLLM restarted to clear budget cache"

# Wait for LiteLLM to be healthy before finishing
for i in $(seq 1 12); do
    sleep 10
    if curl -sf "http://localhost:4000/health/readiness" > /dev/null 2>&1; then
        echo "$(date): LiteLLM healthy after restart"
        # Reset ClawConductor budget fallback state (so paid models resume automatically)
        RESET_RESPONSE=$(curl -s -X POST "http://localhost:8765/admin/reset-fallback?lane=all")
        echo "$(date): ClawConductor fallback reset: $RESET_RESPONSE"
        exit 0
    fi
done

echo "$(date): WARNING - LiteLLM did not become healthy within 120s" >&2
exit 1
