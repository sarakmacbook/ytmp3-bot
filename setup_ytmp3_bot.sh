#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Telegram Bot — One-Click Setup
# Usage: curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh | bash
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
echo "  YouTube MP3 Telegram Bot — Setup v4"
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
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg curl nodejs

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

# ─── Step 4: Download bot script from GitHub ────────────────
info "Downloading bot script from GitHub..."
curl -sL "$GH_RAW/ytmp3_bot.py" -o "$BOT_SCRIPT"
chmod +x "$BOT_SCRIPT"
info "Bot script downloaded: $BOT_SCRIPT"

# ─── Step 5: Systemd service ───────────────────────────────
info "Creating systemd service..."

# Write token to a secure env file
cat > "$BOT_DIR/.env" << EOF
BOT_TOKEN=$BOT_TOKEN
EOF
chmod 600 "$BOT_DIR/.env"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=YouTube MP3 Telegram Bot
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
EOF

# ─── Step 6: Start ─────────────────────────────────────────
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
