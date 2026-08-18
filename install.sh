#!/bin/bash
# ╔══════════════════════════════════════════════════════════╗
# ║           LINO VOICE ASSISTANT - INSTALLER              ║
# ║          Offline AI Voice Assistant for Linux            ║
# ╚══════════════════════════════════════════════════════════╝
set -e

# ── Colors ─────────────────────────────────────────────────
R='\033[1;31m'   # Red
G='\033[1;32m'   # Green
Y='\033[1;33m'   # Yellow
B='\033[1;34m'   # Blue
M='\033[1;35m'   # Magenta
C='\033[1;36m'   # Cyan
W='\033[1;37m'   # White
NC='\033[0m'     # No Color
BOLD='\033[1m'
DIM='\033[2m'

# ── Progress bar ───────────────────────────────────────────
progressbar() {
    local current=$1 total=$2 width=30
    local pct=$(( current * 100 / total ))
    local filled=$(( current * width / total ))
    local empty=$(( width - filled ))
    printf "\r  ${C}[${G}"
    printf '█%.0s' $(seq 1 $filled 2>/dev/null) || true
    printf "${DIM}"
    printf '░%.0s' $(seq 1 $empty 2>/dev/null) || true
    printf "${NC}${C}] ${W}%3d%%${NC}" "$pct"
}

spin() {
    local chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    while kill -0 "$1" 2>/dev/null; do
        printf "\r  ${C}${chars:i++%${#chars}:1}${NC} %s" "$2"
        sleep 0.1
    done
    printf "\r  ${G}✔${NC} %s\n" "$2"
}

# ── Banner ─────────────────────────────────────────────────
clear
echo -e "${M}  ╔═══════════════════════════════════════════╗${NC}"
echo -e "${M}  ║        ${W}★ Stephen's Studio${M} ★               ║${NC}"
echo -e "${M}  ╚═══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${C}"
cat << 'EOF'
    ██╗      ██████╗ ███╗   ██╗██████╗  ██████╗ ███╗   ██╗
    ██║     ██╔═══██╗████╗  ██║██╔══██╗██╔═══██╗████╗  ██║
    ██║     ██║   ██║██╔██╗ ██║██║  ██║██║   ██║██╔██╗ ██║
    ██║     ██║   ██║██║╚██╗██║██║  ██║██║   ██║██║╚██╗██║
    ███████╗╚██████╔╝██║ ╚████║██████╔╝╚██████╔╝██║ ╚████║
    ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝
EOF
echo -e "${NC}"
echo -e "${W}  ── Offline Voice Assistant for Linux ──${NC}"
echo ""

# ── Menu ───────────────────────────────────────────────────
echo -e "${B}[MENU]${NC} Choose an option:"
echo -e "  ${G}1)${NC} Full Install (recommended)"
echo -e "  ${Y}2)${NC} Install System Packages Only"
echo -e "  ${Y}3)${NC} Install Python Packages Only"
echo -e "  ${Y}4)${NC} Download Voice Models Only"
echo -e "  ${R}5)${NC} Uninstall"
echo ""
read -p "$(echo -e ${B}'Select [1-5]: '${NC})" CHOICE

case $CHOICE in
    5)
        echo -e "\n${R}╔═══════════════════════════════════════╗${NC}"
        echo -e "${R}║         UNINSTALL LINO                ║${NC}"
        echo -e "${R}╚═══════════════════════════════════════╝${NC}"
        read -p "$(echo -e ${R}'Are you sure? [y/N]: '${NC})" CONFIRM
        if [[ "$CONFIRM" == "y" || "$CONFIRM" == "Y" ]]; then
            echo -e "${Y}Removing voice models...${NC}"
            rm -rf model voices/*.onnx 2>/dev/null
            echo -e "${G}Done. System packages were not removed.${NC}"
        else
            echo -e "${C}Cancelled.${NC}"
        fi
        exit 0
        ;;
    2)
        INSTALL_SYSTEM=1; INSTALL_PYTHON=0; INSTALL_MODELS=0; INSTALL_OLLAMA=0
        ;;
    3)
        INSTALL_SYSTEM=0; INSTALL_PYTHON=1; INSTALL_MODELS=0; INSTALL_OLLAMA=0
        ;;
    4)
        INSTALL_SYSTEM=0; INSTALL_PYTHON=0; INSTALL_MODELS=1; INSTALL_OLLAMA=0
        ;;
    *)
        INSTALL_SYSTEM=1; INSTALL_PYTHON=1; INSTALL_MODELS=1; INSTALL_OLLAMA=1
        ;;
esac

# ── Helper functions ───────────────────────────────────────
step() { echo -e "\n${G}[$1/$TOTAL]${NC} ${W}$2${NC}"; }
ok()   { echo -e "  ${G}✔${NC} $1"; }
warn() { echo -e "  ${Y}⚠${NC} $1"; }
fail() { echo -e "  ${R}✖${NC} $1"; }

TOTAL=4
STEP=0

# ── Step 1: System packages ────────────────────────────────
if [[ $INSTALL_SYSTEM -eq 1 ]]; then
    STEP=$((STEP+1))
    step $STEP "Installing system packages..."
    echo -e "  ${DIM}This may require sudo${NC}"
    sudo apt-get update -qq 2>/dev/null
    for i in $(seq 1 10); do progressbar $i 10; sleep 0.1; done; echo ""
    sudo apt-get install -y -qq \
        python3 python3-pip python3-venv \
        python3-gi python3-cairo \
        gir1.2-gtk-3.0 gir1.2-gdkpixbuf-2.0 gir1.2-pango-1.0 \
        libcairo2 libxtst6 wmctrl x11-utils \
        pulseaudio alsa-utils ffmpeg xclip \
        xdotool 2>/dev/null || true
    for i in $(seq 1 10); do progressbar $((10+i)) 20; sleep 0.05; done; echo ""
    ok "System packages installed"
fi

# ── Step 2: Python packages ────────────────────────────────
if [[ $INSTALL_PYTHON -eq 1 ]]; then
    STEP=$((STEP+1))
    step $STEP "Installing Python packages..."
    for i in $(seq 1 5); do progressbar $i 10; sleep 0.1; done; echo ""
    pip3 install --user --break-system-packages -r requirements.txt 2>/dev/null || \
    pip3 install --break-system-packages -r requirements.txt 2>/dev/null || \
    pip3 install --user -r requirements.txt 2>/dev/null || \
    pip3 install -r requirements.txt 2>/dev/null || true
    for i in $(seq 1 5); do progressbar $((5+i)) 10; sleep 0.05; done; echo ""
    ok "Python packages installed"
fi

# ── Step 3: Voice models ───────────────────────────────────
if [[ $INSTALL_MODELS -eq 1 ]]; then
    STEP=$((STEP+1))
    step $STEP "Downloading Vosk speech model (~40MB)..."
    if [ ! -d "model" ]; then
        wget -q --show-progress "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" -O /tmp/vosk-model.zip 2>&1 | while read -r line; do
            echo -ne "  ${C}⬇ ${line}${NC}\r"
        done
        echo ""
        unzip -q /tmp/vosk-model.zip -d /tmp/
        mv /tmp/vosk-model-small-en-us-0.15 model
        rm /tmp/vosk-model.zip
        ok "Vosk model downloaded to model/"
    else
        warn "Vosk model already exists, skipping"
    fi
fi

# ── Step 4: Ollama & AI models ─────────────────────────────
if [[ $INSTALL_OLLAMA -eq 1 ]]; then
    STEP=$((STEP+1))
    step $STEP "Checking Ollama (AI chat backend)..."
    if command -v ollama &> /dev/null; then
        ok "Ollama found"
        echo -e "  ${DIM}Pulling Qwen models (may take a while)...${NC}"
        ollama pull qwen2.5:7b &
        PID=$!
        spin $PID "Downloading qwen2.5:7b..."
        wait $PID
        ollama pull qwen2.5vl:7b &
        PID=$!
        spin $PID "Downloading qwen2.5vl:7b..."
        wait $PID
    else
        warn "Ollama not found - install from https://ollama.com"
        echo -e "  ${DIM}AI chat features will be unavailable${NC}"
    fi
fi

# ── Done ───────────────────────────────────────────────────
echo ""
echo -e "${G}╔═══════════════════════════════════════╗${NC}"
echo -e "${G}║       INSTALLATION COMPLETE!          ║${NC}"
echo -e "${G}╚═══════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${W}Start Lino:${NC}"
echo -e "    ${C}python3 voice.py${NC}"
echo ""
echo -e "  ${W}Remote access:${NC}"
echo -e "    ${C}python3 server.py${NC}"
echo ""
echo -e "  ${DIM}Add your photo as 'bubble.jpg' for the avatar${NC}"
echo -e "  ${M}★ Stephen's Studio ★${NC}"
echo ""
