# YouTube MP3 Telegram Bot

One-click Telegram bot that converts YouTube videos to MP3.

## Quick Setup

```bash
curl -sL https://raw.githubusercontent.com/sarakmacbook/ytmp3-bot/main/setup_ytmp3_bot.sh -o setup.sh
bash setup.sh
```

The script will:
1. Prompt you for your Telegram bot token (from @BotFather)
2. Install dependencies (python3, ffmpeg, yt-dlp)
3. Set up the bot as a systemd service
4. Start immediately

## Bot Usage

1. Message your bot on Telegram
2. Send `/start`
3. Paste any YouTube URL
4. Get back the MP3 file

Supports: `youtube.com`, `youtu.be`, `youtube.com/shorts`

## Requirements

- Ubuntu/Debian VPS
- Root or sudo access
- ~500MB disk space
