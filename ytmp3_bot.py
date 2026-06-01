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

BOT_TOKEN=os.environ.get("BOT_TOKEN", "")
DOWNLOAD_DIR = Path(tempfile.mkdtemp(prefix="ytmp3_"))
MAX_DURATION = 3600
AUDIO_QUALITY = "192"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


def download_and_convert(url, out_dir):
    """Download YouTube audio and convert to MP3 using yt-dlp with bot bypass."""
    tmpl = str(out_dir / "%(title)s.%(ext)s")

    # Clean any existing files first
    for f in out_dir.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass

    dl_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", tmpl,
        "--no-warnings",
        # YouTube bot bypass: use PO token / impersonation
        "--extractor-args", "youtube:player_client=android",
        "--extractor-args", "youtube:po_token=web+",
        url,
    ]
    log.info("Running yt-dlp for: %s", url)
    r = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=600)
    log.info("yt-dlp exit=%d stderr_len=%d", r.returncode, len(r.stderr))

    if r.returncode != 0:
        log.error("Download failed: %s", r.stderr[:500])
        # Retry with different approach
        log.info("Retrying with web player client...")
        dl_cmd2 = [
            sys.executable, "-m", "yt_dlp",
            "--no-playlist",
            "-f", "bestaudio/best",
            "-o", tmpl,
            "--no-warnings",
            "--extractor-args", "youtube:player_client=web",
            url,
        ]
        r = subprocess.run(dl_cmd2, capture_output=True, text=True, timeout=600)
        log.info("yt-dlp retry exit=%d", r.returncode)
        if r.returncode != 0:
            log.error("Retry also failed: %s", r.stderr[:500])
            return None, r.stderr[:300]

    # Find the downloaded file
    found = []
    for ext in ("m4a", "webm", "opus", "ogg", "aac", "flac", "wav", "mp3"):
        found.extend(out_dir.glob(f"*.{ext}"))

    if not found:
        all_files = list(out_dir.glob("*"))
        log.error("No audio files. All files: %s", [f.name for f in all_files])
        return None, "No audio file produced"

    src = found[0]
    mp3 = src.with_suffix(".mp3")

    ff_cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vn", "-ar", "44100", "-ac", "2",
        "-b:a", "%sk" % AUDIO_QUALITY,
        "-metadata", "encoder=YTMP3Bot",
        str(mp3),
    ]
    r2 = subprocess.run(ff_cmd, capture_output=True, text=True, timeout=300)
    if r2.returncode != 0:
        log.error("FFmpeg failed: %s", r2.stderr[:300])
        return None, "FFmpeg conversion failed"

    if src != mp3 and src.exists():
        src.unlink()

    return mp3, None


async def cmd_start(u, c):
    await u.message.reply_text(
        "YouTube MP3 Bot\n\n"
        "Send me any YouTube URL and I will send back the MP3!\n\n"
        "/start -- This message\n"
        "/help -- How to use"
    )


async def cmd_help(u, c):
    await u.message.reply_text(
        "How to use:\n\n"
        "1. Copy a YouTube video URL\n"
        "2. Paste it here\n"
        "3. Wait for the MP3 file\n\n"
        "Supports: youtube.com, youtu.be, shorts\n\n"
        "Max: %d min | Quality: %s kbps" % (MAX_DURATION // 60, AUDIO_QUALITY)
    )


async def handle_text(u, c):
    text = u.message.text.strip()

    patterns = [
        r'(https?://(?:www\.)?youtube\.com/watch\?\S+)',
        r'(https?://(?:www\.)?youtube\.com/shorts/\S+)',
        r'(https?://youtu\.be/\S+)',
        r'(https?://(?:www\.)?youtube\.com/embed/\S+)',
    ]
    url = None
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            url = m.group(1)
            break

    if not url:
        await u.message.reply_text("Send a YouTube URL.\nExample: https://www.youtube.com/watch?v=...")
        return

    log.info("Processing URL: %s", url)
    status = await u.message.reply_text("Downloading...")

    req_dir = Path(tempfile.mkdtemp(prefix="ytmp3_req_"))

    try:
        mp3, error_msg = download_and_convert(url, req_dir)
        if not mp3 or not mp3.exists():
            err = error_msg or "Unknown error"
            log.error("Failed: %s", err)
            await status.edit_text("Failed: %s" % err[:300])
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
        log.error("Timeout")
        await status.edit_text("Timed out. Video may be too long.")
    except Exception as e:
        log.error("Error: %s", e)
        await status.edit_text("Error: %s" % str(e)[:200])
    finally:
        for f in req_dir.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        try:
            req_dir.rmdir()
        except Exception:
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
