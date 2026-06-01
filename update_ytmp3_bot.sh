#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Bot — Quick Update Script v2
# Run this on the VPS to pull the latest bot version from GitHub
# ══════════════════════════════════════════════════════════════

set -euo pipefail

BOT_DIR="/opt/ytmp3-bot"
GH_RAW="https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main"

echo "🔄 Updating YouTube MP3 Bot..."

# ── Step 1: Pull latest bot script ──
echo "📥 Downloading latest bot script..."
curl -sL "$GH_RAW/ytmp3_bot.py" -o "$BOT_DIR/bot.py"
chmod +x "$BOT_DIR/bot.py"
echo "✅ Bot script updated ($(wc -l < "$BOT_DIR/bot.py") lines)"

# ── Step 2: Install system deps for Pillow ──
echo "📦 Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq 2>&1 | tail -1
for pkg in libjpeg62-turbo zlib1g-dev libfreetype6-dev liblcms2-dev libopenjp2-7-dev libtiff5-dev libwebp-dev tcl8.6-dev tk8.6-dev; do
    dpkg -l "$pkg" 2>/dev/null | grep -q "^ii" || apt-get install -y -qq "$pkg" 2>&1 | tail -1
done
echo "✅ System deps installed"

# ── Step 3: Install Python deps ──
echo "🐍 Installing Python dependencies..."
source "$BOT_DIR/venv/bin/activate"
pip install --quiet Pillow mutagen 2>&1 | tail -3
echo "✅ Python deps installed"

# ── Step 4: Verify imports ──
echo "🔍 Verifying imports..."
if "$BOT_DIR/venv/bin/python3" -c "import requests; from PIL import Image; from mutagen.mp3 import MP3; print('All imports OK')" 2>&1; then
    echo "✅ All imports verified"
else
    echo "❌ Import check failed!"
    "$BOT_DIR/venv/bin/python3" -c "import requests; from PIL import Image; from mutagen.mp3 import MP3" 2>&1
    exit 1
fi

# ── Step 5: Restart service ──
echo "🔄 Restarting bot service..."
systemctl restart ytmp3-bot
sleep 3

if systemctl is-active --quiet ytmp3-bot; then
    echo ""
    echo "============================================"
    echo "  ✅ Update complete! Bot is running."
    echo "============================================"
else
    echo ""
    echo "============================================"
    echo "  ❌ Bot failed to start after update!"
    echo "============================================"
    echo ""
    echo "Error logs:"
    journalctl -u ytmp3-bot -n 30 --no-pager
    echo ""
    echo "Try running manually to debug:"
    echo "  $BOT_DIR/venv/bin/python3 $BOT_DIR/bot.py"
    exit 1
fi
