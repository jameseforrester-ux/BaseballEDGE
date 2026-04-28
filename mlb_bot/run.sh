#!/usr/bin/env bash
# run.sh — Quick-start script for the MLB Prediction Bot

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python version
PYTHON=$(command -v python3 || command -v python)
PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "🐍 Python $PY_VERSION"

# Install deps if needed
if ! "$PYTHON" -c "import telegram" 2>/dev/null; then
    echo "📦 Installing requirements..."
    "$PYTHON" -m pip install -r requirements.txt --quiet
fi

# Check .env
if [ ! -f ".env" ]; then
    echo "⚠️  .env not found — copying from .env.example"
    cp .env.example .env
    echo "✏️  Please edit .env and set your TELEGRAM_BOT_TOKEN, then re-run."
    exit 1
fi

echo "⚾  Starting MLB Prediction Bot..."
"$PYTHON" main.py
