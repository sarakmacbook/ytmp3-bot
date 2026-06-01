#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Telegram Bot — One-Click Setup
# Usage: bash setup_ytmp3_bot.sh
# ══════════════════════════════════════════════════════════════

set -euo pipefail

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
echo "  YouTube MP3 Telegram Bot — Setup"
echo "============================================"
echo ""

# ─── Step 0: Bot Token ──────────────────────────────────────
echo "Get your bot token from @BotFather:"
echo "  1. Open t.me/BotFather"
echo "  2. Send /newbot"
echo "  3. Choose a name + username"
echo "  4. Copy the API token"
echo ""
read -p "Paste your Telegram Bot Token: " BOT_TOKEN

if [ -z "$BOT_TOKEN" ]; then
    error "Token cannot be empty."
    exit 1
fi

if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
    warn "Token format looks unusual."
    read -p "Continue anyway? (y/n): " CONT
    [ "$CONT" != "y" ] && exit 1
fi

info "Token received."

# ─── Step 1: Dependencies ──────────────────────────────────
info "Installing dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg curl

# ─── Step 2: Directory ──────────────────────────────────────
info "Creating $BOT_DIR ..."
mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

# ─── Step 3: Python venv ───────────────────────────────────
info "Setting up Python environment..."
$PYTHON -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet python-telegram-bot[job-queue] yt-dlp requests

# ─── Step 4: yt-dlp binary ─────────────────────────────────
info "Installing latest yt-dlp..."
curl -sL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp" \
  -o "$BOT_DIR/venv/bin/yt-dlp"
chmod +x "$BOT_DIR/venv/bin/yt-dlp"

# ─── Step 5: Download bot script from GitHub ────────────────
info "Downloading bot script from GitHub..."
curl -sL "$GH_RAW/ytmp3_bot.py" -o "$BOT_SCRIPT"
chmod +x "$BOT_SCRIPT"
info "Bot script downloaded: $BOT_SCRIPT"

# ─── Step 5b: Cookies check ─────────────────────────────────
if [ -f "$BOT_DIR/cookies.txt" ]; then
    info "Cookies file found — bot will use authentication"
else
    warn "No cookies.txt found — downloads may fail on flagged IPs"
    warn "Export cookies from your browser and place at $BOT_DIR/cookies.txt"
fi

# ─── Step 6: Systemd service ───────────────────────────────
info "Creating systemd service..."

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SRVEOF
[Unit]
Description=YouTube MP3 Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
Environment=COOKIE_FILE=${BOT_DIR}/cookies.txt
ExecStart=${BOT_DIR}/venv/bin/python ${BOT_SCRIPT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SRVEOF

# ─── Step 7: Start ─────────────────────────────────────────
info "Starting bot service..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

sleep 3

if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "============================================"
    echo "  SUCCESS — Bot is running!"
    echo "============================================"
    echo ""
    echo "  Open Telegram → Message your bot"
    echo "  Send /start to test"
    echo "  Send a YouTube URL to get MP3"
    echo ""
    echo "  Manage:"
    echo "    systemctl status $SERVICE_NAME"
    echo "    systemctl restart $SERVICE_NAME"
    echo "    journalctl -u $SERVICE_NAME -f"
    echo "============================================"
else
    error "Bot failed to start!"
    echo "Check logs: journalctl -u $SERVICE_NAME -n 50"
    systemctl status "$SERVICE_NAME" --no-pager 2>/dev/null || true
    exit 1
fi
