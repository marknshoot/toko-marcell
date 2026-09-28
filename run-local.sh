#!/usr/bin/env bash
# ==============================================================================
# Toko Marcell — Quickstart Local Runner
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo "  🛍️  Starting Toko Marcell Fullstack Local Environment   "
echo "=========================================================="

if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    echo "📋 .env not found. Creating from .env.example..."
    cp .env.example .env
    echo "💡 Note: Set your GEMINI_API_KEY in .env to activate the AI Copilot."
  fi
fi

echo "🚀 Launching PostgreSQL (pgvector), FastAPI Backend, and Next.js Storefront..."
docker compose up --build -d

echo ""
echo "⏳ Waiting for services to be ready..."
until curl -s http://localhost:8001/health > /dev/null; do
  sleep 2
done

echo ""
echo "=========================================================="
echo "  ✅ Toko Marcell is running successfully!               "
echo "=========================================================="
echo "  🌐 Storefront:  http://localhost:3000                  "
echo "  ⚡ API Docs:    http://localhost:8001/docs             "
echo "  ❤️ Health:      http://localhost:8001/health           "
echo "=========================================================="
echo "  To view logs:   docker compose logs -f                 "
echo "  To stop:        docker compose down                    "
echo "=========================================================="
