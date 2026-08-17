#!/bin/bash
# Lino Easy Install Script
# Installs all dependencies needed to run the Lino voice assistant
set -e

echo "=== Lino Voice Assistant - Easy Install ==="
echo ""

# System dependencies
echo "[1/4] Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    python3-gi python3-cairo \
    gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 \
    libcairo2 libxtst6 wmctrl x11-utils \
    pulseaudio alsa-utils ffmpeg xclip \
    xdotool 2>/dev/null || true

# Python dependencies from requirements.txt
echo ""
echo "[2/4] Installing Python packages..."
pip3 install --user -r requirements.txt 2>/dev/null || pip3 install -r requirements.txt

echo ""
echo "[3/4] Downloading Vosk speech model (~40MB)..."
if [ ! -d "model" ]; then
    wget -q "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -O /tmp/vosk-model.zip
    unzip -q /tmp/vosk-model.zip -d /tmp/
    mv /tmp/vosk-model-small-en-us-0.15 model
    rm /tmp/vosk-model.zip
    echo "Vosk model downloaded to model/"
else
    echo "Vosk model already exists, skipping."
fi

echo ""
echo "[4/4] Checking optional components..."

# Check if Ollama is installed
if command -v ollama &> /dev/null; then
    echo "Ollama found. Pulling AI models (this may take a while)..."
    ollama pull qwen2.5:7b
    ollama pull qwen2.5vl:7b
else
    echo "Ollama not found. Install from https://ollama.com for AI chat."
    echo "Skipping AI model downloads."
fi

echo ""
echo "=== Installation Complete ==="
echo ""
echo "To start Lino, run:"
echo "  python3 voice.py"
echo ""
echo "Or copy your photo to the project as 'bubble.jpg' for the avatar."
