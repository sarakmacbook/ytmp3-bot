# YouTube MP3 Telegram Bot

A Telegram bot that converts YouTube videos to MP3 audio files.

## One-Click Install

```bash
curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh | bash
```

## Requirements

- Ubuntu 22.04+ VPS
- Telegram Bot Token (from @BotFather)
- Node.js (installed automatically)

## Features

- YouTube URL → MP3 conversion
- Cookie-based authentication (bypasses IP blocks)
- Automatic retry and error handling
- Systemd service for auto-restart

## Manual Setup

```bash
# Clone or download
git clone https://github.com/sarakmacbook/ytmp3-bot.git
cd ytmp3-bot

# Install dependencies
sudo apt install python3 python3-venv ffmpeg curl nodejs
python3 -m venv venv
source venv/bin/activate
pip install python-telegram-bot[job-queue] yt-dlp requests

# Set token and run
export BOT_TOKEN=your_token_here
python ytmp3_bot.py
```

## Management

```bash
systemctl status ytmp3-bot
systemctl restart ytmp3-bot
journalctl -u ytmp3-bot -f
```
