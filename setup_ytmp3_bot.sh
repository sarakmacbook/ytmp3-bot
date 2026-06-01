#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Telegram Bot v4 — One-Click Setup
# 
# Usage:
#   curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh | bash
#
# Or step by step:
#   wget https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh
#   bash setup_ytmp3_bot.sh
# ══════════════════════════════════════════════════════════════

set -euo pipefail

# Allow token as first argument: bash setup.sh BOT_TOKEN_HERE
TOKEN_FROM_ARG="${1:-}"

BOT_DIR="/opt/ytmp3-bot"
SERVICE_NAME="ytmp3-bot"
PYTHON="/usr/bin/python3"
BOT_SCRIPT="$BOT_DIR/bot.py"
GH_RAW="https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

echo ""
echo "============================================"
echo "  YouTube MP3 Telegram Bot v4 — Setup"
echo "============================================"
echo ""

# ─── Step 0: Bot Token ──────────────────────────────────────

# Check if token already exists from previous install
EXISTING_TOKEN=""
if [ -f "$BOT_DIR/.env" ]; then
    EXISTING_TOKEN=$(grep -oP 'BOT_TOKEN=\K.*' "$BOT_DIR/.env" 2>/dev/null || true)
fi

# Priority: 1) command-line arg, 2) existing .env, 3) interactive prompt
if [ -n "$TOKEN_FROM_ARG" ]; then
    BOT_TOKEN="$TOKEN_FROM_ARG"
    info "Using token from command-line argument."
elif [ -n "$EXISTING_TOKEN" ]; then
    info "Existing bot token found: ${EXISTING_TOKEN:0:10}..."
    read -p "Use existing token? (y/n): " USE_EXISTING </dev/tty
    if [ "$USE_EXISTING" = "y" ] || [ "$USE_EXISTING" = "Y" ]; then
        BOT_TOKEN="$EXISTING_TOKEN"
        info "Reusing existing token."
    else
        read -p "Paste new Telegram Bot Token: " BOT_TOKEN </dev/tty
    fi
else
    echo "Get your bot token from @BotFather:"
    echo "  1. Open t.me/BotFather"
    echo "  2. Send /newbot"
    echo "  3. Choose a name + username"
    echo "  4. Copy the API token"
    echo ""
    read -p "Paste your Telegram Bot Token: " BOT_TOKEN </dev/tty
fi

if [ -z "$BOT_TOKEN" ]; then
    error "Token cannot be empty."
    exit 1
fi

if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
    warn "Token format looks unusual."
    read -p "Continue anyway? (y/n): " CONT </dev/tty
    [ "$CONT" != "y" ] && exit 1
fi

info "Token OK: ${BOT_TOKEN:0:10}..."

# ─── Step 1: Dependencies ──────────────────────────────────
info "Installing dependencies..."
export DEBIAN_FRONTEND=noninteractive

# Fix potential broken repos
sed -i 's/http:/https:/g' /etc/apt/sources.list 2>/dev/null || true

apt-get update -qq 2>&1 | tail -3

# Install packages one by one to handle individual failures
for pkg in python3 python3-pip python3-venv ffmpeg curl nodejs; do
    dpkg -l "$pkg" 2>/dev/null | grep -q "^ii" && info "$pkg already installed" || apt-get install -y -qq "$pkg" 2>&1 | tail -2
done

# Ensure node is available
if ! command -v node &>/dev/null; then
    info "Installing Node.js via nodesource..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
    apt-get install -y -qq nodejs
fi

info "Node.js version: $(node --version)"

# ─── Step 2: Directory ──────────────────────────────────────
info "Setting up $BOT_DIR ..."
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

# ─── Step 3: Python venv ───────────────────────────────────
info "Setting up Python environment..."
if [ -d "venv" ]; then
    info "Virtual environment already exists, updating..."
else
    $PYTHON -m venv venv
fi
source venv/bin/activate

# Upgrade yt-dlp to latest nightly for best YouTube compatibility
pip install --quiet --upgrade pip
pip install --quiet python-telegram-bot[job-queue] requests Pillow mutagen
pip install --quiet --upgrade yt-dlp

info "yt-dlp version: $(python3 -m yt_dlp --version)"

# ─── Step 4: Download bot script from GitHub ────────────────
info "Downloading bot script from GitHub..."
curl -sL "$GH_RAW/ytmp3_bot.py" -o "$BOT_SCRIPT"
chmod +x "$BOT_SCRIPT"
info "Bot script: ($(wc -l < "$BOT_SCRIPT") lines)"

# ─── Step 5: Token & Service ───────────────────────────────
info "Configuring bot service..."

# Stop existing service first
systemctl stop "$SERVICE_NAME" 2>/dev/null || true

# Kill any leftover bot processes
pkill -f "ytmp3-bot/bot.py" 2>/dev/null || true
sleep 1

# Write token to secure env file
printf 'BOT_TOKEN=%s\n' "$BOT_TOKEN" > "$BOT_DIR/.env"
chmod 600 "$BOT_DIR/.env"
info "Token saved to $BOT_DIR/.env"

# Create systemd service
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOFSVC
[Unit]
Description=YouTube MP3 Telegram Bot v4
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/venv/bin/python $BOT_SCRIPT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOFSVC

# ─── Step 6: Start ─────────────────────────────────────────
info "Starting bot service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

sleep 4

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "============================================"
    echo "  ✅ SUCCESS — Bot is running!"
    echo "============================================"
    echo ""
    echo "  📱 Open Telegram → Message your bot"
    echo "  🎵 Send any YouTube URL → Get MP3"
    echo ""
    echo "  Commands: /start /help"
    echo ""
    echo "  Manage:"
    echo "    systemctl status $SERVICE_NAME"
    echo "    systemctl restart $SERVICE_NAME"
    echo "    journalctl -u $SERVICE_NAME -f"
    echo ""
    echo "  Re-install / Update:"
    echo "    curl -sL $GH_RAW/setup_ytmp3_bot.sh | bash"
    echo "============================================"
else
    error "Bot failed to start!"
    echo ""
    echo "Check logs:"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager
    exit 1
fi
