#!/bin/bash
echo "=== YTMP3 Bot Diagnostic ==="
echo ""

echo "--- System ---"
uname -a
echo ""

echo "--- Python ---"
python3 --version 2>&1
echo ""

echo "--- Bot directory ---"
ls -la /opt/ytmp3-bot/ 2>&1
echo ""

echo "--- Bot script ---"
head -15 /opt/ytmp3-bot/bot.py 2>&1
echo ""

echo "--- Cookies file ---"
ls -la /opt/ytmp3-bot/cookies.txt 2>&1
echo ""

echo "--- Systemd service ---"
systemctl status ytmp3-bot --no-pager 2>&1
echo ""

echo "--- Recent logs ---"
journalctl -u ytmp3-bot --no-pager -n 20 2>&1
echo ""

echo "--- yt-dlp test ---"
/opt/ytmp3-bot/venv/bin/python3 -m yt-dlp --version 2>&1
echo ""

echo "--- ffmpeg ---"
ffmpeg -version 2>&1 | head -1
echo ""

echo "--- Network ---"
curl -s -o /dev/null -w "YouTube: %{http_code}\n" https://www.youtube.com 2>&1
echo ""

echo "--- Telegram API ---"
curl -s "https://api.telegram.org/bot$(grep -oP 'BOT_TOKEN=\K[^ ]+' /etc/systemd/system/ytmp3-bot.service 2>/dev/null || echo 'NO_TOKEN')/getMe" 2>&1 | head -3
