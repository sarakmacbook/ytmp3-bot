#!/usr/bin/env python3
"""YouTube MP3 Telegram Bot v5 — async download, progress bar, cancel, thumbnail art."""

import os, sys, re, asyncio, logging, tempfile, traceback, base64, io
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from PIL import Image
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
AUDIO_QUALITY = "192"
MAX_DURATION = 3600
COOKIE_FILE = os.environ.get("COOKIE_FILE", "/opt/ytmp3-bot/cookies.txt")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Track active downloads per user ───────────────────────
active_downloads = {}  # user_id -> asyncio.subprocess.Process

# ─── Optional proxy ────────────────────────────────────────
PROXY = os.environ.get("SOCKS_PROXY", "")

# ─── Progress bar chars ────────────────────────────────────
BAR_FILL = "▓"
BAR_EMPTY = "░"
BAR_WIDTH = 16


def clean_url(url):
    """Remove tracking parameters from YouTube URLs."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=False)
    for trap in ("si", "feature", "gclid", "utm_source", "utm_medium", "utm_campaign"):
        params.pop(trap, None)
    clean_query = urlencode(params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))


def extract_video_id(url):
    """Extract YouTube video ID from URL."""
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be",):
        return parsed.path.lstrip("/")
    if parsed.hostname and "youtube" in parsed.hostname:
        qs = parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        m = re.match(r"/(?:shorts|embed)/([a-zA-Z0-9_-]+)", parsed.path)
        if m:
            return m.group(1)
    return None


def download_thumbnail(video_id):
    """Download YouTube thumbnail, return JPEG bytes or None."""
    urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and len(r.content) > 1000:
                img = Image.open(io.BytesIO(r.content))
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                log.info(f"Thumbnail from {url} ({len(buf.getvalue())} bytes)")
                return buf.getvalue()
        except Exception as e:
            log.debug(f"Thumbnail fail {url}: {e}")
    return None


def embed_album_art(mp3_path, thumbnail_bytes, title=""):
    """Embed thumbnail as album art in MP3 file."""
    try:
        audio = MP3(str(mp3_path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=thumbnail_bytes))
        if title:
            audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text="YouTube"))
        audio.save()
        log.info("Album art embedded")
    except Exception as e:
        log.warning(f"Could not embed art: {e}")


def build_ydl_cmd(url, out_dir):
    """Build yt-dlp command with cookie auth + JS runtime."""
    tmpl = str(out_dir / "%(title)s-%(id)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", tmpl,
        "--no-warnings",
        "--newline",          # progress on separate lines
        "-q", "--progress",   # progress bar to stderr
    ]
    if Path(COOKIE_FILE).exists():
        cmd.extend(["--cookies", COOKIE_FILE])
    if PROXY:
        cmd.extend(["--proxy", PROXY])
    cmd.extend(["--extractor-args", "youtube:player_client=web"])
    cmd.extend(["--js-runtimes", "node"])
    cmd.append(url)
    return cmd


def format_eta(seconds):
    """Format seconds to human readable ETA."""
    if not seconds or seconds <= 0:
        return "calculating"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}:{s:02d}"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def make_progress_bar(pct, speed_mbps, eta_sec):
    """Build progress bar string."""
    filled = int(BAR_WIDTH * pct / 100)
    bar = BAR_FILL * filled + BAR_EMPTY * (BAR_WIDTH - filled)
    eta_str = format_eta(eta_sec)
    speed_str = f"{speed_mbps:.1f}" if speed_mbps and speed_mbps > 0 else "?"
    return f"{bar} {pct:.0f}% • {speed_str} MB/s • {eta_str} left"


def parse_ydl_progress(line):
    """Parse a yt-dlp progress line. Returns (pct, speed_mbps, eta_sec) or None."""
    # yt-dlp progress looks like: [download]  45.3% of ~123.45MiB at 2.34MiB/s ETA 01:23
    m = re.search(
        r'\[download\]\s+(\d+\.?\d*)%\s+of\s+~?(\d+\.?\d*)([KMGT]i?B)\s+at\s+(\d+\.?\d*)([KMGT]i?B)/s\s+ETA\s+(\d+:\d+(?::\d+)?)',
        line
    )
    if m:
        pct = float(m.group(1))
        speed_val = float(m.group(4))
        speed_unit = m.group(5)
        # Convert to MB/s
        multiplier = {"B": 1/1048576, "KiB": 1/1024, "MiB": 1, "GiB": 1024,
                       "K": 1/1048576, "M": 1, "G": 1024, "T": 1048576}
        speed_mbps = speed_val * multiplier.get(speed_unit, 1)
        # Parse ETA
        eta_parts = m.group(6).split(":")
        eta_sec = sum(int(x) * 60**i for i, x in enumerate(reversed(eta_parts)))
        return pct, speed_mbps, eta_sec

    # Simpler fallback: just percentage
    m2 = re.search(r'\[download\]\s+(\d+\.?\d*)%', line)
    if m2:
        return float(m2.group(1)), None, None

    return None


async def async_download_and_convert(url, out_dir, user_id, status_msg, context):
    """
    Async download with live progress updates.
    Returns (mp3_path, error_string).
    Cancels cleanly if user presses cancel.
    """
    url = clean_url(url)
    log.info(f"Clean URL: {url}")

    for f in out_dir.glob("*"):
        try: f.unlink()
        except: pass

    cmd = build_ydl_cmd(url, out_dir)
    log.info(f"Starting async yt-dlp: {' '.join(cmd[:5])}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Track for cancel
        active_downloads[user_id] = proc

        # Read stderr (where yt-dlp writes progress) line by line
        last_update = asyncio.get_event_loop().time()
        progress_text = "⏳ Starting download..."

        while True:
            try:
                line_bytes = await asyncio.wait_for(proc.stderr.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                # Check if process finished
                if proc.returncode is not None:
                    break
                # Check if cancelled
                if user_id not in active_downloads:
                    return None, "Cancelled"
                continue

            if not line_bytes:
                if proc.returncode is not None:
                    break
                continue

            line = line_bytes.decode("utf-8", errors="replace").strip()
            parsed = parse_ydl_progress(line)

            if parsed:
                pct, speed, eta = parsed
                progress_text = make_progress_bar(pct, speed or 0, eta or 0)

                # Update Telegram every 2 seconds (rate limit friendly)
                now = asyncio.get_event_loop().time()
                if now - last_update >= 2.0:
                    last_update = now
                    try:
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                        ])
                        await status_msg.edit_text(progress_text, reply_markup=keyboard)
                    except Exception:
                        pass  # rate limit or message deleted

            # Check if cancelled between lines
            if user_id not in active_downloads:
                proc.kill()
                await proc.wait()
                return None, "Cancelled"

        # Process finished
        await proc.wait()

        if user_id not in active_downloads:
            return None, "Cancelled"

        if proc.returncode != 0:
            stderr_out = await proc.stderr.read()
            err_text = stderr_out.decode("utf-8", errors="replace")[:500]
            log.error(f"yt-dlp failed (exit {proc.returncode}): {err_text}")
            return None, err_text

        # Find downloaded file
        found = []
        for ext in ("m4a", "mp4", "webm", "opus", "ogg", "aac", "flac", "wav", "mp3"):
            found.extend(out_dir.glob(f"*.{ext}"))
        if not found:
            return None, "No audio file produced"

        src = found[0]
        mp3 = src.with_suffix(".mp3")

        # Convert to MP3 with ffmpeg
        try:
            await status_msg.edit_text("🔄 Converting to MP3...")
        except Exception:
            pass

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", str(src), "-vn", "-ar", "44100", "-ac", "2",
            "-b:a", f"{AUDIO_QUALITY}k", "-metadata", "encoder=YTMP3Bot", str(mp3)
        ]
        ff_proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        await ff_proc.wait()

        if ff_proc.returncode != 0:
            err = (await ff_proc.stderr.read()).decode("utf-8", errors="replace")[:200]
            return None, f"FFmpeg failed: {err}"

        if src != mp3 and src.exists():
            src.unlink()

        return mp3, None

    except asyncio.CancelledError:
        return None, "Cancelled"
    except Exception as e:
        log.error(f"Download error: {e}\n{traceback.format_exc()}")
        return None, str(e)[:300]
    finally:
        active_downloads.pop(user_id, None)


async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "🎵 YouTube MP3 Bot v5\n\n"
        "Send any YouTube URL → get MP3 back\n\n"
        "Commands: /start /help\n"
        "Note: Max 60 min video\n"
        "Progress bar + cancel button while downloading"
    )


async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📖 How to use:\n\n"
        "1️⃣ Copy YouTube URL\n"
        "2️⃣ Send it here\n"
        "3️⃣ Wait for MP3\n\n"
        "⏳ Progress bar shows % / speed / ETA\n"
        "❌ Tap Cancel to stop anytime\n"
        f"Quality: {AUDIO_QUALITY} kbps | Max: {MAX_DURATION//60} min"
    )


async def handle_cancel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button press — kills the async subprocess immediately."""
    user_id = u.callback_query.from_user.id
    query = u.callback_query
    await query.answer("Cancelling...")

    if user_id in active_downloads:
        proc = active_downloads.pop(user_id)
        try:
            proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5)
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
            await asyncio.wait_for(old_proc.wait(), timeout=3)
        except Exception:
            pass

    log.info(f"URL: {url} (user {user_id})")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    status = await u.message.reply_text("⏳ Starting...", reply_markup=keyboard)
    req_dir = Path(tempfile.mkdtemp(prefix="ytmp3_"))

    try:
        mp3, err = await async_download_and_convert(url, req_dir, user_id, status, c)

        # Remove cancel button
        try:
            await status.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if not mp3 or not mp3.exists():
            error_msg = err or "Unknown error"
            if error_msg == "Cancelled":
                # Already handled by cancel handler
                return
            await status.edit_text(f"❌ Failed:\n{error_msg[:400]}")
            return

        # ── Download & embed thumbnail ──
        video_id = extract_video_id(url)
        if video_id:
            thumb = download_thumbnail(video_id)
            if thumb:
                embed_album_art(mp3, thumb, mp3.stem)

        size_mb = mp3.stat().st_size / (1024 * 1024)
        await status.edit_text(f"✅ Uploading... ({size_mb:.1f} MB)")
        with open(mp3, "rb") as f:
            await u.message.reply_audio(
                audio=f, title=mp3.stem, performer="YouTube",
            )
        await status.delete()
        log.info(f"Sent: {mp3.name} ({size_mb:.1f} MB)")

    except Exception as e:
        log.error(f"Error: {e}\n{traceback.format_exc()}")
        try:
            await status.edit_text(f"❌ Error: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        active_downloads.pop(user_id, None)
        for f in req_dir.glob("*"):
            try: f.unlink()
            except: pass
        try: req_dir.rmdir()
        except: pass


async def cmd_update(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Pull latest bot from GitHub and restart."""
    allowed_users = os.environ.get("BOT_OWNER_ID", "")
    user_id = str(u.message.from_user.id)
    if allowed_users and user_id not in allowed_users.split(","):
        await u.message.reply_text("⛔ Not authorized.")
        return
    msg = await u.message.reply_text("🔄 Updating...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "bash", "/opt/ytmp3-bot/update.sh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0:
            await msg.edit_text("✅ Update complete! Bot restarted.")
        else:
            await msg.edit_text(f"❌ Update failed:\n{stderr.decode()[:300]}")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set!"); sys.exit(1)
    log.info("Starting YT-MP3 Bot v5...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CallbackQueryHandler(handle_cancel, pattern="^cancel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot running.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
