#!/usr/bin/env python3
"""YouTube MP3 Telegram Bot v4 — with cancel button support."""

import os, sys, re, subprocess, logging, tempfile, traceback, base64
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
AUDIO_QUALITY = "192"
MAX_DURATION = 3600
COOKIE_FILE = os.environ.get("COOKIE_FILE", "/opt/ytmp3-bot/cookies.txt")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Track active downloads per user ───────────────────────
active_downloads = {}  # user_id -> subprocess.Popen

# ─── Optional proxy ────────────────────────────────────────
PROXY = os.environ.get("SOCKS_PROXY", "")


def clean_url(url):
    """Remove tracking parameters from YouTube URLs."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=False)
    for trap in ("si", "feature", "gclid", "utm_source", "utm_medium", "utm_campaign"):
        params.pop(trap, None)
    clean_query = urlencode(params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))


def build_ydl_opts(url, out_dir):
    """Build yt-dlp options with cookie auth + JS runtime."""
    tmpl = str(out_dir / "%(title)s-%(id)s.%(ext)s")
    opts = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", tmpl,
        "--no-warnings",
        "--quiet",
    ]
    if Path(COOKIE_FILE).exists():
        opts.extend(["--cookies", COOKIE_FILE])
        log.info(f"Using cookies: {COOKIE_FILE}")
    else:
        log.warning("No cookie file found, trying without auth")
    if PROXY:
        opts.extend(["--proxy", PROXY])
    opts.extend(["--extractor-args", "youtube:player_client=web"])
    opts.extend(["--js-runtimes", "node"])
    opts.append(url)
    return opts


def download_and_convert(url, out_dir):
    url = clean_url(url)
    log.info(f"Clean URL: {url}")
    for f in out_dir.glob("*"):
        try: f.unlink()
        except: pass
    cmd = build_ydl_opts(url, out_dir)
    log.info(f"Running yt-dlp...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    log.info(f"yt-dlp exit={r.returncode}")
    if r.returncode != 0:
        log.error(f"Download failed: {r.stderr[:500]}")
        return None, r.stderr[:400]
    found = []
    for ext in ("m4a", "mp4", "webm", "opus", "ogg", "aac", "flac", "wav", "mp3"):
        found.extend(out_dir.glob(f"*.{ext}"))
    if not found:
        return None, "No audio file produced"
    src = found[0]
    mp3 = src.with_suffix(".mp3")
    r2 = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-vn", "-ar", "44100", "-ac", "2",
         "-b:a", f"{AUDIO_QUALITY}k", "-metadata", "encoder=YTMP3Bot", str(mp3)],
        capture_output=True, text=True, timeout=300,
    )
    if r2.returncode != 0:
        return None, f"FFmpeg failed: {r2.stderr[:200]}"
    if src != mp3 and src.exists():
        src.unlink()
    return mp3, None


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "🎵 YouTube MP3 Bot v4\n\n"
        "Send any YouTube URL → get MP3 back\n\n"
        "Commands: /start /help\n"
        "Note: Max 60 min video\n"
        "You can cancel with ❌ button while downloading"
    )

async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📖 How to use:\n\n"
        "1️⃣ Copy YouTube URL\n"
        "2️⃣ Send it here\n"
        "3️⃣ Wait for MP3\n\n"
        "⏳ While downloading, tap ❌ Cancel to stop\n"
        f"Quality: {AUDIO_QUALITY} kbps | Max: {MAX_DURATION//60} min"
    )

async def handle_cancel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button press."""
    user_id = u.callback_query.from_user.id
    query = u.callback_query
    await query.answer()

    if user_id in active_downloads:
        proc = active_downloads.pop(user_id)
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
        await query.edit_message_text("❌ Download cancelled.")
        log.info(f"User {user_id} cancelled download (PID {proc.pid})")
    else:
        await query.edit_message_text("ℹ️ No active download to cancel.")

async def handle_text(u: Update, c: ContextTypes.DEFAULT_TYPE):
    text = u.message.text.strip()
    patterns = [
        r'(https?://(?:www\.)?youtube\.com/watch\?\S+)',
        r'(https?://(?:www\.)?youtube\.com/shorts/\S+)',
        r'(https?://youtu\.be/\S+)',
        r'(https?://(?:www\.)?youtube\.com/embed/\S+)',
        r'(https?://music\.youtube\.com/watch\?\S+)',
    ]
    url = None
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            url = m.group(1)
            break
    if not url:
        await u.message.reply_text("⚠️ Send a YouTube URL.\nExample: https://youtube.com/watch?v=...")
        return

    user_id = u.message.from_user.id

    # Cancel any existing download for this user
    if user_id in active_downloads:
        old_proc = active_downloads.pop(user_id)
        try:
            old_proc.kill()
            old_proc.wait(timeout=3)
        except Exception:
            pass

    log.info(f"URL: {url} (user {user_id})")

    # Show "Downloading..." message with cancel button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    status = await u.message.reply_text("⏳ Downloading...", reply_markup=keyboard)
    req_dir = Path(tempfile.mkdtemp(prefix="ytmp3_"))

    try:
        # Remove cancel button after a short delay (if download is fast)
        mp3, err = download_and_convert(url, req_dir)

        # Remove cancel button
        try:
            await status.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if not mp3 or not mp3.exists():
            await status.edit_text(f"❌ Failed:\n{err[:400]}")
            return

        size_mb = mp3.stat().st_size / (1024 * 1024)
        await status.edit_text(f"✅ Uploading... ({size_mb:.1f} MB)")
        with open(mp3, "rb") as f:
            await u.message.reply_audio(
                audio=f, title=mp3.stem,
                caption="🎵 YT-MP3", performer="YouTube",
            )
        await status.delete()
        log.info(f"Sent: {mp3.name} ({size_mb:.1f} MB)")

    except subprocess.TimeoutExpired:
        await status.edit_text("❌ Timed out.")
    except Exception as e:
        log.error(f"Error: {e}\n{traceback.format_exc()}")
        await status.edit_text(f"❌ Error: {str(e)[:200]}")
    finally:
        # Clean up active download tracking
        active_downloads.pop(user_id, None)
        for f in req_dir.glob("*"):
            try: f.unlink()
            except: pass
        try: req_dir.rmdir()
        except: pass


def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set!"); sys.exit(1)
    log.info("Starting YT-MP3 Bot v4...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_cancel, pattern="^cancel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot running.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
