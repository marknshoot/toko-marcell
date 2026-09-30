#!/usr/bin/env bash
# ==============================================================================
# Toko Marcell — Quickstart Local Runner
#
#   ./run-local.sh
#
# Builds and starts the full stack (Postgres+pgvector, FastAPI, Next.js) and
# waits until the API is healthy. Prompts for an LLM key so the AI copilot works
# out of the box; press Enter to skip it.
#
# Copilot provider: OpenRouter (OpenAI-compatible) when OPENROUTER_API_KEY is
# set, otherwise Google Gemini as a fallback.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  🛍️  Starting Toko Marcell Fullstack Local Environment   "
echo "=========================================================="

# --- 1. Config file -----------------------------------------------------------
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "📋 Created .env from .env.example"
fi

# --- 2. Optional: prompt for an LLM key (copilot) -----------------------------
get_env_value() { # $1 = variable name
  [ -f .env ] || return 0
  grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true
}

is_unset_placeholder() {
  case "$1" in
    ""|your_openrouter_api_key_here|your_gemini_api_key_here) return 0 ;;
    *) return 1 ;;
  esac
}

OR_KEY="$(get_env_value OPENROUTER_API_KEY)"
GEM_KEY="$(get_env_value GEMINI_API_KEY)"

if ! is_unset_placeholder "$OR_KEY"; then
  COPILOT="enabled (OpenRouter)"
elif ! is_unset_placeholder "$GEM_KEY"; then
  COPILOT="enabled (Gemini fallback)"
else
  COPILOT="disabled"
fi

if [ "$COPILOT" = "disabled" ] && [ -t 0 ]; then
  echo ""
  echo "🤖 The AI copilot needs an LLM key — OpenRouter is preferred and free."
  echo "   Get one at https://openrouter.ai/keys  (or press Enter to skip)."
  printf "   OPENROUTER_API_KEY: "
  read -r OR_INPUT || true
  if [ -n "${OR_INPUT:-}" ]; then
    if grep -qE '^OPENROUTER_API_KEY=' .env 2>/dev/null; then
      awk -v key="$OR_INPUT" \
        '{ if ($0 ~ /^OPENROUTER_API_KEY=/) print "OPENROUTER_API_KEY=" key; else print }' \
        .env > .env.tmp && mv .env.tmp .env
    else
      printf '\nOPENROUTER_API_KEY=%s\n' "$OR_INPUT" >> .env
    fi
    COPILOT="enabled (OpenRouter)"
    echo "✅ OpenRouter key saved to .env — the copilot will be enabled."
  else
    echo "⏭️  Skipped — the copilot will be disabled (everything else still works)."
  fi
fi

# --- 3. Bring the stack up ----------------------------------------------------
echo ""
echo "🚀 Launching PostgreSQL (pgvector), FastAPI backend and Next.js storefront..."
echo "   (the first build takes a few minutes; the database seeds itself on boot)"
docker compose up --build -d

echo ""
echo "⏳ Waiting for the API to become healthy..."
for _ in $(seq 1 90); do
  if curl -fsS http://localhost:8001/health > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! curl -fsS http://localhost:8001/health > /dev/null 2>&1; then
  echo "⚠️  The API did not become healthy in time. Check the logs with: docker compose logs -f api"
  exit 1
fi

echo ""
echo "=========================================================="
echo "  ✅ Toko Marcell is running successfully!               "
echo "=========================================================="
echo "  🌐 Storefront:  http://localhost:3000                  "
echo "  ⚡ API Docs:    http://localhost:8001/docs             "
echo "  ❤️ Health:      http://localhost:8001/health           "
echo "----------------------------------------------------------"
echo "  🤖 AI Copilot:  $COPILOT"
if [ "$COPILOT" = "disabled" ]; then
  echo "     → add OPENROUTER_API_KEY to .env and rerun to enable it"
fi
echo "=========================================================="
echo "  To view logs:   docker compose logs -f                 "
echo "  To stop:        docker compose down                    "
echo "=========================================================="
