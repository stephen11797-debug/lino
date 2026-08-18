# Lino - Offline Voice Assistant

A floating, always-on-top voice assistant container that orchestrates
open-source AI tools to run fully offline on Linux.

## What is Lino?

Lino is a **container/wrapper** that integrates several open-source components
into a unified voice assistant experience. Lino itself is just the glue code
(GTK UI, voice command routing, configuration). The actual AI and voice
technology comes from the excellent open-source projects listed below.

## Key Features

- **Voice control** — mouse, keyboard, app launching, volume, screenshots
- **Offline AI chat** via Ollama + Qwen models
- **Screen vision** — ask "what's on my screen" and Lino describes it
- **Neural TTS** with multiple voice options (via Piper)
- **GTK floating UI** — stays on top like an Android assistant bubble

## Quick Install

```bash
# 1. System dependencies (one-time)
sudo apt install python3-gi python3-cairo gir1.2-gtk-3.0 \
                 gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 \
                 libcairo2 libxtst6 wmctrl x11-utils \
                 pulseaudio alsa-utils ffmpeg xclip xdotool

# 2. Python packages
pip3 install -r requirements.txt

# 3. Install Ollama for AI chat (separate project)
curl -fsSL https://ollama.com/install | sh
ollama pull qwen2.5:7b      # conversation
ollama pull qwen2.5vl:7b    # vision (screen description)

# 4. Download Vosk speech model
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model

# 5. Download Piper voices (neural TTS)
# See: https://github.com/rhasspy/piper/releases
# Or use the install.sh helper script for automated setup

# 6. Run!
python3 voice.py
```

## Usage

| Command | Action |
|---------|--------|
| Tap mic button | Start/stop listening |
| "Click" | Left mouse click |
| "Right click" / "Double click" | Mouse control |
| "Move mouse up/down/left/right" | Pointer movement |
| "Volume up/down" / "Mute" | Audio control |
| "Open browser/terminal/files" | App launching |
| "What's on my screen?" | Screenshot + AI description |
| "Start conversation" | Continuous listening mode |
| "Open chat" | Open text chat window |

## Configuration

Edit constants at the top of `voice.py`:

```python
USER_NAME = "Studio"          # Name used in greetings
ASSISTANT_NAME = "Lino"      # Lino's name
BUBBLE_PHOTO = "..."             # Avatar image path
AI_MODEL = "qwen2.5:7b"         # Ollama model name
```

## What is Included in this Repository

### This Project (Lino wrapper) — MIT License
- `voice.py` — Main application (GTK UI + voice command routing + AI chat integration)
- `server.py` — Simple HTTP relay server for web access
- `requirements.txt` — Python package dependencies
- `install.sh` — Automated dependency installer
- `voices/*.json` — Voice metadata files (configuration info only)

### Third-Party Components (separately licensed)

This project **integrates** (but does not redistribute) the following:

1. **Ollama** — Local AI inference engine
   - License: MIT
   - Website: https://ollama.com
   - Models must be downloaded separately via `ollama pull`

2. **Qwen/Qwen2.5/Qwen2.5-VL** — AI language/vision models
   - License: Apache 2.0 (model weights)
   - Created by: Alibaba Cloud
   - Repository: https://github.com/QwenLM/Qwen
   - Pulled via Ollama (not included here)

3. **Vosk** — Speech recognition toolkit
   - License: Apache 2.0
   - Repository: https://github.com/alphacephei/vosk-api
   - Speech model downloaded separately (~40MB)

4. **Piper** — Neural text-to-speech engine
   - License: MIT
   - Repository: https://github.com/rhasspy/piper
   - Voice models are NOT included in this repository
   - Download from: https://github.com/rhasspy/piper/releases

5. **GTK/PyGObject/Cairo** — GUI toolkit
   - License: LGPL
   - Used for the floating UI window

## License

### Lino (this project)
MIT License — see [LICENSE](LICENSE) file.

**Note:** Lino integrates open-source components that have their own
licenses. When you run Lino, you are responsible for complying with those
licenses:

- Download your own copies of all models and voices
- Follow the terms of Ollama, Vosk, Piper, and Qwen licenses
- Voice model files (`.onnx`) are NOT included here and must be downloaded
  separately from the Piper or Coqui projects

## Credits

Built by Stephen Campbell. Special thanks to:
- The Ollama team for making local AI easy
- The Vosk team for offline speech recognition
- The Piper/Namex team for neural TTS voices
- The Qwen team (Alibaba) for the open language models
