#!/usr/bin/env bash
# Laolao setup script (Linux / macOS)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Laolao Setup ==="
echo

# Find a Python new enough to install everything.
#
# 3.10 is the real floor, not 3.9: pyvirtualcam publishes no wheel below it, and
# pip aborts the WHOLE install when one requirement is unsatisfiable -- so on the
# stock macOS interpreter (3.9, from the Command Line Tools) setup used to fail
# having installed nothing at all. Search for a usable interpreter instead of
# taking whichever python3 happens to be first on PATH, the way
# docs/snapdragon/setup-arm64.ps1 already does on Windows.
MIN_MAJOR=3
MIN_MINOR=10

py_ok() {
    [ -x "$(command -v "$1" 2>/dev/null)" ] || return 1
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" 2>/dev/null
}

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if py_ok "$candidate"; then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python ${MIN_MAJOR}.${MIN_MINOR}+ is required and none was found."
    if command -v python3 &>/dev/null; then
        echo "       The python3 on your PATH is $(python3 -V 2>&1)."
    fi
    echo "       macOS: brew install python@3.12"
    echo "       Or install from https://python.org"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print('%d.%d' % sys.version_info[:2])")
echo "Python: $PYTHON ($PY_VERSION)"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv venv
fi

source venv/bin/activate
echo "Virtual environment: $(which python)"

# Upgrade pip
pip install --quiet --upgrade pip

# Check for CUDA
if python -c "import torch; print(torch.cuda.is_available())" 2>/dev/null | grep -q "True"; then
    echo "NVIDIA GPU detected — installing torch+cuda version..."
    pip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet
fi

# Pick the requirements file that matches this machine.
#
# This used to install requirements.txt unconditionally, which meant an Apple
# Silicon Mac never got mlx-whisper -- so config.json's "device": "mlx" silently
# fell through to faster-whisper on the CPU. The app still worked, just several
# times slower, and nothing said why. Choose explicitly and say which was chosen.
REQ_FILE="requirements.txt"
if [[ "$OSTYPE" == "darwin"* && "$(uname -m)" == "arm64" ]]; then
    REQ_FILE="requirements-macos.txt"
fi

echo "Installing dependencies from $REQ_FILE ..."
pip install -r "$REQ_FILE"

# Apple Silicon: the Neural Engine path is the whole point of this platform, so
# a missing mlx-whisper is worth a loud warning rather than a silent slow mode.
if [[ "$REQ_FILE" == "requirements-macos.txt" ]]; then
    if python -c "import mlx_whisper" 2>/dev/null; then
        echo "  MLX (Apple Neural Engine) backend available."
    else
        echo "  WARNING: mlx-whisper did not install. Laolao will fall back to"
        echo "           faster-whisper on the CPU, which is several times slower."
    fi
fi

# macOS: install portaudio if sounddevice fails
if [[ "$OSTYPE" == "darwin"* ]]; then
    if ! python -c "import sounddevice" 2>/dev/null; then
        echo "Installing portaudio via Homebrew..."
        if command -v brew &>/dev/null; then
            brew install portaudio
            pip install sounddevice
        else
            echo "WARNING: Homebrew not found. Install portaudio manually or install Homebrew."
        fi
    fi
fi

# Linux: install portaudio if needed
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if ! python -c "import sounddevice" 2>/dev/null; then
        echo "Installing portaudio (requires sudo)..."
        sudo apt-get install -y portaudio19-dev python3-dev
        pip install sounddevice
    fi
fi

echo
echo "=== Setup complete! ==="
echo
echo "Next steps:"
echo "  1. Open OBS Studio"
echo "  2. Add a Browser Source in your scene"
echo "  3. Set URL to: file://$(realpath overlay/index.html)"
echo "  4. Set Width: 1920, Height: 1080 (or match your canvas)"
echo "  5. Enable 'Shutdown source when not visible' = OFF"
echo "  6. Run: ./run.sh"
echo "  7. Start OBS Virtual Camera"
echo "  8. Select 'OBS Virtual Camera' in your video call app"
echo
echo "Configuration: edit config.json to adjust model, language, etc."
echo "List microphones: ./run.sh --list-devices"
echo
