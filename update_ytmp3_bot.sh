#!/bin/bash
# ══════════════════════════════════════════════════════════════
# YouTube MP3 Bot — Quick Update Script v3
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
# Ubuntu 24.04 uses libjpeg-turbo8 (not libjpeg62-turbo)
for pkg in libjpeg-turbo8 libjpeg-turbo8-dev zlib1g-dev libfreetype6-dev liblcms2-dev libopenjp2-7-dev libtiff5-dev libwebp-dev; do
    dpkg -l "$pkg" 2>/dev/null | grep -q "^ii" || apt-get install -y -qq "$pkg" 2>&1 | tail -1 || echo "⚠️  Skipped: $pkg"
done
echo "✅ System deps done"

# ── Step 3: Install Python deps ──
echo "🐍 Installing Python dependencies..."
source "$BOT_DIR/venv/bin/activate"
pip install --quiet Pillow eyed3 mutagen 2>&1 | tail -3
echo "✅ Python deps installed"

# ── Step 4: Verify imports ──
echo "🔍 Verifying imports..."
if "$BOT_DIR/venv/bin/python3" -c "import requests; from PIL import Image; from mutagen.mp3 import MP3; print('All imports OK')" 2>&1; then
    echo "✅ All imports verified"
else
    echo "❌ Import check failed — trying fresh install..."
    pip install --force-reinstall --quiet Pillow mutagen 2>&1 | tail -3
    "$BOT_DIR/venv/bin/python3" -c "import requests; from PIL import Image; from mutagen.mp3 import MP3; print('All imports OK')" 2>&1 || { echo "❌ Still failing"; exit 1; }
fi

# ── Step 5: Clear pycache ──
rm -rf "$BOT_DIR/__pycache__" "$BOT_DIR"/**/__pycache__ 2>/dev/null || true

# ── Step 6: Restart service ──
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
