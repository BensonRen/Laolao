#!/usr/bin/env bash
# Laolao startup script (Linux / macOS)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Forward any extra args to server.py
# Examples:
#   ./run.sh --model small
#   ./run.sh --language auto
#   ./run.sh --list-devices
exec python server.py "$@"
