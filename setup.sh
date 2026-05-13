#!/usr/bin/env bash
# Laolao setup script (Linux / macOS)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Laolao Setup ==="
echo

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3.9+ is required. Install from https://python.org"
    exit 1
fi

PYTHON=$(command -v python3)
PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
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

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

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
