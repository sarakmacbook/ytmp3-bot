#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Bot — Quick Update Script
# Run this on the VPS to pull the latest bot version from GitHub
# ══════════════════════════════════════════════════════════════

set -euo pipefail

BOT_DIR="/opt/ytmp3-bot"
GH_RAW="https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main"

echo "🔄 Updating YouTube MP3 Bot..."

# Pull latest bot script
curl -sL "$GH_RAW/ytmp3_bot.py" -o "$BOT_DIR/bot.py"
chmod +x "$BOT_DIR/bot.py"

# Install any new dependencies
source "$BOT_DIR/venv/bin/activate"
pip install --quiet Pillow mutagen 2>/dev/null || true

# Restart service
systemctl restart ytmp3-bot
sleep 2

if systemctl is-active --quiet ytmp3-bot; then
    echo "✅ Update complete! Bot is running."
else
    echo "❌ Bot failed to start after update!"
    journalctl -u ytmp3-bot -n 20 --no-pager
fi
