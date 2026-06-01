# YouTube MP3 Telegram Bot v4

A Telegram bot that converts YouTube videos to MP3 audio files.

## Quick Install

### Method 1: One-liner with token
```bash
curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh -o setup.sh
bash setup.sh YOUR_BOT_TOKEN
```

### Method 2: Interactive (download first, then run)
```bash
curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh -o setup.sh
bash setup.sh
```
Then follow the prompts to enter your bot token.

### Method 3: Re-install / Update (auto-detects existing token)
```bash
curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh -o setup.sh
bash setup.sh
```
If a token already exists, it will ask if you want to reuse it.

## Requirements

- Ubuntu 22.04+ / Debian 12+
- Telegram Bot Token (from @BotFather)
- Node.js (installed automatically for YouTube signature solving)

## Features

- 🎵 YouTube URL → MP3 conversion
- 🍪 Cookie-based authentication (bypasses IP blocks)
- 🔧 Auto-installs Node.js for YouTube signature solving
- 🔄 Systemd service with auto-restart
- 📦 Self-contained bot script (no external dependencies)
- 🔑 Token stored securely in `.env` file

## How It Works

1. Send any YouTube URL to your bot
2. Bot downloads audio using yt-dlp with cookie auth
3. Converts to MP3 using ffmpeg
4. Sends the MP3 file back to you

## Management

```bash
# Check status
systemctl status ytmp3-bot

# Restart
systemctl restart ytmp3-bot

# View logs
journalctl -u ytmp3-bot -f

# Stop
systemctl stop ytmp3-bot
```

## File Structure

```
/opt/ytmp3-bot/
├── bot.py          # Main bot script
├── .env            # Bot token (secure, chmod 600)
├── venv/           # Python virtual environment
└── cookies.txt     # YouTube cookies (auto-generated)
```

## Troubleshooting

**"Requested format is not available"**
→ Update yt-dlp: `/opt/ytmp3-bot/venv/bin/pip install -U yt-dlp`

**"Sign in to confirm you're not a bot"**
→ Export fresh cookies from your browser and replace `/opt/ytmp3-bot/cookies.txt`

**Bot not responding**
→ Check logs: `journalctl -u ytmp3-bot -n 50 --no-pager`
