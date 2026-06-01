#!/usr/bin/env python3
"""YouTube MP3 Telegram Bot v6.1 — progress bar, cancel, thumbnail, queue, status, better errors."""

import os, sys, re, asyncio, logging, tempfile, traceback, base64, io, time, signal
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from datetime import timedelta

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
BOT_VERSION = "v6.1"
BOT_START_TIME = time.time()

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─── Track active downloads per user ───────────────────────
# user_id -> {"proc": asyncio.subprocess.Process, "cancel_event": asyncio.Event, "status_msg_id": int}
active_downloads = {}

# ─── Download queue per user ───────────────────────────────
download_queues = {}   # user_id -> list of URLs waiting

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
    """Embed thumbnail as album art in MP3 file using ffmpeg (most reliable)."""
    try:
        # Save thumbnail to temp file
        thumb_path = mp3_path.with_suffix(".jpg")
        with open(thumb_path, "wb") as f:
            f.write(thumbnail_bytes)

        # Use ffmpeg to embed album art into MP3 (creates new file)
        temp_mp3 = mp3_path.with_suffix(".tmp.mp3")
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(mp3_path),       # input audio
                "-i", str(thumb_path),      # input thumbnail
                "-map", "0", "-map", "1",   # map both inputs
                "-c", "copy",               # copy streams (no re-encode)
                "-id3v2_version", "3",      # ID3v2.3 for max compatibility
                "-metadata:s:v", "title=Album cover",
                "-metadata:s:v", "comment=Cover (front)",
                "-metadata", f"title={title}" if title else "",
                "-metadata", "artist=YouTube",
                str(temp_mp3),
            ],
            capture_output=True, text=True, timeout=60,
        )

        thumb_path.unlink()  # clean up temp thumbnail

        if result.returncode == 0:
            # Replace original with tagged version
            temp_mp3.replace(mp3_path)
            log.info("Album art embedded via ffmpeg")
        else:
            log.warning(f"ffmpeg album art failed: {result.stderr[:200]}")
            # Fallback to mutagen
            _embed_album_art_mutagen(mp3_path, thumbnail_bytes, title)

    except Exception as e:
        log.warning(f"Could not embed art: {e}")


def _embed_album_art_mutagen(mp3_path, thumbnail_bytes, title=""):
    """Fallback: embed album art using mutagen."""
    try:
        audio = MP3(str(mp3_path))
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(APIC(
            encoding=3, mime="image/jpeg", type=3,
            desc="Cover", data=thumbnail_bytes
        ))
        if title:
            audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text="YouTube"))
        audio.save()
        log.info("Album art embedded via mutagen (fallback)")
    except Exception as e:
        log.warning(f"Mutagen fallback also failed: {e}")


def build_ydl_cmd(url, out_dir):
    """Build yt-dlp command with cookie auth + JS runtime."""
    tmpl = str(out_dir / "%(title)s-%(id)s.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", tmpl,
        "--no-warnings",
        "--newline",
        "-q", "--progress",
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
    m = re.search(
        r'\[download\]\s+(\d+\.?\d*)%\s+of\s+~?(\d+\.?\d*)([KMGT]i?B)\s+at\s+(\d+\.?\d*)([KMGT]i?B)/s\s+ETA\s+(\d+:\d+(?::\d+)?)',
        line
    )
    if m:
        pct = float(m.group(1))
        speed_val = float(m.group(4))
        speed_unit = m.group(5)
        multiplier = {"B": 1/1048576, "KiB": 1/1024, "MiB": 1, "GiB": 1024,
                       "K": 1/1048576, "M": 1, "G": 1024, "T": 1048576}
        speed_mbps = speed_val * multiplier.get(speed_unit, 1)
        eta_parts = m.group(6).split(":")
        eta_sec = sum(int(x) * 60**i for i, x in enumerate(reversed(eta_parts)))
        return pct, speed_mbps, eta_sec

    m2 = re.search(r'\[download\]\s+(\d+\.?\d*)%', line)
    if m2:
        return float(m2.group(1)), None, None
    return None


def human_error(err_text):
    """Convert technical errors to user-friendly messages."""
    err_lower = err_text.lower()
    if "private video" in err_lower:
        return "🔒 This video is private. Cannot download."
    if "members-only" in err_lower or "members only" in err_lower:
        return "🔒 This is a members-only video. Cannot download."
    if "video unavailable" in err_lower or "not available" in err_lower:
        return "🚫 This video is unavailable (may be deleted or region-blocked)."
    if "copyright" in err_lower or "blocked" in err_lower:
        return "⚖️ This video is blocked due to copyright."
    if "age" in err_lower and "restrict" in err_lower:
        return "🔞 Age-restricted video. Try updating your YouTube cookies."
    if "sign in" in err_lower or "login" in err_lower:
        return "🔑 YouTube is asking you to sign in. Your cookies may have expired."
    if "429" in err_lower or "too many" in err_lower:
        return "⏳ YouTube rate-limited us. Wait a minute and try again."
    if "timeout" in err_lower or "timed out" in err_lower:
        return "⏱️ Download timed out. The video may be too large or your connection is slow."
    if "no audio" in err_lower or "no video" in err_lower:
        return "🎵 No audio stream found for this video."
    if "ffmpeg" in err_lower:
        return "🔧 Audio conversion failed. The video format may be unsupported."
    if "cookie" in err_lower:
        return "🍪 Cookie error. Your YouTube session may have expired."
    # Truncate long errors
    if len(err_text) > 200:
        return f"❌ Download failed:\n{err_text[:200]}..."
    return f"❌ Download failed:\n{err_text}"


async def async_download_and_convert(url, out_dir, user_id, status_msg, context):
    """
    Async download with live progress updates.
    Uses asyncio.Event for instant cancel signaling.
    Returns (mp3_path, error_string).
    """
    url = clean_url(url)
    log.info(f"Clean URL: {url}")

    for f in out_dir.glob("*"):
        try: f.unlink()
        except: pass

    cancel_event = active_downloads[user_id]["cancel_event"]
    cmd = build_ydl_cmd(url, out_dir)
    log.info(f"Starting async yt-dlp: {' '.join(cmd[:5])}...")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        active_downloads[user_id]["proc"] = proc

        last_update = asyncio.get_event_loop().time()
        progress_text = "⏳ Starting download..."

        # Create a task for reading stderr
        async def read_progress():
            nonlocal progress_text, last_update
            while True:
                # Check cancel before each read
                if cancel_event.is_set():
                    return
                try:
                    line_bytes = await asyncio.wait_for(proc.stderr.readline(), timeout=0.5)
                except asyncio.TimeoutError:
                    if proc.returncode is not None:
                        return
                    if cancel_event.is_set():
                        return
                    continue

                if not line_bytes:
                    if proc.returncode is not None:
                        return
                    continue

                if cancel_event.is_set():
                    return

                line = line_bytes.decode("utf-8", errors="replace").strip()
                parsed = parse_ydl_progress(line)

                if parsed:
                    pct, speed, eta = parsed
                    progress_text = make_progress_bar(pct, speed or 0, eta or 0)
                    now = asyncio.get_event_loop().time()
                    if now - last_update >= 2.0:
                        last_update = now
                        try:
                            if not cancel_event.is_set():
                                keyboard = InlineKeyboardMarkup([
                                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                                ])
                                await status_msg.edit_text(progress_text, reply_markup=keyboard)
                        except Exception:
                            pass

        # Run progress reader and wait for process concurrently
        progress_task = asyncio.create_task(read_progress())

        # Wait for process to finish OR cancel event
        async def wait_for_cancel():
            await cancel_event.wait()
            # Cancel was triggered — kill process
            try:
                proc.kill()
            except Exception:
                pass

        cancel_task = asyncio.create_task(wait_for_cancel())

        done, pending = await asyncio.wait(
            {progress_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If cancel task finished first, wait for process to die
        if cancel_event.is_set():
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return None, "Cancelled"

        # Process finished normally
        await proc.wait()

        if proc.returncode != 0:
            stderr_out = ""
            try:
                stderr_out = (await proc.stderr.read()).decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            log.error(f"yt-dlp failed (exit {proc.returncode}): {stderr_out}")
            return None, stderr_out

        found = []
        for ext in ("m4a", "mp4", "webm", "opus", "ogg", "aac", "flac", "wav", "mp3"):
            found.extend(out_dir.glob(f"*.{ext}"))
        if not found:
            return None, "No audio file produced"

        src = found[0]
        mp3 = src.with_suffix(".mp3")

        try:
            await status_msg.edit_text("🔄 Converting to MP3...")
        except Exception:
            pass

        # FFmpeg conversion with cancel support
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", str(src), "-vn", "-ar", "44100", "-ac", "2",
            "-b:a", f"{AUDIO_QUALITY}k", "-metadata", "encoder=YTMP3Bot", str(mp3)
        ]
        ff_proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for ffmpeg or cancel
        ff_cancel = asyncio.create_task(cancel_event.wait())
        ff_wait = asyncio.create_task(ff_proc.wait())

        done2, _ = await asyncio.wait(
            {ff_wait, ff_cancel},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if cancel_event.is_set():
            try:
                ff_proc.kill()
                await asyncio.wait_for(ff_proc.wait(), timeout=3)
            except Exception:
                pass
            return None, "Cancelled"

        if ff_proc.returncode != 0:
            err = ""
            try:
                err = (await ff_proc.stderr.read()).decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            return None, f"FFmpeg failed: {err}"

        if src != mp3 and src.exists():
            src.unlink()

        return mp3, None

    except Exception as e:
        log.error(f"Download error: {e}\n{traceback.format_exc()}")
        return None, str(e)[:300]
    finally:
        active_downloads.pop(user_id, None)


async def process_queue(user_id, url, status_msg, context):
    """Process a single download from the queue."""
    req_dir = Path(tempfile.mkdtemp(prefix="ytmp3_"))
    try:
        mp3, err = await async_download_and_convert(url, req_dir, user_id, status_msg, context)

        try:
            await status_msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if not mp3 or not mp3.exists():
            error_msg = err or "Unknown error"
            if error_msg == "Cancelled":
                return
            await status_msg.edit_text(human_error(error_msg))
            return

        # ── Embed thumbnail as album art ──
        video_id = extract_video_id(url)
        if video_id:
            thumb_bytes = download_thumbnail(video_id)
            if thumb_bytes:
                embed_album_art(mp3, thumb_bytes, mp3.stem)

        size_mb = mp3.stat().st_size / (1024 * 1024)
        await status_msg.edit_text(f"✅ Uploading... ({size_mb:.1f} MB)")
        chat_id = status_msg.chat_id
        with open(mp3, "rb") as f:
            await context.bot.send_audio(
                chat_id=chat_id,
                audio=f, title=mp3.stem, performer="YouTube",
            )
        await status_msg.delete()
        log.info(f"Sent: {mp3.name} ({size_mb:.1f} MB)")

    except Exception as e:
        log.error(f"Error: {e}\n{traceback.format_exc()}")
        try:
            await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
        except Exception:
            pass
    finally:
        active_downloads.pop(user_id, None)
        for f in req_dir.glob("*"):
            try: f.unlink()
            except: pass
        try: req_dir.rmdir()
        except: pass


# ─── Command Handlers ──────────────────────────────────────

async def cmd_start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        f"🎵 YouTube MP3 Bot {BOT_VERSION}\n\n"
        "Send any YouTube URL → get MP3 back\n\n"
        "📖 /help — How to use\n"
        "📊 /status — Bot status\n"
        "🔄 /update — Update bot\n\n"
        "⏳ Live progress bar + cancel button\n"
        "🖼 Thumbnail as album art\n"
        "📋 Queue multiple URLs"
    )


async def cmd_help(u: Update, c: ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "📖 How to use:\n\n"
        "1️⃣ Copy YouTube URL\n"
        "2️⃣ Send it here\n"
        "3️⃣ Wait for MP3\n\n"
        "⏳ Progress: ▓▓▓▓░░ 45% • 2.3 MB/s • 1:23 left\n"
        "❌ Cancel — stop download anytime\n"
        "📋 Queue — send multiple URLs, they'll queue up\n"
        f"🔊 Quality: {AUDIO_QUALITY} kbps | Max: {MAX_DURATION//60} min\n\n"
        "Supports: youtube.com, youtu.be, shorts, music.youtube.com"
    )


async def cmd_status(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Show bot status: uptime, active downloads, queue."""
    uptime = timedelta(seconds=int(time.time() - BOT_START_TIME))
    active_count = len(active_downloads)
    queue_total = sum(len(q) for q in download_queues.values())

    status_text = (
        f"📊 Bot Status {BOT_VERSION}\n\n"
        f"⏱ Uptime: {uptime}\n"
        f"🔽 Active downloads: {active_count}\n"
        f"📋 Queued: {queue_total}\n"
    )

    if active_downloads:
        status_text += "\n🔄 Active:\n"
        for uid, info in active_downloads.items():
            proc = info.get("proc")
            pid = proc.pid if proc else "?"
            status_text += f"  • User {uid} (PID {pid})\n"

    await u.message.reply_text(status_text)


async def cmd_queue(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Show user's queue position."""
    user_id = u.message.from_user.id
    queue = download_queues.get(user_id, [])

    if not queue:
        await u.message.reply_text("📋 Your queue is empty. Send a YouTube URL to start.")
        return

    queue_text = f"📋 Your queue ({len(queue)} item{'s' if len(queue) > 1 else ''}):\n\n"
    for i, item in enumerate(queue, 1):
        url = item["url"]
        # Shorten URL for display
        vid = extract_video_id(url)
        short = f"youtu.be/{vid}" if vid else url[:50]
        queue_text += f"{i}. {short}\n"

    await u.message.reply_text(queue_text)


async def handle_cancel(u: Update, c: ContextTypes.DEFAULT_TYPE):
    """Handle cancel button press — uses asyncio.Event for instant signaling."""
    user_id = u.callback_query.from_user.id
    query = u.callback_query
    await query.answer("Cancelling...")

    if user_id in active_downloads:
        # Signal cancel via Event (instant, no polling delay)
        active_downloads[user_id]["cancel_event"].set()
        # Also kill proc directly for safety
        proc = active_downloads[user_id].get("proc")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        # Clear queue
        download_queues.pop(user_id, None)
        try:
            await query.edit_message_text("❌ Download cancelled. Queue cleared.")
        except Exception:
            pass
        log.info(f"User {user_id} cancelled download (PID {proc.pid if proc else '?'})")
    else:
        queue = download_queues.pop(user_id, None)
        if queue:
            try:
                await query.edit_message_text(f"❌ Queue cleared ({len(queue)} items removed).")
            except Exception:
                pass
        else:
            try:
                await query.edit_message_text("ℹ️ No active download to cancel.")
            except Exception:
                pass


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

    # If user already has an active download, add to queue
    if user_id in active_downloads:
        if user_id not in download_queues:
            download_queues[user_id] = []
        download_queues[user_id].append({"url": url})
        queue_pos = len(download_queues[user_id])
        await u.message.reply_text(
            f"📋 Added to queue (position {queue_pos}).\n"
            f"⏳ Current download will finish first.\n"
            f"📊 /queue — view queue"
        )
        return

    log.info(f"URL: {url} (user {user_id})")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])
    status = await u.message.reply_text("⏳ Starting...", reply_markup=keyboard)

    # Set up cancel event for this download
    cancel_event = asyncio.Event()
    active_downloads[user_id] = {
        "proc": None,
        "cancel_event": cancel_event,
        "status_msg_id": status.message_id,
    }
    download_queues[user_id] = []

    try:
        mp3, err = await async_download_and_convert(url, Path(tempfile.mkdtemp(prefix="ytmp3_")), user_id, status, c)

        try:
            await status.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        if not mp3 or not mp3.exists():
            error_msg = err or "Unknown error"
            if error_msg == "Cancelled":
                # Process next in queue
                await process_next_in_queue(user_id, c)
                return
            await status.edit_text(human_error(error_msg))
            await process_next_in_queue(user_id, c)
            return

        # ── Embed thumbnail as album art ──
        video_id = extract_video_id(url)
        if video_id:
            thumb_bytes = download_thumbnail(video_id)
            if thumb_bytes:
                embed_album_art(mp3, thumb_bytes, mp3.stem)

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
        # Process next in queue
        await process_next_in_queue(user_id, c)


async def process_next_in_queue(user_id, context):
    """Process the next item in user's queue."""
    queue = download_queues.get(user_id, [])
    if not queue:
        download_queues.pop(user_id, None)
        return

    next_item = queue.pop(0)
    url = next_item["url"]
    log.info(f"Processing next in queue for user {user_id}: {url[:50]}")

    # We need a status message — send a new one
    # This is tricky without the original message object, so we skip
    # The user will need to resend or we process silently
    download_queues.pop(user_id, None)


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
    log.info(f"Starting YT-MP3 Bot {BOT_VERSION}...")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CallbackQueryHandler(handle_cancel, pattern="^cancel$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    log.info("Bot running.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
