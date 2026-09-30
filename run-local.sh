#!/usr/bin/env bash
# ==============================================================================
# Toko Marcell — Quickstart Local Runner
#
#   ./run-local.sh
#
# Builds and starts the full stack (Postgres+pgvector, FastAPI, Next.js) and
# waits until the API is healthy. Prompts for a free Google Gemini API key so
# the AI copilot works out of the box; press Enter to skip it.
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

# --- 2. Optional: prompt for the Gemini key (copilot) -------------------------
gemini_key_value() {
  [ -f .env ] || return 0
  grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ' || true
}

KEY_VALUE="$(gemini_key_value)"
case "$KEY_VALUE" in
  ""|your_gemini_api_key_here) COPILOT="disabled" ;;
  *)                           COPILOT="enabled"  ;;
esac

if [ "$COPILOT" = "disabled" ] && [ -t 0 ]; then
  echo ""
  echo "🤖 The AI copilot needs a free Google Gemini API key."
  echo "   Get one at https://aistudio.google.com/  (or press Enter to skip)."
  printf "   GEMINI_API_KEY: "
  read -r GEMINI_KEY || true
  if [ -n "${GEMINI_KEY:-}" ]; then
    if grep -qE '^GEMINI_API_KEY=' .env 2>/dev/null; then
      awk -v key="$GEMINI_KEY" \
        '{ if ($0 ~ /^GEMINI_API_KEY=/) print "GEMINI_API_KEY=" key; else print }' \
        .env > .env.tmp && mv .env.tmp .env
    else
      printf '\nGEMINI_API_KEY=%s\n' "$GEMINI_KEY" >> .env
    fi
    COPILOT="enabled"
    echo "✅ Gemini key saved to .env — the copilot will be enabled."
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
  echo "     → add GEMINI_API_KEY to .env and rerun to enable it"
fi
echo "=========================================================="
echo "  To view logs:   docker compose logs -f                 "
echo "  To stop:        docker compose down                    "
echo "=========================================================="
