#!/usr/bin/env python3
"""
Lino — floating offline voice assistant.
Looks like an Android assistant, always on top, fully offline.

Uses:  GTK (window) + vosk (speech-to-text) + Piper/TTS (speech).
If vosk isn't installed yet, the app still runs and greets you,
and voice recognition turns on automatically once vosk is ready.
"""
import math
import os
import queue
import re
import subprocess
import sys
import threading
import urllib.parse
import wave

import cairo

# Make vosk (installed in the local venv) importable from system python.
_VENV_SP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".venv", "lib", "python3.12", "site-packages")
if os.path.isdir(_VENV_SP) and _VENV_SP not in sys.path:
    sys.path.insert(0, _VENV_SP)

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, Pango, GdkPixbuf

BASE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
USER_NAME = "Studio"
ASSISTANT_NAME = "Lino"
GREETING = "Hi " + USER_NAME + ", I am " + ASSISTANT_NAME

COLOR_BG = "#101020"
COLOR_ACCENT = "#00e08a"
COLOR_TEXT = "#ffffff"
COLOR_MUTED = "#8a8a9a"

BUBBLE_SIZE = 160
# Photo shown in the bubble. Looks in the project dir first, then ~/Pictures.
BUBBLE_PHOTO = os.path.join(BASE, "lino-icon.png")
if not os.path.exists(BUBBLE_PHOTO):
    BUBBLE_PHOTO = os.path.expanduser("~/Pictures/48257.jpg")


TTS_VOICE_TYPE = "male1"   # fallback voice (only used if Piper isn't installed)
TTS_LANG = "en-US"
TTS_PITCH = 0
TTS_RATE = 0

# Neural Piper voices (offline). Fall back to spd-say if missing.
PIPER_VOICES = {
    "Female (Amy)": "en_US-amy-medium",
    "Female (Kristin)": "en_US-kristin-medium",
    "Female (Cori, British)": "en_GB-cori-high",
    "Male (Joe)": "en_US-joe-medium",
    "Male (Ryan)": "en_US-ryan-high",
    "Male (Alan, British)": "en_GB-alan-medium",
    "Neutral (Lessac)": "en_US-lessac-medium",
}
CURRENT_VOICE = "en_US-lessac-medium"


def _set_voice(name):
    """Switch the Piper voice used for speech (persists for this session)."""
    global CURRENT_VOICE
    if name in PIPER_VOICES.values():
        CURRENT_VOICE = name


# --- AI chat plugin (local ollama, fully offline) ---
AI_URL = "http://127.0.0.1:11434/api/chat"
AI_MODEL = "qwen2.5:7b"
VISION_MODEL = "qwen2.5vl:7b"
AI_FALLBACK = "qwen2.5:1.5b"
AI_MODELS = {
    "qwen2.5:1.5b": "fast, runs great on this PC",
    "llama3.2:1b": "tiny and quick",
    "llama3.2:3b": "smarter, still light",
    "phi3:mini": "compact, solid answers",
    "gemma2:2b": "Google small model",
    "mistral:7b": "strong, needs more RAM",
}
AI_SYSTEM = ("You are Lino, a friendly, helpful AI assistant running "
             "offline on the user's PC. Talk like a natural, warm human friend — the "
             "way Google Gemini or a great assistant would: casual, curious, "
             "never robotic or lecture-y. Be concise (1-3 short sentences) "
             "unless he wants depth. Offer advice, practical suggestions, and "
             "ask a light follow-up now and then. You can control the computer using "
             "the provided tools (open_app, click, type_text, press_key, scroll, "
             "volume, open_chat, look_at_screen). Use a tool whenever the user asks "
             "for an action; then say in one short sentence what you did. If it is a "
             "normal question or they just want to chat, answer plainly with no "
             "tool call.")

# Native Ollama tools (much more reliable than text action tags).
TOOLS = [
    {"type": "function", "function": {"name": "open_app",
        "description": "Open or switch to an application by name (firefox, terminal, calculator, files, code editor, browser).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "App name, e.g. firefox, terminal, calculator, files, code"}},
            "required": ["name"]}}},
    {"type": "function", "function": {"name": "click",
        "description": "Perform a mouse click at the current pointer position.",
        "parameters": {"type": "object", "properties": {
            "button": {"type": "string", "enum": ["left", "right", "double"]}},
            "required": ["button"]}}},
    {"type": "function", "function": {"name": "type_text",
        "description": "Type a string of text into the focused field or window.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "press_key",
        "description": "Press a single key or shortcut: enter, space, tab, escape, backspace, delete, up, down, left, right, home, end, page_up, page_down, ctrl_s, alt_tab.",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "scroll",
        "description": "Scroll the mouse wheel.",
        "parameters": {"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]}},
            "required": ["direction"]}}},
    {"type": "function", "function": {"name": "volume",
        "description": "Raise, lower, or mute the system volume.",
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["up", "down", "mute"]}},
            "required": ["action"]}}},
    {"type": "function", "function": {"name": "look_at_screen",
        "description": "Take a screenshot and describe what is currently on the screen. Use when the user asks what you see, what's on the screen, or needs help finding something visible.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string", "description": "Optional question about the screen contents"}}}}},
    {"type": "function", "function": {"name": "open_chat",
        "description": "Open the Lino chat window.",
        "parameters": {"type": "object", "properties": {}}}},
]

_PRESS_KEYS = {
    "enter": 36, "return": 36, "space": 65, "tab": 23, "escape": 9, "esc": 9,
    "backspace": 22, "delete": 119, "del": 119, "up": 111, "arrow up": 111,
    "down": 116, "arrow down": 116, "left": 113, "arrow left": 113,
    "right": 114, "arrow right": 114, "home": 110, "end": 115,
    "page up": 112, "page_down": 117, "page down": 117,
    "ctrl_s": (39, ("ctrl",)), "ctrl c": (54, ("ctrl",)),
    "ctrl v": (55, ("ctrl",)), "ctrl x": (53, ("ctrl",)),
    "ctrl z": (52, ("ctrl",)), "alt_tab": (23, ("alt",)),
    "alt f4": (71, ("alt",)), "ctrl a": (38, ("ctrl",)),
}


def say(text, async_ok=True):
    """Speak with the neural Piper voice (natural, pro); falls back to
    spd-say/espeak if Piper isn't installed yet."""
    piper = os.path.join(BASE, ".venv", "bin", "piper")
    model = os.path.join(BASE, "voices", CURRENT_VOICE + ".onnx")
    if not os.path.exists(model):
        model = os.path.join(BASE, "voices", "en_US-lessac-medium.onnx")
    if os.path.exists(piper) and os.path.exists(model):
        try:
            wav = os.path.join(BASE, "speak.wav")
            p = subprocess.Popen([piper, "--model", model, "--output_file", wav],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            p.communicate(text.encode("utf-8"))
            if p.returncode == 0 and os.path.exists(wav):
                subprocess.run(["paplay", wav], capture_output=True)
                return
        except Exception:
            pass
    cmd = ["spd-say", "-w", "-l", TTS_LANG, "-t", TTS_VOICE_TYPE,
           "-p", str(TTS_PITCH), "-r", str(TTS_RATE), text]
    try:
        subprocess.run(cmd, check=False, capture_output=True)
    except FileNotFoundError:
        pass


def run_tts_async(text):
    global _last_tts
    if not text:
        return
    import time as _t
    with _tts_lock:
        if text == _last_tts[0] and _t.time() - _last_tts[1] < 2.0:
            return
        _last_tts = (text, _t.time())
    threading.Thread(target=say, args=(text,), daemon=True).start()


_tts_lock = threading.Lock()
_last_tts = ("", 0.0)


def ai_chat(messages, model=AI_MODEL, timeout=90, tools=None, tool_handler=None,
            max_tools=6, images=None):
    """Ask the local AI (ollama) for a reply. Returns None if unavailable.
    If `tools` and `tool_handler` are given, executes native tool_calls in a
    loop and feeds each result back to the model. `images` (list of base64
    PNGs) is attached to the last user message for vision models."""
    import json as _json
    import urllib.request as _url
    if images:
        messages = list(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                messages[i] = dict(messages[i])
                messages[i]["images"] = list(images)
                break
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.7},
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
    req = _url.Request(AI_URL, data=_json.dumps(body).encode("utf-8"),
                       headers={"Content-Type": "application/json"})
    for _ in range(max_tools + 1):
        try:
            with _url.urlopen(req, timeout=timeout) as r:
                data = _json.loads(r.read().decode("utf-8"))
        except Exception:
            return None
        msg = data.get("message", {})
        calls = msg.get("tool_calls")
        if calls and tool_handler:
            results = []
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {}) or {}
                if isinstance(args, str):
                    try:
                        args = _json.loads(args)
                    except Exception:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                results.append((call, tool_handler(name, args)))
            messages.append({"role": "assistant", "content": "", "tool_calls": calls})
            for call, result in results:
                messages.append({"role": "tool", "content": str(result),
                                 "tool_name": call.get("function", {}).get("name", "")})
            req = _url.Request(AI_URL, data=_json.dumps(body).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
            continue
        reply = msg.get("content", "").strip()
        return reply or None
    return None


# ============================================================
# ECHO BRAIN — offline conversational AI
# ============================================================
import datetime as _dt
import random as _rnd


class EchoBrain:
    """Rule-based conversation engine with memory (fully offline)."""

    def __init__(self, user_name="Studio"):
        self.user_name = user_name
        self.last_topic = None
        self.heard_count = 0
        self.mood = "good"
        self.known_name = False

    def reply(self, text):
        """Return a reply string, or None if this wasn't conversation."""
        t = text.lower().strip()
        self.heard_count += 1

        # --- greetings ---
        if _has(t, "hi", "hello", "hey", "good morning", "good afternoon",
                "good evening", "howdy"):
            return _pick("Hello! How are you?", "Hey there! How's it going?",
                         "Hi! Good to see you.")
        if _has(t, "good morning"):
            return "Good morning " + self.user_name + "! Ready for the day?"
        if _has(t, "good night", "goodnight"):
            return "Good night " + self.user_name + ". Sleep well!"
        if _has(t, "goodbye", "bye", "see you", "good bye"):
            return "Goodbye " + self.user_name + "! Talk to you soon."

        # --- name ---
        if _has(t, "your name", "who are you", "what are you called"):
            return "I'm " + ASSISTANT_NAME + ", your offline voice assistant."
        if _has(t, "my name is", "i am", "i'm"):
            name = _grab_after(t, "my name is", "i am", "i'm")
            if name:
                self.user_name = name.title()
                self.known_name = True
                return "Nice to meet you, " + self.user_name + "!"
        if _has(t, "do you know my name", "what is my name"):
            if self.known_name:
                return "Your name is " + self.user_name + "."
            return "You haven't told me your name yet. You can say, my name is Studio."

        # --- how are you ---
        if _has(t, "how are you", "how do you feel", "what's up", "whats up",
                "how is it going"):
            self.mood = _pick("good", "great", "fantastic")
            return "I'm " + self.mood + ", thanks for asking! How are you?"
        if _has(t, "i am good", "i'm good", "im good", "doing well",
                "i am fine", "feeling good"):
            return _pick("That's great to hear!",
                         "Glad you're doing well.",
                         "Awesome! What can I help you with?")
        if _has(t, "i am bad", "not good", "i'm sad", "not great", "feeling down"):
            return "Sorry to hear that. Want to talk about it, or should I cheer you up?"

        # --- time & date ---
        if _has(t, "what time", "the time", "tell me time"):
            return "It is " + _dt.datetime.now().strftime("%I:%M %p") + "."
        if _has(t, "what day", "today's date", "what date", "the date"):
            return "Today is " + _dt.datetime.now().strftime("%A, %B %d") + "."

        # --- thanks / please ---
        if _has(t, "thank", "thanks", "thank you"):
            return _pick("You're welcome!", "Anytime!", "No problem at all.")
        if _has(t, "i love you", "love you"):
            return "Aw, thank you " + self.user_name + "! I like you too."
        if _has(t, "are you real", "are you alive", "are you a robot",
                "are you an ai"):
            return "I'm a voice assistant running fully on your computer, offline. No internet needed!"

        # --- jokes ---
        if _has(t, "joke", "funny", "make me laugh"):
            return _pick(
                "Why do programmers prefer dark mode? Because light attracts bugs.",
                "I told my computer I needed a break. Now it won't stop sending me vacation ads.",
                "Why did the microphone get cold? It was left in the corner. Get it? Corner!",
                "Why do bees hum? Because they don't know the words!")

        # --- facts ---
        if _has(t, "tell me something", "fact", "did you know"):
            return _pick(
                "Did you know? Octopuses have three hearts and blue blood.",
                "Fun fact: Honey never spoils. Archaeologists found 3,000 year old honey that was still edible.",
                "Did you know? The human brain uses about twenty watts of power — enough to run a dim lightbulb.")

        # --- who / what ---
        if _has(t, "what can you do", "help", "commands", "options"):
            return ("I can control your computer by voice! Try: "
                    "click, double click, right click, move mouse up, down, "
                    "left or right, scroll up or down, arrow keys, backspace, "
                    "copy, paste, undo, type, press enter, open terminal, "
                    "open code chat, volume up or down, mute, open the browser, or just chat with me.")

        # --- pc control feedback caught by handle_command, but confirm here ---
        return None

    def follow_up(self):
        """Ask a natural follow-up to keep the conversation going."""
        return _pick("What else can I do for you?",
                     "Anything else?",
                     "Is there something you'd like me to do?",
                     "How else can I help, " + self.user_name + "?")


def _has(t, *words):
    return any(w in t for w in words)


def _intent(t, required=(), any_of=()):
    """Word-based match: all 'required' words + one of 'any_of' must appear,
    in any order, with any extra words — so natural phrasing works."""
    words = set(re.findall(r"[a-z']+", t))
    if any(w not in words for w in required):
        return False
    if any_of and not any(w in words for w in any_of):
        return False
    return True


def _pick(*choices):
    return _rnd.choice(choices)


def _grab_after(t, *prefixes):
    for p in prefixes:
        if t.startswith(p):
            return t[len(p):].strip()
    # also find mid-sentence
    for p in prefixes:
        i = t.find(p)
        if i != -1:
            return t[i + len(p):].strip()
    return ""


# ============================================================
# SYSTEM HELPERS (volume, app launching)
# ============================================================
def home_dir():
    return os.path.expanduser("~")


def _window_list():
    """List real open window titles (skips panels/desktops/Lino itself)."""
    try:
        out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return []
    skip = {"bottom panel", "desktop", "n/a"}
    wins = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 4:
            title = parts[3].strip()
            low = title.lower()
            if not title or low in skip or "lino" in low:
                continue
            wins.append(title)
    return wins


def _screenshot_b64():
    """Capture the whole screen and return a base64 PNG (or None on failure)."""
    import base64 as _b64
    try:
        p = subprocess.run(["import", "-window", "root", "-silent", "png:-"],
                           capture_output=True, timeout=20)
        if p.returncode != 0 or not p.stdout:
            p = subprocess.run(["sh", "-c", "xwd -root -silent | convert xwd:- png:-"],
                               capture_output=True, timeout=20)
        if p.returncode != 0 or not p.stdout:
            return None
        return _b64.b64encode(p.stdout).decode("ascii")
    except Exception:
        return None


def _panel_geometry():
    """Find the start bar (taskbar) geometry: (x, y, width, height) or None."""
    import re as _re
    now = _time.time()
    if now - _panel_geometry.last < 5 and _panel_geometry.cache:
        return _panel_geometry.cache
    best = None
    try:
        out = subprocess.run(["xwininfo", "-root", "-tree"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        out = ""
    for line in out.splitlines():
        if "Panel" not in line:
            continue
        m = _re.search(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', line)
        if not m:
            continue
        w, h, x, y = (int(v) for v in m.groups())
        if w > 100 and h < 200:      # a real taskbar, not the 10x10 helper
            area = w * h
            if best is None or area > best[0]:
                best = (area, (x, y, w, h))
    result = best[1] if best else None
    _panel_geometry.cache = result
    _panel_geometry.last = now
    return result

_panel_geometry.cache = None
_panel_geometry.last = 0.0


def _input_source():
    """Best capture source for arecord -D pulse, or None for default.

    Prefers real microphones (never the silent loopback monitors) and ranks
    them by quality: Bluetooth headset first (that's where you talk), then
    USB/quality mics, highest sample rate wins within each tier.
    """
    import re as _re
    try:
        out = subprocess.run(["pactl", "list", "sources", "short"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    entries = []
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        name, spec = cols[1], cols[3]
        if "monitor" in name:
            continue
        rate = 0
        m = _re.search(r"(\d+)\s*Hz", spec)
        if m:
            rate = int(m.group(1))
        entries.append((name, rate))
    if not entries:
        return None
    usb = sorted([e for e in entries if "usb" in e[0]], key=lambda e: e[1], reverse=True)
    bluez = sorted([e for e in entries if "bluez" in e[0]], key=lambda e: e[1], reverse=True)
    pool = bluez or usb or entries
    pool.sort(key=lambda e: e[1], reverse=True)
    return pool[0][0]


def _volume(plus=0, mute=None):
    """Raise/lower or mute the default sink with pactl."""
    try:
        if mute is not None:
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@",
                            "1" if mute else "0"],
                           capture_output=True, check=False)
        if plus:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@",
                            ("+%d%%" if plus > 0 else "%d%%") % plus],
                           capture_output=True, check=False)
        return True
    except FileNotFoundError:
        return False


def _launch(*cmd):
    try:
        subprocess.Popen(list(cmd), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False


_APP_LAUNCH = {
    "firefox": ("firefox",),
    "browser": ("firefox",),
    "web": ("firefox",),
    "calculator": ("gnome-calculator",),
    "calc": ("gnome-calculator",),
    "files": ("xdg-open", home_dir()),
    "folder": ("xdg-open", home_dir()),
    "terminal": ("x-terminal-emulator",),
    "console": ("x-terminal-emulator",),
    "code": ("x-terminal-emulator",),
}


def _activate_app(title):
    """Raise + focus the first open window whose title OR class contains 'title'."""
    try:
        out = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return False
    t = title.lower()
    target = None
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        wid, desktop, cls, wtitle = parts
        if t in cls.lower() or t in wtitle.lower():
            target = wid
            break
    if target:
        subprocess.run(["wmctrl", "-ia", target], capture_output=True)
        return True
    return False


# ============================================================
# MOUSE & KEYBOARD CONTROL (offline, via libX11/libXtst)
# ============================================================
import ctypes
from ctypes import c_int, c_uint, c_ulong, POINTER, byref

_libX11 = None
_libXtst = None
_display = None

def _load_x():
    global _libX11, _libXtst, _display
    if _libX11 is not None:
        return True
    try:
        _libX11 = ctypes.CDLL("libX11.so.6")
        _libXtst = ctypes.CDLL("libXtst.so.6")
        _libX11.XOpenDisplay.restype = ctypes.c_void_p
        _libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        _display = _libX11.XOpenDisplay(None)
        return bool(_display)
    except OSError:
        _display = None
        return False

def _x_fixups():
    """Set ctypes signatures once so pointer calls are reliable."""
    if getattr(_libX11, "_fixed", None):
        return
    _libX11.XDefaultRootWindow.restype = c_ulong
    _libX11.XWarpPointer.argtypes = [ctypes.c_void_p, c_ulong, c_ulong,
                                     c_int, c_int, c_int, c_int, c_int, c_int]
    _libX11.XQueryPointer.argtypes = [ctypes.c_void_p, c_ulong, POINTER(c_ulong),
                                      POINTER(c_ulong), POINTER(c_int), POINTER(c_int),
                                      POINTER(c_int), POINTER(c_int), POINTER(c_uint)]
    _libX11.XQueryPointer.restype = c_int
    _libX11._fixed = True


def mouse_pos():
    """Return the pointer's absolute screen position (x, y)."""
    if not _load_x():
        return (0, 0)
    _x_fixups()
    root = _libX11.XDefaultRootWindow(_display)
    rr = c_ulong()
    cr = c_ulong()
    rx = c_int(); ry = c_int(); wx = c_int(); wy = c_int(); m = c_uint()
    _libX11.XQueryPointer(_display, root, byref(rr), byref(cr),
                          byref(rx), byref(ry), byref(wx), byref(wy), byref(m))
    return (rx.value, ry.value)


def mouse_move(x, y):
    """Move the pointer to absolute screen coordinates."""
    if not _load_x():
        return False
    _x_fixups()
    root = _libX11.XDefaultRootWindow(_display)
    _libX11.XWarpPointer(_display, 0, root, 0, 0, 0, 0, c_int(int(x)), c_int(int(y)))
    _libX11.XFlush(_display)
    return True


def mouse_click(button=1):
    """Click mouse button (1=left, 2=middle, 3=right)."""
    if not _load_x():
        return False
    _libXtst.XTestFakeButtonEvent(_display, c_uint(button), True, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.03)
    _libXtst.XTestFakeButtonEvent(_display, c_uint(button), False, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.02)
    return True


def mouse_scroll(clicks=1, up=True):
    """Scroll the mouse wheel. up=True scrolls up."""
    if not _load_x():
        return False
    btn = 4 if up else 5
    for _ in range(abs(int(clicks))):
        _libXtst.XTestFakeButtonEvent(_display, c_uint(btn), True, 0)
        _libX11.XFlush(_display)
        _time.sleep(0.02)
        _libXtst.XTestFakeButtonEvent(_display, c_uint(btn), False, 0)
        _libX11.XFlush(_display)
        _time.sleep(0.02)
    return True


def _move_px(t, default=150):
    """Distance to move the mouse from the spoken phrase."""
    import re
    m = re.search(r"\b(\d+)\b", t)
    if m:
        return min(int(m.group(1)), 2000)
    if "a lot" in t or "lots" in t:
        return 400
    if "a little bit" in t or "a bit" in t or "a little" in t or "slightly" in t:
        return 60
    return default

def key_press(keycode, ctrl=False, alt=False, shift=False, super_=False):
    """Press a key (X11 keycode number), optionally with modifiers."""
    if not _load_x():
        return False
    # modifier keycodes: 37=Ctrl_L 64=Alt_L 50=Shift_L 133=Super_L
    mods = [(37, ctrl), (64, alt), (50, shift), (133, super_)]
    for code, on in mods:
        if on:
            _libXtst.XTestFakeKeyEvent(_display, c_uint(code), True, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.03)
    _libXtst.XTestFakeKeyEvent(_display, c_uint(int(keycode)), True, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.03)
    _libXtst.XTestFakeKeyEvent(_display, c_uint(int(keycode)), False, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.03)
    for code, on in reversed(mods):
        if on:
            _libXtst.XTestFakeKeyEvent(_display, c_uint(code), False, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.02)
    return True

def type_text(text):
    """Type a string. Uses clipboard if xclip is present, else keysyms."""
    if not _load_x():
        return False
    import subprocess as sp
    try:
        p = sp.run(["xclip", "-selection", "clipboard"], input=text.encode(),
                   capture_output=True)
        if p.returncode == 0:
            key_press(55, ctrl=True)   # Ctrl+V
            return True
    except FileNotFoundError:
        pass
    return type_text_keysym(text)

def _keysym_for_char(ch):
    """Return the X11 keysym for a single ASCII character (or 0)."""
    o = ord(ch)
    if o == 32: return 0x20          # space
    if 0x21 <= o <= 0x7e: return o   # printable ASCII map to keysym directly
    return 0

import time as _time

def _tap(keycode):
    """Press and release a key with real timing so apps register it."""
    _libXtst.XTestFakeKeyEvent(_display, c_uint(int(keycode)), True, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.03)
    _libXtst.XTestFakeKeyEvent(_display, c_uint(int(keycode)), False, 0)
    _libX11.XFlush(_display)
    _time.sleep(0.02)

def type_text_keysym(text):
    """Type ASCII text by mapping each char to a keysym + keycode."""
    if not _load_x():
        return False
    _libX11.XStringToKeysym.restype = c_ulong
    _libX11.XStringToKeysym.argtypes = [ctypes.c_char_p]
    _libX11.XKeysymToKeycode.restype = c_uint
    _libX11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, c_ulong]
    _libX11.XFlush.restype = c_int
    _libX11.XFlush.argtypes = [ctypes.c_void_p]

    for ch in text:
        if ch == '\n':
            _tap(36)            # Return
            continue
        if ch == ' ':
            _tap(65)            # space
            continue
        if ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?':
            code = _char_keycode(_libX11, ch, shifted=True)
            if not code:
                continue
            _libXtst.XTestFakeKeyEvent(_display, 50, True, 0)   # Shift down
            _libX11.XFlush(_display)
            _time.sleep(0.02)
            _tap(code)
            _libXtst.XTestFakeKeyEvent(_display, 50, False, 0)  # Shift up
            _libX11.XFlush(_display)
        else:
            code = _char_keycode(_libX11, ch, shifted=False)
            if not code:
                continue
            _tap(code)
        _time.sleep(0.01)
    return True

def _char_keycode(libX11, ch, shifted):
    """Get keycode for a char. Try upper/lower keysym depending on shift."""
    if shifted:
        s = libX11.XStringToKeysym(ch.encode())
        if s == 0:
            return 0
        return libX11.XKeysymToKeycode(_display, s)
    s = libX11.XStringToKeysym(ch.lower().encode())
    if s == 0:
        return 0
    return libX11.XKeysymToKeycode(_display, s)


class BubbleFace(Gtk.DrawingArea):
    """A circular photo bubble that doubles as Lino's face."""

    def __init__(self, path, size):
        super().__init__()
        self.size = size
        self.set_size_request(size, size)
        self.pb = None
        if os.path.exists(path):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(path)
                w, h = pb.get_width(), pb.get_height()
                s = min(w, h)
                pb = pb.new_subpixbuf((w - s) // 2, (h - s) // 2, s, s)
                self.pb = pb.scale_simple(size - 8, size - 8,
                                          GdkPixbuf.InterpType.BILINEAR)
            except Exception:
                self.pb = None
        self.connect("draw", self.on_draw)

    def on_draw(self, widget, cr):
        r = self.size / 2.0
        cr.save()
        cr.arc(r, r, r - 2, 0, 2 * math.pi)
        cr.clip()
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        grad = cairo.RadialGradient(r, r, 6, r, r, r)
        grad.add_color_stop_rgb(0, 0.10, 0.10, 0.22)
        grad.add_color_stop_rgb(1, 0.02, 0.02, 0.05)
        cr.set_source(grad)
        cr.paint()
        if self.pb is not None:
            Gdk.cairo_set_source_pixbuf(cr, self.pb, 4, 4)
            cr.paint()
        cr.restore()
        cr.save()
        cr.arc(r, r, r - 3, 0, 2 * math.pi)
        cr.set_line_width(3)
        cr.set_source_rgb(0.0, 0.88, 0.54)
        cr.stroke()
        cr.restore()
        return False


class LinoVoice(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title("Lino Assistant")
        self.set_default_size(BUBBLE_SIZE, BUBBLE_SIZE + 110)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_position(Gtk.WindowPosition.NONE)
        try:
            self.set_icon_from_file(BUBBLE_PHOTO)
        except Exception:
            pass

        # rgba (transparent) window if the compositor allows it
        scr = self.get_screen()
        if scr.is_composited():
            visual = scr.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        # --- styling ---
        css = b"""
        * { font-family: 'DejaVu Sans', system-ui, sans-serif; }
        #bg { background: transparent; }
        #status {
            color: #e6e6ff; font-size: 11px;
            background: rgba(16, 16, 32, 0.85);
            border-radius: 10px;
            padding: 2px 8px;
        }
        #name {
            color: #00e08a; font-size: 15px; font-weight: bold;
        }
        #heard {
            color: #ffffff; font-size: 11px;
            background: rgba(16, 16, 32, 0.85);
            border-radius: 10px;
            padding: 2px 8px;
        }
        #mic {
            background: #00e08a; color: #0a0a1a;
            font-size: 12px; font-weight: bold;
            border-radius: 16px;
            padding: 4px 14px;
            border: none;
        }
        #mic:hover { background: #2ff8a6; }
        #title {
            background: rgba(16, 16, 32, 0.9);
            border-radius: 8px 8px 0 0;
            padding: 0 4px;
        }
        #handle { color: #8a8a9a; font-size: 13px; padding: 1px 8px; }
        #handle:hover { color: #00e08a; }
        #close {
            background: transparent; color: #ff6b78;
            font-size: 11px; font-weight: bold;
            border: none; padding: 0 7px;
        }
        #close:hover { background: #5f1f2b; border-radius: 6px; }
        #mute {
            background: transparent; color: #00e08a;
            font-size: 11px; font-weight: bold;
            border: none; padding: 0 6px;
        }
        #mute:hover { background: #16322a; border-radius: 6px; }
        #chat-tab {
            background: rgba(16, 16, 32, 0.92);
            color: #00e08a; font-size: 18px;
            border-radius: 14px;
            border: 1px solid #00e08a;
            padding: 2px 8px;
        }
        #chat-tab:hover { background: #1a1a3a; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("bg")
        box.set_margin_top(2)
        box.set_margin_start(2)
        box.set_margin_end(2)
        self.add(box)

        # tiny title strip: drag ≡ to move, ✕ to close
        tbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tbox = Gtk.EventBox()
        tbox.set_name("title")
        hl = Gtk.Label(label="⠿")
        hl.set_name("handle")
        tbox.add(hl)
        tbox.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                        | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        tbox.connect("button-press-event", self._on_drag_start)
        tbox.connect("button-release-event", self._on_drag_stop)
        tbox.connect("motion-notify-event", self._on_drag_move)
        closeb = Gtk.Button(label="✕")
        closeb.set_name("close")
        closeb.set_relief(Gtk.ReliefStyle.NONE)
        closeb.set_tooltip_text("Hide Lino to the tray (use the tray icon to get it back)")
        closeb.connect("clicked", lambda w: self.hide())
        self.muted = False
        muteb = Gtk.Button(label="🔊")
        muteb.set_name("mute")
        muteb.set_relief(Gtk.ReliefStyle.NONE)
        muteb.set_tooltip_text("Mute / unmute Lino's voice")
        muteb.connect("clicked", self.toggle_mute)
        self.muteb = muteb
        tbar.pack_start(tbox, True, True, 0)
        tbar.pack_end(closeb, False, False, 0)
        tbar.pack_end(muteb, False, False, 0)
        box.pack_start(tbar, False, False, 0)

        # circular photo bubble — Lino's face
        face = BubbleFace(BUBBLE_PHOTO, BUBBLE_SIZE)
        face.set_name("bubble")
        box.pack_start(face, False, False, 0)

        # Lino's name under the icon
        name_label = Gtk.Label(label=ASSISTANT_NAME + " Linux")
        name_label.set_name("name")
        box.pack_start(name_label, False, False, 0)

        # voice state line (ready / speak now / thinking...)
        status = Gtk.Label(label="offline · ready")
        status.set_name("status")
        box.pack_start(status, False, False, 0)

        # heard-text / reply line (what you said, or Lino's answer)
        heard = Gtk.Label(label="")
        heard.set_name("heard")
        box.pack_start(heard, False, False, 0)

        # visible mic button — click to speak, click again (or just stop
        # talking) to send, like ChatGPT's voice chat
        mic = Gtk.Button(label="🎤  TAP TO SPEAK")
        mic.set_name("mic")
        mic.connect("clicked", self.on_mic)
        box.pack_start(mic, False, False, 0)

        self.heard_label = heard
        self.status = status
        self.mic = mic
        self.listening = False
        self.conversation_mode = False

        self.audio_queue = queue.Queue()
        self.rec_thread = None
        self._stt = None
        self.brain = EchoBrain(user_name="Studio")
        self.chat_history = []
        self.ai_mode = "smart"
        self.ai_model = AI_MODEL

        self.connect("draw", self._draw_transparent_bg)
        self.connect("button-press-event", self._on_bubble_click)
        self.connect("destroy", Gtk.main_quit)
        self.show_all()

        self._make_tray()
        self._make_chat_tab()

        # Pin to the top-center of the monitor, then stay there.
        GLib.idle_add(self.pin_to_top)
        GLib.timeout_add(2000, self.stay_active)

        # greet
        GLib.timeout_add(400, self.greet)
        # try to enable vosk in background
        GLib.timeout_add(1500, self.try_enable_stt)

    def _draw_transparent_bg(self, widget, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        return False

    def _on_bubble_click(self, widget, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if ev.button == 1:
            self.toggle_mic()
            return True
        if ev.button == 3:
            self._popup_menu(ev.button, ev.time)
            return True
        return False

    def _popup_menu(self, button, time):
        menu = Gtk.Menu()
        for label, cb in (("🎤  Speak", self.toggle_mic),
                          ("💬  Chat", self.on_chat),
                          ("🙈  Hide to tray", lambda w: self.hide()),
                          ("✕  Quit Lino", lambda w: self.destroy())):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", cb)
            menu.append(item)
        menu.show_all()
        menu.popup(None, None, None, None, button, time)

    def _tray_menu(self, icon, button, time):
        menu = Gtk.Menu()
        for label, cb in (("👁  Show Lino", lambda w: self.present()),
                          ("🎤  Speak", self.toggle_mic),
                          ("💬  Chat", self.on_chat),
                          ("✕  Quit Lino", lambda w: self.destroy())):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", cb)
            menu.append(item)
        menu.show_all()
        menu.popup(None, None, None, None, button, time)

    def _make_tray(self):
        try:
            self.tray = Gtk.StatusIcon()
            self.tray.set_from_file(BUBBLE_PHOTO)
            self.tray.set_title("Lino Linux")
            self.tray.set_tooltip_text("Lino Linux — voice assistant")
            self.tray.connect("activate", lambda i: self.present())
            self.tray.connect("popup-menu", self._tray_menu)
        except Exception as e:
            print("tray unavailable:", e)
            self.tray = None

    def _on_drag_start(self, w, ev):
        if ev.button == 1:
            wx, wy = self.get_position()
            self._drag_off = (int(ev.x_root - wx), int(ev.y_root - wy))
            return True
        return False

    def _on_drag_move(self, w, ev):
        if getattr(self, "_drag_off", None) is not None:
            self.move(int(ev.x_root - self._drag_off[0]),
                      int(ev.y_root - self._drag_off[1]))
            self._user_moved = True
            return True
        return False

    def _on_drag_stop(self, w, ev):
        self._drag_off = None
        return False

    def _monitor_geo(self, x, y):
        scr = self.get_screen()
        for m in range(scr.get_n_monitors()):
            g = scr.get_monitor_geometry(m)
            if g.x <= x < g.x + g.width and g.y <= y < g.y + g.height:
                return g
        return None

    def _bubble_target(self):
        """Top-center of the monitor holding the start bar."""
        geo = _panel_geometry()
        if geo:
            px, py, pw, ph = geo
            g = self._monitor_geo(px + pw // 2, py + ph // 2)
            if g is not None:
                return (g.x + (g.width - BUBBLE_SIZE) // 2, g.y)
        scr = self.get_screen()
        best = None
        for m in range(scr.get_n_monitors()):
            g = scr.get_monitor_geometry(m)
            area = g.width * g.height
            if best is None or area > best[0]:
                best = (area, g)
        if best is not None:
            g = best[1]
            return (g.x + (g.width - BUBBLE_SIZE) // 2, g.y)
        return None

    def _make_chat_tab(self):
        """A small 💬 tab on the start bar, right where the tray icons live,
        so clicking near the Bluetooth icon pops up the chat bar."""
        self.chat_tab = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.chat_tab.set_title("Lino Chat")
        self.chat_tab.set_decorated(False)
        self.chat_tab.set_keep_above(True)
        self.chat_tab.set_app_paintable(True)
        self.chat_tab.set_accept_focus(False)
        lb = Gtk.Label(label="💬")
        lb.set_name("chat-tab")
        eb = Gtk.EventBox()
        eb.add(lb)
        eb.connect("button-press-event",
                   lambda w, e: (self.on_chat(None), True)[1])
        self.chat_tab.add(eb)
        self.chat_tab.connect("draw", self._draw_transparent_bg)
        self.chat_tab.connect("destroy", lambda w: None)
        self.chat_tab.show_all()
        self._pin_chat_tab()

    def _tray_geo(self):
        """Where the Lino tray icon currently sits: (x, y, w, h) or None."""
        icon = getattr(self, "tray", None)
        if icon is None:
            return None
        try:
            res = icon.get_geometry()
            rect = None
            ok = True
            if isinstance(res, (tuple, list)):
                if res and not res[0]:
                    return None
                for item in res:
                    if isinstance(item, Gdk.Rectangle):
                        rect = item
                        break
                    if (isinstance(item, (tuple, list)) and len(item) == 4):
                        rect = Gdk.Rectangle(*item)
                        break
            if rect is None:
                return None
            # a 1x1 placeholder means the tray hasn't laid the icon out yet
            if rect.width < 4 or rect.height < 4:
                return None
            return (rect.x, rect.y, rect.width, rect.height)
        except Exception:
            return None

    def _pin_chat_tab(self):
        """Anchor the chat tab directly above the tray icon."""
        tg = self._tray_geo()
        if tg:
            x, y, w, h = tg
            self.chat_tab.move(x + w // 2 - 20, y - 36)
            return False
        geo = _panel_geometry()
        if geo:
            px, py, pw, ph = geo
            self.chat_tab.move(px + pw - 110, py - 36)
        return False

    def stay_active(self):
        """Keep the bubble always-on-top; stay where the user moved it."""
        self.set_keep_above(True)
        if not getattr(self, "_user_moved", False):
            t = self._bubble_target()
            if t:
                x, y = t
                cx, cy = self.get_position()
                if abs(cx - x) > 40 or abs(cy - y) > 40:
                    self.move(x, y)
        self._pin_chat_tab()
        return True

    def greet(self):
        self.heard_label.set_text("👋 " + GREETING)
        run_tts_async(GREETING)
        self.status.set_text("offline · I heard you 👂")
        return False

    def pin_to_top(self):
        """Pin the bubble to the top-center of the monitor holding the start bar."""
        t = self._bubble_target()
        if t:
            self.move(*t)
        return False

    def try_enable_stt(self):
        def _set(text):
            GLib.idle_add(lambda: self.status.set_text(text))
        try:
            import vosk  # noqa
            self._stt = vosk
            self.status.set_text("offline · voice engine ready")
            if not os.path.isdir(os.path.join(BASE, "model")):
                threading.Thread(target=self.dl_model, daemon=True).start()
        except ImportError:
            self.status.set_text("offline · voice needs vosk (pip install vosk)")
        return False

    def ensure_vosk(self):
        model_dir = os.path.join(BASE, "model")
        if not os.path.isdir(model_dir):
            self.dl_model()
        GLib.idle_add(lambda: self.status.set_text("offline · voice engine ready"))

    def dl_model(self):
        GLib.idle_add(lambda: self.status.set_text("offline · downloading voice model (~40 MB)..."))
        # small English model
        url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        import urllib.request
        import zipfile
        try:
            zpath = os.path.join(BASE, "vosk-model-small-en-us-0.15.zip")
            urllib.request.urlretrieve(url, zpath)
            with zipfile.ZipFile(zpath) as z:
                z.extractall(BASE)
            os.rename(os.path.join(BASE, "vosk-model-small-en-us-0.15"), os.path.join(BASE, "model"))
            os.remove(zpath)
            self._stt = self._stt or __import__("vosk")
            GLib.idle_add(lambda: self.status.set_text("offline · voice engine ready"))
        except Exception as e:
            GLib.idle_add(lambda: self.status.set_text("offline · model download failed: " + str(e)[:40]))

    def say_ready(self):
        run_tts_async("I am ready. Say something.")

    def on_mic(self, w):
        self.toggle_mic()

    def toggle_mic(self):
        if self.listening:
            self.stop_listen()
        else:
            self.start_listen()

    def toggle_mute(self, w=None):
        self.muted = not self.muted
        _volume(mute=self.muted)
        self.muteb.set_label("🔇" if self.muted else "🔊")
        self.status.set_text("🔇 muted" if self.muted else "🔊 unmuted")
        run_tts_async("Muted." if self.muted else "Unmuted.")

    def on_chat(self, w):
        """Open a chat window — type messages, get AI replies (no mic needed)."""
        dlg = Gtk.Dialog(title="Chat with Lino", transient_for=self,
                         flags=Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dlg.set_default_size(320, 400)

        box = dlg.get_content_area()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_left_margin(10)
        view.set_right_margin(10)
        view.set_top_margin(10)
        view.set_bottom_margin(10)
        buf = view.get_buffer()
        buf.create_tag("user", foreground="#00e08a", weight=Pango.Weight.BOLD)
        buf.create_tag("ai", foreground="#ffffff")
        buf.create_tag("command", foreground="#00b06a", style=Pango.Style.ITALIC)
        buf.create_tag("thinking", foreground="#8a8a9a", style=Pango.Style.ITALIC)
        scrolled.add(view)
        box.pack_start(scrolled, True, True, 0)

        hint = Gtk.Label(label="Type a message — pick an AI mode below.")
        hint.set_name("status")
        hint.set_margin_top(4)
        hint.set_margin_bottom(4)
        box.pack_start(hint, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        micb = Gtk.Button(label="🎤")
        micb.set_tooltip_text("Tap to speak")
        micb.get_style_context().add_class("suggested-action")

        def on_micb(_w):
            self.toggle_mic()
            micb.set_label("⏹" if self.listening else "🎤")

        micb.connect("clicked", on_micb)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Type a message...")
        send = Gtk.Button(label="Send")
        send.get_style_context().add_class("suggested-action")

        def on_lookup(_w):
            q = entry.get_text().strip()
            if not q:
                return
            url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(q)
            if not _launch("firefox", url):
                _launch("xdg-open", url)
            run_tts_async("Looking up " + q + " on the web.")

        lu = Gtk.Button(label="🔎")
        lu.set_tooltip_text("Look it up online")
        lu.connect("clicked", on_lookup)
        row.pack_start(micb, False, False, 0)
        row.pack_start(entry, True, True, 0)
        row.pack_start(send, False, False, 0)
        row.pack_start(lu, False, False, 0)
        box.pack_start(row, False, False, 0)

        mrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        mlabel = Gtk.Label(label="AI mode:")
        mlabel.set_name("status")
        combo = Gtk.ComboBoxText()
        combo.append("smart", "Offline AI (smart)")
        combo.append("echo", "Quick echo")
        combo.append("online", "Online search")
        combo.set_active({"smart": 0, "echo": 1, "online": 2}.get(self.ai_mode, 0))
        combo.set_tooltip_text("Which brain answers: local smart AI, quick rules, or web search")

        def on_mode(ch):
            self.ai_mode = ch.get_active_id() or "smart"

        combo.connect("changed", on_mode)
        mrow.pack_start(mlabel, False, False, 0)
        mrow.pack_start(combo, False, False, 0)

        dbtn = Gtk.Button(label="Download AI model…")
        dbtn.set_tooltip_text("Pull a bigger/smarter model from the web (ollama)")
        dbtn.connect("clicked", lambda w: self._download_dialog(append))
        mrow.pack_end(dbtn, False, False, 0)
        box.pack_start(mrow, False, False, 0)

        vrow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vlab = Gtk.Label(label="Voice:")
        vlab.set_name("status")
        vcombo = Gtk.ComboBoxText()
        for label, f in PIPER_VOICES.items():
            vcombo.append(f, label)
        vcombo.set_active_id(CURRENT_VOICE)
        vcombo.set_tooltip_text("Which voice Lino speaks with (offline, neural)")

        def on_vcombo(ch):
            _set_voice(ch.get_active_id() or "en_US-lessac-medium")

        vcombo.connect("changed", on_vcombo)
        vrow.pack_start(vlab, False, False, 0)
        vrow.pack_start(vcombo, True, True, 0)
        box.pack_start(vrow, False, False, 0)

        dlg.add_button("Close", Gtk.ResponseType.CLOSE)

        def append(role, text):
            tag = {"assistant": "ai", "thinking": "thinking",
                   "command": "command"}.get(role, "user")
            label = {"assistant": "💬 ", "thinking": "⏳ ",
                     "command": "🖱 "}.get(role, "🧑 ")
            end = buf.get_end_iter()
            buf.insert_with_tags_by_name(end, label + text + "\n\n", tag)
            GLib.idle_add(_scroll_to_end)

        def _scroll_to_end():
            adj = scrolled.get_vadjustment()
            if adj is not None:
                try:
                    adj.set_value(adj.get_upper())
                except Exception:
                    pass
            return False

        def send_msg(*_a):
            text = entry.get_text().strip()
            if not text:
                return
            entry.set_text("")
            append("user", text)
            # Computer control first — same commands as voice (mouse/keyboard).
            if self.handle_command(text):
                note = self.status.get_text() or "Command executed"
                append("command", "✓ " + note)
                return
            mark = buf.create_mark(None, buf.get_end_iter(), True)
            append("thinking", "Lino is thinking...")
            send.set_sensitive(False)
            entry.set_sensitive(False)

            def work():
                reply = self.ask_lino(text)
                GLib.idle_add(finish, reply, text, mark)

            threading.Thread(target=work, daemon=True).start()

        def finish(reply, user_text, mark):
            if not dlg.get_visible():
                return
            start = buf.get_iter_at_mark(mark)
            end = buf.get_end_iter()
            if start.get_offset() < end.get_offset():
                buf.delete(start, end)
            buf.delete_mark(mark)
            send.set_sensitive(True)
            entry.set_sensitive(True)
            reply = self._run_reply_actions(reply, user_text)
            if self.status.get_text().startswith("🖱"):
                append("command", "✓ " + self.status.get_text().lstrip("🖱 "))
            append("assistant", reply)
            self.status.set_text("💬 AI chat")
            run_tts_async(reply)

        send.connect("clicked", send_msg)
        entry.connect("activate", send_msg)
        dlg.connect("response", lambda d, r: d.destroy())
        geo = _panel_geometry()
        tg = self._tray_geo()
        if geo and getattr(self, "chat_tab", None):
            tx, ty = self.chat_tab.get_position()
            anchor_y = tg[1] if tg else geo[1]
            dlg.move(tx - 300, anchor_y - 410)
        dlg.show_all()
        entry.grab_focus()

    def start_listen(self):
        self.listening = True
        self._finishing = False
        self.mic.set_label("⏹  TAP TO STOP")
        self.heard_label.set_text("Listening...")
        self.status.set_text("speak now · stops when you stop talking")
        self.audio_queue = queue.Queue()
        self._rec_proc = None
        self._rec_stop = threading.Event()
        self.rec_thread = threading.Thread(target=self.capture, daemon=True)
        self.rec_thread.start()

    def stop_listen(self):
        self._rec_stop.set()
        if self._rec_proc:
            try:
                self._rec_proc.terminate()
            except Exception:
                pass
            try:
                self._rec_proc.wait(timeout=2)
            except Exception:
                try:
                    self._rec_proc.kill()
                except Exception:
                    pass
        if self.rec_thread:
            self.rec_thread.join(timeout=3)
        self._finalize_capture()

    def _finalize_capture(self):
        """Shared finish path: switch the mic button back and recognize."""
        if self._finishing:
            return
        self._finishing = True
        self.listening = False
        self.mic.set_label("🎤  TAP TO SPEAK")
        self.status.set_text("thinking...")
        threading.Thread(target=self.recognize, daemon=True).start()

    def capture(self):
        """Record mic through a pipe; stop automatically 1.6s after you stop
        talking (ChatGPT-style), or when STOP is tapped. Writes capture.wav."""
        import audioop
        out = os.path.join(BASE, "capture.wav")
        if os.path.exists(out):
            try:
                os.remove(out)
            except OSError:
                pass
        cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "raw"]
        src = _input_source()
        env = os.environ
        if src:
            cmd[1:1] = ["-D", "pulse"]
            env = dict(os.environ)
            env["PULSE_SOURCE"] = src
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, env=env)
        except FileNotFoundError:
            return
        self._rec_proc = proc
        pcm = bytearray()
        spoke = False
        last_speech = _time.time()
        start = _time.time()
        try:
            while not self._rec_stop.is_set():
                data = proc.stdout.read(8000)
                if not data:
                    break
                pcm.extend(data)
                rms = audioop.rms(data, 2)
                if rms > 200:
                    spoke = True
                    last_speech = _time.time()
                if spoke and _time.time() - last_speech > 1.6:
                    break
                if not spoke and _time.time() - start > 6:
                    break
                if _time.time() - start > 30:
                    break
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            with wave.open(out, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(pcm))
        except Exception:
            pass
        if not self._rec_stop.is_set():
            GLib.idle_add(self._finalize_capture)

    def recognize(self):
        if not self._stt or not os.path.isdir(os.path.join(BASE, "model")):
            GLib.idle_add(lambda: self.status.set_text("voice engine not ready yet"))
            return
        path = os.path.join(BASE, "capture.wav")
        if not os.path.exists(path):
            GLib.idle_add(lambda: self.heard_label.set_text("nothing captured"))
            return
        try:
            from vosk import Model, KaldiRecognizer
            import json
            model = Model(os.path.join(BASE, "model"))
            rec = KaldiRecognizer(model, 16000)
            text = ""
            with wave.open(path, "rb") as wf:
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    if rec.AcceptWaveform(data):
                        res = json.loads(rec.Result())
                        text += res.get("text", "") + " "
            res = json.loads(rec.FinalResult())
            text += res.get("text", "")
            text = text.strip()
            GLib.idle_add(self.on_heard, text)
        except Exception as e:
            GLib.idle_add(lambda: self.heard_label.set_text("error: " + str(e)[:40]))

    def on_heard(self, text):
        self.listening = False
        self.mic.set_label("🎤  TAP TO SPEAK")
        if not text:
            self.heard_label.set_text("Didn't catch that.")
            run_tts_async("I didn't catch that.")
            return
        self.heard_label.set_text("🗣  " + text)
        if self.handle_command(text):
            return
        # Same smart AI as the chat box, so voice and chat answer identically.
        threading.Thread(target=self.voice_ask, args=(text,), daemon=True).start()

    def voice_ask(self, text):
        reply = self.ask_lino(text)
        GLib.idle_add(lambda: self._show_voice_reply(reply, text))
        self.continue_conversation()

    def _show_voice_reply(self, reply, user_text=""):
        reply = self._run_reply_actions(reply, user_text)
        self.heard_label.set_text("💬 " + reply)
        self.status.set_text("💬 AI chat")
        run_tts_async(reply)
        return False

    def ask_lino(self, text):
        """One brain for voice and chat, driven by the selected AI mode."""
        mode = getattr(self, "ai_mode", "smart")
        self.chat_history.append({"role": "user", "content": text})
        if mode == "echo":
            reply = self.brain.reply(text)
            if reply:
                return reply
            return _pick("I'm in quick echo mode right now. "
                         "Say 'AI mode smart' in chat to get the full brain.",
                         "Quick mode here. For deep answers, switch the "
                         "AI mode to Offline in the chat window.")
        if mode == "online":
            url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(text)
            if not _launch("firefox", url):
                _launch("xdg-open", url)
            return "I searched online for that. Check the browser that just opened."
        t = text.lower()
        if (_has(t, "what's on my screen", "what is on my screen", "whats on my screen",
                 "look at the screen", "look at my screen", "see the screen",
                 "what do you see", "look at that", "look at this",
                 "what's going on", "what is going on", "whats going on",
                 "what's open", "what is open", "whats open", "what windows are open",
                 "what's my pc going", "what is my pc going", "what is my pc doing",
                 "what am i doing", "what have i got open", "is my pc doing",
                 "what you see", "what you're seeing", "what you are seeing",
                 "what your seeing", "what you seeing", "my computer doing",
                 "is my computer doing", "what is my computer doing")
                or ("screen" in t and "look" in t)
                or ("look" in t and ("see" in t or "what" in t))):
            q = re.sub(r"^(what's on my screen|what is on my screen|whats on my screen|"
                       r"look at the screen|look at my screen|see the screen|"
                       r"what do you see|what's going on|what is going on|whats going on|"
                       r"what's open|what is open|whats open|what windows are open|"
                       r"look at that|look at this)[ ,:?.]*", "", text,
                       flags=re.IGNORECASE).strip()
            return self._describe_screen(q or "What is on the screen right now?")
        model = getattr(self, "ai_model", AI_MODEL)
        reply = None
        for candidate in (model, AI_FALLBACK):
            try:
                msgs = [{"role": "system", "content": AI_SYSTEM}]
                msgs += self.chat_history[-13:]
                reply = ai_chat(msgs, model=candidate, tools=TOOLS,
                                tool_handler=self._run_tool)
            except Exception:
                reply = None
            if reply:
                break
        if not reply:
            reply = self.brain.reply(text) or _pick(
                "The AI engine is offline, but I'm here! Try a command, "
                "or ask me something again.",
                "The smart brain isn't reachable right now, so I'll improvise.",
                "Offline AI is busy. I'm still listening — what else can I do?")
        return reply

    def _download_dialog(self, append):
        dlg = Gtk.Dialog(title="Download an AI model", transient_for=self,
                         flags=Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dlg.set_default_size(380, -1)
        box = dlg.get_content_area()
        lbl = Gtk.Label(label="Pick a model, or type any ollama name:")
        box.pack_start(lbl, False, False, 8)
        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. llama3.2:3b")
        box.pack_start(entry, False, False, 8)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for name, desc in list(AI_MODELS.items())[1:]:
            b = Gtk.Button(label=name)
            b.set_tooltip_text(desc)
            b.connect("clicked", lambda w, n=name: entry.set_text(n))
            row.pack_start(b, False, False, 0)
        box.pack_start(row, False, False, 8)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok = dlg.add_button("Download", Gtk.ResponseType.OK)
        ok.get_style_context().add_class("suggested-action")
        dlg.connect("response", self._on_dl_response, entry, append)
        dlg.show_all()
        entry.grab_focus()

    def _on_dl_response(self, dlg, resp, entry, append):
        if resp == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            if name:
                append("command", "📥 Downloading " + name + " — this can take a while...")
                self._download_model(name, append)
        dlg.destroy()

    def _download_model(self, name, append):
        def work():
            try:
                p = subprocess.Popen(["ollama", "pull", name],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
                for line in p.stdout:
                    ln = line.strip()
                    if ln:
                        GLib.idle_add(lambda l=ln: append("command", l))
                p.wait()
                if p.returncode == 0:
                    self.ai_model = name
                    GLib.idle_add(lambda: append(
                        "assistant",
                        "✅ " + name + " is installed and now selected as the AI brain."))
                else:
                    GLib.idle_add(lambda: append(
                        "assistant", "⚠️ Download failed for " + name + "."))
            except Exception as e:
                GLib.idle_add(lambda: append("assistant", "⚠️ " + str(e)))

        threading.Thread(target=work, daemon=True).start()

    def continue_conversation(self):
        """Keep listening automatically when conversation mode is on."""
        if self.conversation_mode and not self.listening:
            GLib.timeout_add(1200, self.auto_listen)

    def auto_listen(self):
        if self.conversation_mode and not self.listening:
            self.start_listen()
        return False

    # ---- voice commands: mouse & keyboard ----
    _ACTION_REQ = {
        "OPEN_CODE": ("code", "chat", "bot", "opencode"),
        "OPEN_BROWSER": ("browser", "firefox", "web", "internet"),
        "OPEN_TERMINAL": ("terminal", "console"),
        "OPEN_FILES": ("files", "folder", "documents"),
        "CLICK": ("click", "tap"),
        "RIGHT_CLICK": ("right", "click"),
        "DOUBLE_CLICK": ("double", "click"),
        "SCROLL_UP": ("scroll", "up"),
        "SCROLL_DOWN": ("scroll", "down"),
        "COPY": ("copy",),
        "PASTE": ("paste",),
        "VOLUME_UP": ("volume", "louder"),
        "VOLUME_DOWN": ("volume", "quieter"),
        "MUTE": ("mute",),
        "MINIMIZE": ("minimize",),
        "MAXIMIZE": ("maximize",),
    }

    def _exec_action(self, tag, user_text=""):
        """Execute an [ACTION:...] tag. Returns a short confirmation or None.
        If we know what the user said, ignore tags that don't match it."""
        t = tag.strip().upper()
        if user_text:
            req = self._ACTION_REQ.get(t)
            if req and not _has(user_text.lower(), *req):
                return None
        if t == "OPEN_CODE":
            _activate_app("OpenCode"); return "opening code chat"
        if t == "OPEN_BROWSER":
            _launch("firefox"); return "opening browser"
        if t == "OPEN_TERMINAL":
            _launch("x-terminal-emulator"); return "opening terminal"
        if t == "OPEN_FILES":
            _launch("xdg-open", home_dir()); return "opening files"
        if t == "CLICK":
            mouse_click(1); return "clicked"
        if t == "RIGHT_CLICK":
            mouse_click(3); return "right clicked"
        if t == "DOUBLE_CLICK":
            mouse_click(1); mouse_click(1); return "double clicked"
        if t == "SCROLL_UP":
            mouse_scroll(3, up=True); return "scrolled up"
        if t == "SCROLL_DOWN":
            mouse_scroll(3, up=False); return "scrolled down"
        if t == "COPY":
            key_press(54, ctrl=True); return "copied"
        if t == "PASTE":
            key_press(55, ctrl=True); return "pasted"
        if t == "VOLUME_UP":
            _volume(plus=10); return "volume up"
        if t == "VOLUME_DOWN":
            _volume(plus=-10); return "volume down"
        if t == "MUTE":
            _volume(mute=True); return "muted"
        if t == "MINIMIZE":
            _launch("xdotool", "getactivewindow", "windowminimize"); return "minimized the window"
        if t == "MAXIMIZE":
            _launch("xdotool", "getactivewindow", "windowsize", "100%", "100%"); return "maximized the window"
        if t.startswith("TYPE:"):
            phrase = t[5:].strip()
            if phrase:
                type_text(phrase)
                run_tts_async("Typing, " + phrase + ".")
                return "typed " + phrase
            return None
        return None

    def _describe_screen(self, question):
        """Screenshot the screen, combine with the real window list, and answer."""
        wins = _window_list()
        ctx = ""
        if wins:
            ctx = (" Right now these windows are open on your PC: "
                   + "; ".join(wins) + ".")
        self.status.set_text("👁 looking at the screen...")
        b64 = _screenshot_b64()
        if not b64:
            if wins:
                return ("I can't grab the screen right now. Right now you have "
                        "open: " + "; ".join(wins) + ".")
            return "I could not capture the screen."
        msgs = [{"role": "system", "content": AI_SYSTEM + ctx},
                {"role": "user", "content": (question or "Describe what is on the screen right now, briefly.")}]
        reply = ai_chat(msgs, model=VISION_MODEL, timeout=120, images=[b64])
        if reply:
            return reply
        if wins:
            return "Right now you have open: " + "; ".join(wins) + "."
        return "I saw the screen but could not describe it."

    def _run_tool(self, name, args):
        """Execute a native Ollama tool call; narrate and return the result."""
        try:
            if name == "open_app":
                app = str(args.get("name", "")).lower().strip()
                if _activate_app(app):
                    self.status.set_text("🖱 clicked " + app)
                    run_tts_async("Opening " + app + ".")
                    return "opened " + app
                for key, cmd in _APP_LAUNCH.items():
                    if key in app:
                        _launch(*cmd)
                        self.status.set_text("🖱 opened " + key)
                        run_tts_async("Opening " + key + ".")
                        return "opened " + key
                return "error: unknown app " + app
            if name == "click":
                b = args.get("button", "left")
                if b == "right":
                    mouse_click(3)
                elif b == "double":
                    mouse_click(1); mouse_click(1)
                else:
                    mouse_click(1)
                self.status.set_text("🖱 " + b + " click")
                run_tts_async({"right": "Right click.", "double": "Double click.",
                               "left": "Click."}[b])
                return "clicked"
            if name == "type_text":
                txt = str(args.get("text", ""))
                if txt:
                    type_text(txt)
                    self.status.set_text("⌨️ typed: " + txt)
                    run_tts_async("Typing, " + txt + ".")
                return "typed"
            if name == "press_key":
                key = str(args.get("key", "")).lower().strip()
                entry = _PRESS_KEYS.get(key)
                if not entry:
                    return "error: unknown key " + key
                if isinstance(entry, tuple):
                    code, mods = entry
                    kw = {}
                    for m in mods:
                        kw[m] = True
                    key_press(code, **kw)
                else:
                    key_press(entry)
                self.status.set_text("⌨️ " + key)
                run_tts_async("Pressing " + key.replace("_", " ") + ".")
                return "pressed " + key
            if name == "scroll":
                up = args.get("direction") == "up"
                mouse_scroll(3, up=up)
                self.status.set_text("🖱 scrolled " + args.get("direction", ""))
                run_tts_async("Scrolling " + args.get("direction", "") + ".")
                return "scrolled"
            if name == "volume":
                act = args.get("action", "up")
                if act == "mute":
                    _volume(mute=True)
                else:
                    _volume(plus=10 if act == "up" else -10)
                self.status.set_text("🔊 volume " + act)
                run_tts_async("Volume " + act + ".")
                return "volume " + act
            if name == "look_at_screen":
                desc = self._describe_screen(args.get("question", ""))
                self.status.set_text("👁 screen: " + desc[:60])
                return "screen contents: " + desc
            if name == "open_chat":
                GLib.idle_add(self.on_chat, None)
                self.status.set_text("💬 opening chat")
                run_tts_async("Opening chat.")
                return "opened chat"
        except Exception as e:
            return "error: " + str(e)
        return "error: unknown tool " + name

    def _run_reply_actions(self, reply, user_text=""):
        """Execute any [ACTION:...] tags the AI put in its reply; return clean text."""
        if not reply or "ACTION:" not in reply:
            return reply
        notes = []
        for tag in re.findall(r"\[ACTION:([^\]]+)\]", reply, re.IGNORECASE):
            note = self._exec_action(tag, user_text)
            if note:
                notes.append(note)
        clean = re.sub(r"\[ACTION:[^\]]+\]", "", reply, flags=re.IGNORECASE)
        clean = re.sub(r"\s+", " ", clean).strip()
        if notes:
            self.status.set_text("🖱 " + "; ".join(notes))
        return clean

    def _click_named(self, t, text):
        """'click <thing>' -> find that window and bring it up. Returns True if handled."""
        m = re.search(r"click\s+(?:on\s+|the\s+)?(.+?)[,\.]?$", t)
        if not m:
            return False
        target = m.group(1).strip()
        target = re.sub(r"\s+(now|please|thanks)$", "", target).strip()
        if len(target) < 2:
            return False
        if _activate_app(target):
            self.status.set_text("🖱 clicked " + target)
            run_tts_async("Clicked " + target + ".")
            return True
        for key, cmd in _APP_LAUNCH.items():
            if key in target:
                _launch(*cmd)
                self.status.set_text("🖱 opened " + key)
                run_tts_async("Opened " + key + ".")
                return True
        return False

    def handle_command(self, text):
        t = text.lower()
        # ---- open apps (FIRST: "click open code" must open code, not click) ----
        if _intent(t, ("open",), ("terminal", "console", "command line")):
            subprocess.Popen(["x-terminal-emulator"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status.set_text("🖥 opened terminal"); run_tts_async("Opening terminal."); return True
        if (_intent(t, ("open",), ("code", "chat", "bot", "opencode"))
                or _intent(t, ("bring", "code")) or _intent(t, ("show", "chat"))
                or _intent(t, ("chat", "bot"))
                or "opencode" in t or "code chat" in t):
            _activate_app("OpenCode")
            self.status.set_text("💻 opening code chat"); run_tts_async("Opening code chat."); return True
        if _intent(t, ("open", "lino")) or "lino chat" in t or "chat window" in t:
            self.on_chat(None)
            self.status.set_text("💬 opening Lino chat")
            run_tts_async("Opening chat.")
            return True
        if _intent(t, ("open",), ("browser", "firefox", "web", "internet")):
            _launch("firefox"); self.status.set_text("🌐 opening browser"); run_tts_async("Opening browser."); return True
        if _intent(t, ("open",), ("files", "folder", "file manager", "home folder", "documents")):
            _launch("xdg-open", home_dir()); self.status.set_text("🗀 opening files"); run_tts_async("Opening files."); return True
        if _intent(t, ("open",), ("calculator", "calc")) or "calculator" in t:
            _launch("gnome-calculator"); self.status.set_text("🧮 opening calculator"); run_tts_async("Opening calculator."); return True
        # ---- vision: look at the screen (Gemini-style) ----
        if (_has(t, "look at the screen", "look at my screen", "see the screen",
                 "look at the", "what's on my screen", "whats on my screen",
                 "what is on my screen", "look at that", "take a look at the screen",
                 "look at the desktop", "what do you see", "look at this")
                or (t.startswith("look") and "screen" in t)):
            q = re.sub(r"^(look at the screen|look at my screen|look at the screen|"
                       r"whats? on my screen|what do you see|look at the|look at this)"
                       r"[ ,:.]*", "", text, flags=re.IGNORECASE).strip()
            def _done(desc):
                self.heard_label.set_text("👁 " + desc)
                self.status.set_text("👁 screen read")
                run_tts_async(desc)
            threading.Thread(target=lambda: GLib.idle_add(
                _done, self._describe_screen(q)), daemon=True).start()
            self.status.set_text("👁 looking at the screen...")
            return True
        # ---- mouse clicks (specific before generic) ----
        if "right click" in t:
            mouse_click(3); self.status.set_text("🖱 right-click"); run_tts_async("Right click."); return True
        if "double click" in t:
            mouse_click(1); mouse_click(1); self.status.set_text("🖱 double-click"); run_tts_async("Double click."); return True
        if "middle click" in t:
            mouse_click(2); self.status.set_text("🖱 middle-click"); run_tts_async("Middle click."); return True
        if "click" in t and self._click_named(t, text):
            return True
        if "click" in t:
            mouse_click(1); self.status.set_text("🖱 clicked"); run_tts_async("Click."); return True
        # ---- mouse movement ----
        if _has(t, "move mouse", "move the mouse") or ("mouse" in t and _has(t, "up", "down", "left", "right")):
            px = _move_px(t)
            dx = dy = 0
            if "up" in t:
                dy = -px
            elif "down" in t:
                dy = px
            if "left" in t:
                dx = -px
            elif "right" in t:
                dx = px
            if dx or dy:
                cx, cy = mouse_pos()
                mouse_move(cx + dx, cy + dy)
                d = ("up" if dy < 0 else "down") if dx == 0 else ("left" if dx < 0 else "right")
                self.status.set_text("🖱 mouse " + d)
                run_tts_async("Moving mouse " + d + ".")
                return True
        if "scroll up" in t:
            mouse_scroll(3, up=True); self.status.set_text("🖱 scrolled up"); run_tts_async("Scrolling up."); return True
        if "scroll down" in t:
            mouse_scroll(3, up=False); self.status.set_text("🖱 scrolled down"); run_tts_async("Scrolling down."); return True
        if "mouse" in t and "center" in t:
            g = _panel_geometry()
            if g:
                mouse_move(g[0] + g[2] / 2, g[1] + g[3] / 2)
            else:
                mouse_move(1920, 540)
            self.status.set_text("🖱 mouse centered"); run_tts_async("Mouse centered."); return True
        if "copy" in t:
            key_press(54, ctrl=True); self.status.set_text("⌨️ copied"); run_tts_async("Copy."); return True
        if "paste" in t:
            key_press(55, ctrl=True); self.status.set_text("⌨️ pasted"); run_tts_async("Paste."); return True
        if "cut" in t:
            key_press(53, ctrl=True); self.status.set_text("⌨️ cut"); run_tts_async("Cut."); return True
        if "undo" in t:
            key_press(52, ctrl=True); self.status.set_text("⌨️ undo"); run_tts_async("Undo."); return True
        if "enter" in t or "enter key" in t:
            key_press(36); self.status.set_text("⌨️ enter"); run_tts_async("Enter."); return True
        if "space bar" in t or "space key" in t or (t.strip() == "space" and "backspace" not in t):
            key_press(65); self.status.set_text("⌨️ space"); run_tts_async("Space."); return True
        if "tab" in t:
            key_press(23); self.status.set_text("⌨️ tab"); run_tts_async("Tab."); return True
        if "escape" in t or "escape key" in t:
            key_press(9); self.status.set_text("⌨️ escape"); run_tts_async("Escape."); return True
        if "save" in t:
            key_press(39, ctrl=True); self.status.set_text("⌨️ save"); run_tts_async("Save."); return True
        if "select all" in t:
            key_press(38, ctrl=True); self.status.set_text("⌨️ select all"); run_tts_async("Select all."); return True
        # ---- keyboard navigation ----
        for phrase, code, name in (("page up", 112, "Page up"), ("page down", 117, "Page down"),
                                   ("backspace", 22, "Backspace"), ("delete key", 119, "Delete"),
                                   ("press home", 110, "Home"), ("press end", 115, "End"),
                                   ("arrow up", 111, "Up"), ("up arrow", 111, "Up"),
                                   ("press up", 111, "Up"), ("arrow down", 116, "Down"),
                                   ("down arrow", 116, "Down"), ("press down", 116, "Down"),
                                   ("arrow left", 113, "Left"), ("left arrow", 113, "Left"),
                                   ("press left", 113, "Left"), ("arrow right", 114, "Right"),
                                   ("right arrow", 114, "Right"), ("press right", 114, "Right")):
            if phrase in t:
                key_press(code)
                self.status.set_text("⌨️ " + name)
                run_tts_async(name + ".")
                return True
        # ---- type / say / write ----
        if _has(t, "type ", "say ", "write "):
            phrase = None
            for w in ("type ", "say ", "write "):
                idx = text.lower().find(w)
                if idx != -1:
                    phrase = text[idx + len(w):].strip()
                    break
            if phrase:
                type_text(phrase)
                self.status.set_text("⌨️ typed: " + phrase)
                run_tts_async("Typing, " + phrase + ".")
                return True
        if "close window" in t:
            key_press(71, alt=True); self.status.set_text("🗔 closed window"); run_tts_async("Closed window."); return True
        if "switch window" in t or "switch windows" in t:
            key_press(23, alt=True); self.status.set_text("🗔 switched window"); run_tts_async("Switched window."); return True
        # ---- conversation mode ----
        if _has(t, "start conversation", "carry on conversation", "keep talking",
                "chat mode", "conversation mode", "start listening", "keep listening",
                "have a conversation", "talk to me"):
            self.conversation_mode = True
            self.status.set_text("💬 conversation mode ON")
            run_tts_async("Conversation mode on. I'll keep listening and chatting.")
            self.continue_conversation()
            return True
        if _has(t, "stop conversation", "stop listening", "end conversation",
                "stop talking", "quit conversation", "conversation off"):
            self.conversation_mode = False
            self.status.set_text("💬 conversation mode OFF")
            run_tts_async("Conversation mode off.")
            return True
        # ---- voice selection ----
        if _has(t, "female voice", "use female", "female speaker", "woman voice",
                "woman's voice", "girl voice", "use a female voice"):
            _set_voice("en_US-amy-medium")
            self.status.set_text("🎙 female voice"); run_tts_async("Switched to a female voice."); return True
        if _has(t, "male voice", "use male", "male speaker", "man voice",
                "man's voice", "guy voice", "boy voice", "use a male voice"):
            _set_voice("en_US-joe-medium")
            self.status.set_text("🎙 male voice"); run_tts_async("Switched to a male voice."); return True
        if _has(t, "british voice", "british accent", "use a british accent",
                "british male", "english accent"):
            _set_voice("en_GB-alan-medium")
            self.status.set_text("🎙 British voice"); run_tts_async("Switched to a British accent."); return True
        # ---- volume control ----
        if _has(t, "volume up", "turn it up", "louder", "volume up a lot"):
            n = 10 if "a lot" in t else 5
            _volume(plus=n); self.status.set_text("🔊 volume up"); run_tts_async("Volume up."); return True
        if _has(t, "volume down", "turn it down", "quieter", "volume down a lot"):
            n = 10 if "a lot" in t else 5
            _volume(plus=-n); self.status.set_text("🔉 volume down"); run_tts_async("Volume down."); return True
        if _has(t, "mute", "mute audio", "silence"):
            _volume(mute=True); self.muted = True
            self.muteb.set_label("🔇")
            self.status.set_text("🔇 muted"); run_tts_async("Muted."); return True
        if _has(t, "unmute", "unmute audio"):
            _volume(mute=False); self.muted = False
            self.muteb.set_label("🔊")
            self.status.set_text("🔊 unmuted"); run_tts_async("Unmuted."); return True
        # ---- system control ----
        if _has(t, "screenshot", "take a screenshot"):
            _launch("gnome-screenshot"); self.status.set_text("📸 screenshot"); run_tts_async("Opening screenshot tool."); return True
        if _has(t, "lock the screen", "lock screen", "lock my computer",
                "lock my pc", "lock the pc"):
            subprocess.Popen([sys.executable, os.path.join(BASE, "native", "lock_screen.py")],
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status.set_text("🔒 locking screen"); run_tts_async("Locking the screen."); return True
        if _has(t, "minimize window", "minimize the window"):
            _launch("xdotool", "getactivewindow", "windowminimize")
            self.status.set_text("🗕 minimized"); run_tts_async("Minimized the window."); return True
        if _has(t, "dock to taskbar", "move to the taskbar", "move above taskbar",
                "same width as taskbar", "move to start bar", "move to big dell",
                "move to the big dell", "big dell", "move to main monitor",
                "move to main screen", "move window up"):
            GLib.idle_add(self.pin_to_top)
            self.status.set_text("🖥 docked above the taskbar")
            run_tts_async("Docked above the taskbar.")
            return True
        if _has(t, "maximize window", "maximize the window"):
            _launch("xdotool", "getactivewindow", "windowsize", "100%", "100%")
            self.status.set_text("🗖 maximized"); run_tts_async("Maximized the window."); return True
        return False

    def on_key_press(self, w, ev):
        return False


if __name__ == "__main__":
    import signal as _signal

    def _other_lino_pids():
        me = os.getpid()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            pid = int(pid)
            if pid == me:
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "ignore")
            except OSError:
                continue
            if "voice.py" in cmd:
                yield pid

    for pid in _other_lino_pids():
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass
    import time as _t
    _t.sleep(0.5)

    import fcntl
    _lock_fd = open(os.path.join("/tmp", "lino.lock"), "a+")
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Lino is already running (single-instance lock).")
        sys.exit(0)
    app = LinoVoice()
    Gtk.main()
