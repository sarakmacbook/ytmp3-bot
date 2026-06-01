#!/usr/bin/env python3
"""YouTube MP3 Telegram Bot — Send a YouTube URL, get back MP3."""

import os
import sys
import re
import subprocess
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="ytmp3_"))
MAX_DURATION = 3600
AUDIO_QUALITY = "192"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def download_and_convert(url: str, out_dir: Path):
    tmpl = str(out_dir / "%(title)s.%(ext)s")
    r = subprocess.run(
        [sys.executable, "-m", "yt_dlp",
         "--no-playlist", "-f", "bestaudio/best",
         "-o", tmpl, "--no-warnings", "--quiet", url],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        log.error("Download failed: %s", r.stderr[:300])
        return None

    found = []
    for ext in ("m4a", "webm", "opus", "ogg", "aac", "flac", "wav", "mp3"):
        found.extend(out_dir.glob(f"*.{ext}"))
    if not found:
        log.error("No audio files found")
        return None

    src = found[0]
    mp3 = src.with_suffix(".mp3")

    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src),
         "-vn", "-ar", "44100", "-ac", "2",
         "-b:a", "%sk" % AUDIO_QUALITY,
         "-metadata", "encoder=YTMP3Bot", str(mp3)],
        capture_output=True, text=True, timeout=300,
    )
    if r2.returncode != 0:
        log.error("FFmpeg failed: %s", r2.stderr[:300])
        return src if src.suffix == ".mp3" else None

    if src != mp3 and src.exists():
        src.unlink()
    return mp3


def cleanup(d: Path):
    for f in d.glob("*"):
        try: f.unlink()
        except: pass


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "YouTube MP3 Bot\n\n"
        "Send me any YouTube URL and I'll send back the MP3!\n\n"
        "/start — This message\n"
        "/help — How to use"
    )


async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "How to use:\n\n"
        "1. Copy a YouTube video URL\n"
        "2. Paste it here\n"
        "3. Wait for the MP3 file\n\n"
        "Supports: youtube.com, youtu.be, shorts\n\n"
        "Max: %d min | Quality: %s kbps" % (MAX_DURATION // 60, AUDIO_QUALITY)
    )


async def handle_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    text = u.message.text.strip()
    m = re.search(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+)', text)
    if not m:
        await u.message.reply_text("Send a YouTube URL.\nExample: https://youtube.com/watch?v=...")
        return
    url = m.group(1)

    status = await u.message.reply_text("Downloading...")
    req_dir = DOWNLOAD_DIR / ("req_%d" % u.message.message_id)
    req_dir.mkdir(exist_ok=True)

    try:
        mp3 = download_and_convert(url, req_dir)
        if not mp3 or not mp3.exists():
            await status.edit_text("Failed. Check URL and try again.")
            cleanup(req_dir)
            return

        size_mb = mp3.stat().st_size / (1024 * 1024)
        await status.edit_text("Uploading... (%.1f MB)" % size_mb)

        with open(mp3, "rb") as f:
            await u.message.reply_audio(
                audio=f,
                title=mp3.stem,
                caption="YouTube MP3 Bot",
                performer="YouTube",
            )
        await status.delete()
        log.info("Sent: %s (%.1f MB)", mp3.name, size_mb)

    except subprocess.TimeoutExpired:
        await status.edit_text("Timed out.")
    except Exception as e:
        log.error("Error: %s", e)
        await status.edit_text("Error: %s" % str(e)[:200])
    finally:
        cleanup(req_dir)
        try:
            req_dir.rmdir()
        except:
            pass


def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN env var not set!")
        sys.exit(1)
    log.info("Starting YouTube MP3 Bot...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot running. Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
