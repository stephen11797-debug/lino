# Lino - Offline Voice Assistant

A floating, always-on-top voice assistant that runs **fully offline** on Linux. Controls your PC by voice, chats with local AI, and speaks with neural voices.

## Features

- **Voice control** — mouse, keyboard, app launching, volume, screenshots, window management
- **Offline AI chat** via [Ollama](https://ollama.com) (qwen2.5 models)
- **Screen vision** — ask "what's on my screen" and Lino will describe it
- **8 neural TTS voices** (Piper) — male, female, British, neutral accents
- **GTK floating UI** — stays on top like an Android assistant bubble
- **Tray icon** + chat window
- **Conversation mode** — keeps listening after replies

## Quick Install

```bash
# 1. System dependencies (one-time)
sudo apt install python3-gi python3-cairo gir1.2-gtk-3.0 \
                 gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 \
                 libcairo2 libxtst6 wmctrl x11-utils \
                 pulseaudio alsa-utils ffmpeg xclip

# 2. Python packages (optional: use a venv for isolation)
pip3 install -r requirements.txt

# 3. (Optional) Install Ollama for AI chat
curl -fsSL https://ollama.com/install | sh
ollama pull qwen2.5:7b          # fast conversation
ollama pull qwen2.5vl:7b        # vision (screen description)

# 4. Download Vosk speech model (first run auto-downloads it)
#    Or manually:
#    wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
#    unzip vosk-model-small-en-us-0.15.zip && mv vosk-model-small-en-us-0.15 model

# 5. Run!
python3 voice.py
```

## Quick Install (Debian/Ubuntu .deb)

A pre-built `.deb` package is available:

```bash
sudo dpkg -i lino_1.0.0_all.deb
sudo apt-get install -f  # fix any missing deps
lino                        # launch from terminal or app menu
```

## Usage

| Command | Action |
|---------|--------|
| Tap mic button or click bubble | Start/stop listening |
| "Click" / "Double click" / "Right click" | Mouse control |
| "Move mouse up/down/left/right" | Pointer movement |
| "Volume up/down" / "Mute" / "Unmute" | Audio control |
| "Open browser/terminal/files/code" | App launching |
| "Scroll up/down" | Scroll wheel |
| "Type hello world" | Type text |
| "Press enter/tab/escape" | Key presses |
| "What's on my screen?" | Screenshot + AI description |
| "Start conversation" | Continuous listening mode |
| "Switch to female voice" | Change TTS voice |
| "Open chat" | Open text chat window |

### Voice Commands
All standard computer control commands work via voice, including: click, right-click, double-click, scroll, mouse movement, volume, copy/paste, keyboard keys, app launching, and more.

## Configuration

Edit these constants at the top of `voice.py`:

```python
USER_NAME = "Stephen Campbell"    # Who Lino talks to
ASSISTANT_NAME = "Lino"          # Lino's name
BUBBLE_PHOTO = "..."             # Avatar image (defaults to ~/Pictures/48257.jpg)

AI_MODEL = "qwen2.5:7b"         # Primary AI model
AI_FALLBACK = "qwen2.5:1.5b"    # Fallback if primary unavailable
VISION_MODEL = "qwen2.5vl:7b"   # For screen description
```

## Project Structure

```
.
├── voice.py              # Main application (GTK UI + voice control + AI chat)
├── server.py             # Web relay server (for phone-to-PC voice)
├── voices/               # Piper neural TTS voice models (.onnx files)
├── model/                # Vosk speech-to-text model (auto-downloaded)
├── lino_autostart.sh     # Autostart wrapper
├── requirements.txt      # Python dependencies
└── .venv/                # Virtual environment (not in repo)
```

## Dependencies

| Component | Purpose |
|-----------|---------|
| Python 3.12 | Runtime |
| PyGObject + GTK 3 | UI |
| Cairo | Drawing |
| Vosk | Speech-to-text |
| Piper TTS | Text-to-speech |
| Ollama | AI chat (optional but recommended) |
| wmctrl, xdotool | Window/mouse control |
| arecord/pulseaudio | Audio capture |

## License

MIT — see [LICENSE](LICENSE) file.

## Credits

Built by Stephen Campbell for Linux Mint. Uses:
- [Vosk](https://github.com/alphacephei/vosk-api) for offline speech recognition
- [Piper](https://github.com/rhasspy/piper) for neural text-to-speech
- [Ollama](https://ollama.com) for local AI inference
