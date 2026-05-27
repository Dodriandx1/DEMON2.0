import os
import sys
import time
import asyncio
import subprocess
import psutil
import gc
import yt_dlp
import glob
import http.server
import socketserver
import threading
import re
import json
from datetime import datetime, timedelta
from pathlib import Path

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, InputMediaVideo
)
from pyrogram.errors import MessageNotModified, FloodWait

try:
    import pymongo
    MONGO_AVAILABLE = True
except ImportError:
    MONGO_AVAILABLE = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
API_ID    = int(os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MONGO_URI = os.environ.get("MONGO_URI", "")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

DOWNLOAD_DIR = "/tmp/downloads/"
os.makedirs(DOWNLOAD_DIR, mode=0o777, exist_ok=True)

BOT_CREDITS = "✪ Bot By → @The_canst & @Ryota_YT"

# ─── KEEP-ALIVE ───────────────────────────────────────────────────────────────
def keep_alive():
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Running")
        def log_message(self, *a): pass
    port = int(os.environ.get("PORT", 8000))
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

# ─── STATE ────────────────────────────────────────────────────────────────────
active_tasks: dict   = {}   # task_id -> {"process": ..., "cancelled": bool}
last_updates: dict   = {}   # msg_id  -> float (last edit timestamp)
bot_start_time       = datetime.now()

# ─── DATABASE ─────────────────────────────────────────────────────────────────
db_connected = False
users_col    = None
if MONGO_AVAILABLE and MONGO_URI:
    try:
        db_client  = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        users_col  = db_client["bot_mediafire"]["authorized_users"]
        db_client.server_info()
        db_connected = True
    except Exception:
        pass

# ─── UTILITIES ────────────────────────────────────────────────────────────────

def is_authorized(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    if db_connected and users_col is not None:
        try:
            return users_col.find_one({"user_id": user_id}) is not None
        except Exception:
            return False
    return True  # open if no DB configured

def get_readable_size(size) -> str:
    if size is None:
        return "0B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return "0B"

def get_readable_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "∞"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"

def build_bar(pct: float, width: int = 13) -> str:
    filled    = int(pct / 100 * width)
    cursor    = min(filled, width - 1)
    bar_chars = []
    for i in range(width):
        if i < filled - 1:
            bar_chars.append("⬢")
        elif i == cursor:
            bar_chars.append("◉")
        else:
            bar_chars.append("◌")
    return "[" + "".join(bar_chars) + "]"

def fmt_download_bar(user_name: str, pct: float, done_parts: int, total_parts: int,
                     speed: str, eta: str, past: str, task_id: str) -> str:
    bar = build_bar(pct)
    return (
        f"╭ Task By → 「{user_name}」\n"
        f"┊ {bar} {pct:.2f}%\n"
        f"┊ Status   : Download\n"
        f"┊ Done     : {done_parts} / {total_parts} partes\n"
        f"┊ Total    : {total_parts} partes\n"
        f"┊ Speed    : {speed}\n"
        f"┊ ETA      : {eta}\n"
        f"┊ Past     : {past}\n"
        f"┊ Engine   : CRDWV2\n"
        f"╰ Mode     : #CRDW\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_CREDITS}"
    )

def fmt_encode_bar(user_name: str, pct: float, done_kb: str, total_kb: str,
                   fps: str, eta: str, past: str, task_id: str) -> str:
    bar = build_bar(pct)
    return (
        f"╭ Task By → 「{user_name}」\n"
        f"┊ {bar} {pct:.2f}%\n"
        f"┊ Status   : Encoding\n"
        f"┊ Done     : {done_kb}\n"
        f"┊ Total    : {total_kb}\n"
        f"┊ Speed    : {fps}\n"
        f"┊ ETA      : {eta}\n"
        f"┊ Past     : {past}\n"
        f"┊ Engine   : FFmpeg\n"
        f"╰ Mode     : #FFENC\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_CREDITS}"
    )

def fmt_upload_bar(user_name: str, pct: float, done: str, total: str,
                   speed: str, eta: str, past: str, task_id: str) -> str:
    bar = build_bar(pct)
    return (
        f"╭ Task By → 「{user_name}」\n"
        f"┊ {bar} {pct:.2f}%\n"
        f"┊ Status   : Upload\n"
        f"┊ Done     : {done}\n"
        f"┊ Total    : {total}\n"
        f"┊ Speed    : {speed}\n"
        f"┊ ETA      : {eta}\n"
        f"┊ Past     : {past}\n"
        f"┊ Engine   : Pyrogram\n"
        f"╰ Mode     : #TLGUP\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_CREDITS}"
    )

async def safe_edit(msg, text: str, reply_markup=None):
    now = time.time()
    if now - last_updates.get(msg.id, 0) < 2.0:
        return
    last_updates[msg.id] = now
    try:
        await msg.edit_text(text, reply_markup=reply_markup)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await asyncio.sleep(e.value)
    except Exception:
        pass

def extract_thumbnail(video_path: str) -> str | None:
    thumb = video_path + ".jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-ss", "00:00:02",
             "-vframes", "1", thumb, "-y"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        return thumb
    except Exception:
        return None

def get_video_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", path],
            stderr=subprocess.DEVNULL
        )
        info = json.loads(out)
        return float(info["format"].get("duration", 0))
    except Exception:
        return 0

# ─── BOT CLIENT ───────────────────────────────────────────────────────────────
bot = Client(
    "ryota_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=16
)

# ─── /start ───────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    await message.reply_text(
        "**🎬 Bot Descargador de Videos**\n\n"
        "Envíame un enlace de:\n"
        "• YouTube, TikTok, Instagram, Twitter/X\n"
        "• Facebook, Mega.nz y más\n\n"
        "**Comandos:**\n"
        "`/stat` — Estadísticas del bot\n"
        "`/reset` — Reiniciar estadísticas\n"
        "`/encode` — Extraer video de torrent (.mkv/.avi→mp4)\n"
        "`/cancel` — Cancelar descarga activa\n\n"
        "**Marcas de agua:**\n"
        "Envía un video y recibirás opciones de marca de agua\n\n"
        "**Subtítulos:**\n"
        "Añade `-lat` al enlace para subtítulos en español\n\n"
        f"{BOT_CREDITS}",
        parse_mode=enums.ParseMode.MARKDOWN
    )

# ─── /stat ────────────────────────────────────────────────────────────────────
@bot.on_message(filters.command(["stat", "Stat"]))
async def cmd_stat(client: Client, message: Message):
    uptime   = datetime.now() - bot_start_time
    ram      = psutil.virtual_memory()
    disk     = psutil.disk_usage("/tmp")
    cpu_pct  = psutil.cpu_percent(interval=0.5)
    platform = sys.platform
    try:
        server_name = os.uname().nodename
    except Exception:
        server_name = "Unknown"
    try:
        cpu_name = subprocess.check_output(
            ["sh", "-c", "cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d: -f2"],
            text=True
        ).strip() or "Unknown CPU"
    except Exception:
        cpu_name = "Unknown CPU"

    h, rem = divmod(int(uptime.total_seconds()), 3600)
    m, s   = divmod(rem, 60)
    up_str = f"{h}h {m}m {s}s"

    text = (
        f"╭─ Status Panel\n"
        f"┊ 🕐 Time on  : {up_str}\n"
        f"┊ 🧠 RAM      : {get_readable_size(ram.used)} / {get_readable_size(ram.total)}\n"
        f"┊ 💾 Storage  : {get_readable_size(disk.used)} / {get_readable_size(disk.total)}\n"
        f"┊ 🖥️ Server   : {server_name}\n"
        f"┊ ⚙️ Platform : {platform}\n"
        f"┊ 🔧 CPU      : {cpu_pct}% — {cpu_name}\n"
        f"╰─ Engine    : CRDWV2 + FFmpeg\n\n"
        f"{BOT_CREDITS}"
    )
    await message.reply_text(text)

# ─── /reset ───────────────────────────────────────────────────────────────────
@bot.on_message(filters.command(["reset", "Reset"]))
async def cmd_reset(client: Client, message: Message):
    if not is_authorized(message.from_user.id):
        await message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    global bot_start_time
    # cancel all active tasks
    cancelled_count = 0
    for tid, task_data in list(active_tasks.items()):
        task_data["cancelled"] = True
        proc = task_data.get("process")
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        cancelled_count += 1
    active_tasks.clear()
    gc.collect()

    freed = "Nada que limpiar"
    if cancelled_count:
        freed = f"{cancelled_count} tarea(s) canceladas"

    # clean temp dir
    for f in glob.glob(DOWNLOAD_DIR + "*"):
        try:
            os.remove(f)
        except Exception:
            pass

    bot_start_time = datetime.now()

    text = (
        f"╭─「 Reset Completado ✅ 」\n"
        f"┊ 🔄 Estado    : Online\n"
        f"┊ 🕐 Uptime    : 0s\n"
        f"┊ 🧹 Liberado  : {freed}\n"
        f"┊ ⛔ Descargas : Canceladas\n"
        f"╰─ Engine     : CRDWV2\n\n"
        f"{BOT_CREDITS}"
    )
    await message.reply_text(text)

# ─── /cancel ──────────────────────────────────────────────────────────────────
@bot.on_message(filters.regex(r"^/cancel(?:_(\d+))?$"))
async def cmd_cancel(client: Client, message: Message):
    text = message.text.strip()
    match = re.match(r"^/cancel_?(\d+)?$", text)
    task_id_str = match.group(1) if match and match.group(1) else None

    cancelled = False
    if task_id_str:
        task_data = active_tasks.get(task_id_str)
        if task_data:
            task_data["cancelled"] = True
            proc = task_data.get("process")
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
            active_tasks.pop(task_id_str, None)
            cancelled = True
    else:
        # cancel the most recent task of this user
        user_id = str(message.from_user.id)
        for tid, tdata in list(active_tasks.items()):
            if tdata.get("user_id") == user_id:
                tdata["cancelled"] = True
                proc = tdata.get("process")
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                active_tasks.pop(tid, None)
                cancelled = True
                break

    if cancelled:
        await message.reply_text("✅ Descarga cancelada correctamente.")
    else:
        await message.reply_text("⚠️ No hay ninguna descarga activa para cancelar.")

# ─── MEGA DOWNLOAD ────────────────────────────────────────────────────────────
async def download_mega(url: str, dest_dir: str, task_id: str,
                        prog_msg: Message, user_name: str) -> str | None:
    """Download from Mega.nz using megatools or yt-dlp fallback."""
    start = time.time()
    out_path = None

    # Try megatools first
    try:
        proc = subprocess.Popen(
            ["megadl", "--path", dest_dir, url],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        active_tasks[task_id]["process"] = proc

        downloaded = 0
        total_size = 1
        last_text = ""
        for line in proc.stdout:
            if active_tasks.get(task_id, {}).get("cancelled"):
                proc.kill()
                return None
            # parse: "Downloaded X/Y (Z%)"
            m = re.search(r"(\d+)/(\d+)", line)
            if m:
                downloaded = int(m.group(1))
                total_size = int(m.group(2))
            pct = (downloaded / total_size * 100) if total_size else 0
            elapsed = time.time() - start
            speed_bps = downloaded / elapsed if elapsed > 0 else 0
            eta = (total_size - downloaded) / speed_bps if speed_bps > 0 else 0
            new_text = fmt_download_bar(
                user_name, pct,
                int(downloaded / 1024 / 1024 * 10), int(total_size / 1024 / 1024 * 10),
                get_readable_size(speed_bps) + "/s",
                get_readable_time(eta), get_readable_time(elapsed), task_id
            )
            if new_text != last_text:
                await safe_edit(prog_msg, new_text)
                last_text = new_text

        proc.wait()
        files = glob.glob(dest_dir + "*")
        if files:
            out_path = max(files, key=os.path.getmtime)
        return out_path
    except FileNotFoundError:
        pass  # megatools not found, try yt-dlp

    # yt-dlp fallback (some mega links work)
    return await download_ytdlp(url, dest_dir, task_id, prog_msg, user_name)

# ─── YT-DLP DOWNLOAD ──────────────────────────────────────────────────────────
async def download_ytdlp(url: str, dest_dir: str, task_id: str,
                         prog_msg: Message, user_name: str,
                         subtitle_lang: str | None = None) -> str | None:
    start     = time.time()
    out_file  = [None]
    title_ref = [None]
    total_ref = [1]
    done_ref  = [0]
    last_text = [""]

    def progress_hook(d):
        if active_tasks.get(task_id, {}).get("cancelled"):
            raise yt_dlp.utils.DownloadError("Cancelled by user")

        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes", 0) or 0
            total      = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            speed      = d.get("speed") or 0
            eta_s      = d.get("eta") or 0
            pct        = (downloaded / total * 100) if total else 0
            elapsed    = time.time() - start

            done_ref[0]  = downloaded
            total_ref[0] = total

            done_parts  = int(downloaded / 1024 / 1024 * 10)
            total_parts = max(int(total / 1024 / 1024 * 10), 1)

            new_text = fmt_download_bar(
                user_name, pct, done_parts, total_parts,
                get_readable_size(speed) + "/s",
                get_readable_time(eta_s), get_readable_time(elapsed), task_id
            )
            if new_text != last_text[0]:
                asyncio.get_event_loop().call_soon_threadsafe(
                    lambda t=new_text: asyncio.ensure_future(safe_edit(prog_msg, t))
                )
                last_text[0] = new_text

        elif d["status"] == "finished":
            out_file[0] = d.get("filename")

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": dest_dir + "%(title)s.%(ext)s",
        "progress_hooks": [progress_hook],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [],
    }

    if subtitle_lang:
        ydl_opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": [subtitle_lang, "es", "es-419"],
            "subtitlesformat": "srt",
            "postprocessors": [{"key": "FFmpegEmbedSubtitle"}],
        })

    loop    = asyncio.get_event_loop()
    result  = [None]
    ex_ref  = [None]
    info_ref = [None]

    def run_download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    title_ref[0] = info.get("title", "Video")
                    # find output file
                    if out_file[0] is None:
                        out_file[0] = ydl.prepare_filename(info)
        except Exception as e:
            ex_ref[0] = e

    await loop.run_in_executor(None, run_download)

    if ex_ref[0]:
        return None

    path = out_file[0]
    if path and not os.path.exists(path):
        # try finding it
        candidates = glob.glob(dest_dir + "*.mp4")
        if candidates:
            path = max(candidates, key=os.path.getmtime)

    return path

# ─── TIKTOK CAROUSEL ─────────────────────────────────────────────────────────
async def handle_tiktok_carousel(url: str, message: Message, user_name: str):
    dest = DOWNLOAD_DIR + f"tt_{message.id}/"
    os.makedirs(dest, exist_ok=True)

    ydl_opts = {
        "outtmpl": dest + "%(autonumber)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
    }

    loop = asyncio.get_event_loop()
    photos = []

    def run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info and info.get("_type") == "playlist":
                for entry in info.get("entries", []):
                    fname = ydl.prepare_filename(entry)
                    if os.path.exists(fname):
                        photos.append(fname)

    await loop.run_in_executor(None, run)

    if photos:
        # send as media group if images, else videos
        media = []
        for p in photos[:10]:
            media.append(InputMediaVideo(p) if p.endswith((".mp4", ".mov")) else p)
        await message.reply_text(f"📸 Carrusel TikTok — {len(photos)} archivos recibidos de {user_name}")
        for p in photos:
            try:
                if p.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    await message.reply_photo(p)
                else:
                    thumb = extract_thumbnail(p)
                    await message.reply_video(p, thumb=thumb)
            except Exception:
                pass
    else:
        await message.reply_text("❌ No se pudo descargar el carrusel.")

    # cleanup
    for f in glob.glob(dest + "*"):
        try: os.remove(f)
        except: pass
    try: os.rmdir(dest)
    except: pass

# ─── ENCODE (FFmpeg fast) ─────────────────────────────────────────────────────
async def encode_video(input_path: str, output_path: str, task_id: str,
                       prog_msg: Message, user_name: str) -> bool:
    """Re-encode with FFmpeg using fast CRF settings."""
    start = time.time()
    duration = get_video_duration(input_path)
    last_text = [""]

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        output_path
    ]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    active_tasks[task_id]["process"] = proc

    frame_ref    = [0]
    fps_ref      = [0.0]
    out_size_ref = [0]

    for line in proc.stdout:
        if active_tasks.get(task_id, {}).get("cancelled"):
            proc.kill()
            return False

        line = line.strip()
        if line.startswith("frame="):
            try: frame_ref[0] = int(line.split("=")[1])
            except: pass
        elif line.startswith("fps="):
            try: fps_ref[0] = float(line.split("=")[1])
            except: pass
        elif line.startswith("total_size="):
            try: out_size_ref[0] = int(line.split("=")[1])
            except: pass
        elif line.startswith("out_time_us="):
            try:
                elapsed_us = int(line.split("=")[1])
                elapsed    = time.time() - start
                if duration > 0:
                    pct = min((elapsed_us / 1e6) / duration * 100, 99.9)
                else:
                    pct = 0

                fps_disp    = f"{fps_ref[0]:.2f} fps"
                size_done   = get_readable_size(out_size_ref[0])
                total_est   = get_readable_size(
                    int(out_size_ref[0] / pct * 100) if pct > 0 else 0
                )
                eta_s = ((100 - pct) / pct) * elapsed if pct > 0 else 0

                new_text = fmt_encode_bar(
                    user_name, pct, size_done, total_est,
                    fps_disp, get_readable_time(eta_s),
                    get_readable_time(elapsed), task_id
                )
                if new_text != last_text[0]:
                    await safe_edit(prog_msg, new_text)
                    last_text[0] = new_text
            except Exception:
                pass

    proc.wait()
    return proc.returncode == 0

# ─── WATERMARK ────────────────────────────────────────────────────────────────
WATERMARK_SESSIONS: dict = {}  # user_id -> {state, video_path, original_caption}

POS_MAP = {
    "↖ Arriba Izq": "10:10",
    "↗ Arriba Der": "main_w-overlay_w-10:10",
    "↙ Abajo Izq":  "10:main_h-overlay_h-10",
    "↘ Abajo Der":  "main_w-overlay_w-10:main_h-overlay_h-10",
    "⬛ Centro":    "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
}

def watermark_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💧 Añadir Marca de Agua", callback_data="wm_start"),
         InlineKeyboardButton("📤 Enviar Sin Marca",      callback_data="wm_skip")],
    ])

def wm_pos_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖ Arriba Izq",  callback_data="wm_pos_0"),
         InlineKeyboardButton("↗ Arriba Der",   callback_data="wm_pos_1")],
        [InlineKeyboardButton("↙ Abajo Izq",   callback_data="wm_pos_2"),
         InlineKeyboardButton("↘ Abajo Der",    callback_data="wm_pos_3")],
        [InlineKeyboardButton("⬛ Centro",       callback_data="wm_pos_4")],
    ])

def wm_size_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("25%",  callback_data="wm_sz_25"),
         InlineKeyboardButton("50%",  callback_data="wm_sz_50"),
         InlineKeyboardButton("75%",  callback_data="wm_sz_75"),
         InlineKeyboardButton("100%", callback_data="wm_sz_100")],
    ])

def wm_border_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Con Contorno",  callback_data="wm_border_yes"),
         InlineKeyboardButton("❌ Sin Contorno",  callback_data="wm_border_no")],
    ])

@bot.on_callback_query(filters.regex(r"^wm_"))
async def wm_callback(client: Client, query: CallbackQuery):
    uid  = query.from_user.id
    data = query.data
    sess = WATERMARK_SESSIONS.get(uid, {})

    if data == "wm_skip":
        WATERMARK_SESSIONS.pop(uid, None)
        await query.message.delete()
        await query.answer("📤 Enviando sin marca de agua...")
        # upload original video
        video_path = sess.get("video_path")
        caption    = sess.get("caption", "")
        if video_path and os.path.exists(video_path):
            await upload_video_to_chat(client, query.message.chat.id,
                                       video_path, caption, uid)
        return

    if data == "wm_start":
        WATERMARK_SESSIONS[uid] = {**sess, "state": "ask_text"}
        await query.message.edit_text(
            "✏️ Escribe el **texto** de la marca de agua:",
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await query.answer()
        return

    if data.startswith("wm_pos_"):
        pos_idx = int(data.split("_")[-1])
        pos_keys = list(POS_MAP.keys())
        WATERMARK_SESSIONS[uid] = {**sess, "position": pos_keys[pos_idx], "state": "ask_size"}
        await query.message.edit_text(
            f"📐 Posición: **{pos_keys[pos_idx]}**\n\nElige el **tamaño** de la letra:",
            reply_markup=wm_size_keyboard(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await query.answer()
        return

    if data.startswith("wm_sz_"):
        size = int(data.split("_")[-1])
        WATERMARK_SESSIONS[uid] = {**sess, "font_size_pct": size, "state": "ask_border"}
        await query.message.edit_text(
            f"📐 Tamaño: **{size}%**\n\n¿Con o sin **contorno**?",
            reply_markup=wm_border_keyboard(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        await query.answer()
        return

    if data.startswith("wm_border_"):
        border = data == "wm_border_yes"
        WATERMARK_SESSIONS[uid] = {**sess, "border": border, "state": "processing"}
        pos_label = sess.get("position", "↗ Arriba Der")
        size_pct  = sess.get("font_size_pct", 50)
        wm_text   = sess.get("wm_text", "Watermark")
        caption   = sess.get("caption", "")
        video_path = sess.get("video_path")

        border_str = "Con Contorno ✅" if border else "Sin Contorno ❌"
        preview_text = (
            f"╭─「 💧 Marca de Agua 」\n"
            f"┊ {caption[:40] if caption else 'Video'}\n"
            f"┊ Texto     : {wm_text}\n"
            f"┊ Posición  : {pos_label}\n"
            f"┊ Contorno  : {border_str}\n"
            f"┊ Tamaño    : {size_pct}%\n"
            f"┊\n"
            f"┊ ⚙️ Procesando...\n"
            f"┊ ⏳ Tiempo restante: calculando...\n"
            f"╰──────────────────────────\n\n"
            f"{BOT_CREDITS}"
        )
        prog_msg = await query.message.edit_text(preview_text)
        await query.answer()

        if video_path and os.path.exists(video_path):
            wm_out = video_path.replace(".mp4", "_wm.mp4")
            await apply_watermark(
                video_path, wm_out, wm_text, pos_label, size_pct, border,
                prog_msg, caption, uid
            )
            async def upload_task():
                await upload_video_to_chat(client, query.message.chat.id,
                                           wm_out, caption, uid)
                WATERMARK_SESSIONS.pop(uid, None)
                try: await prog_msg.delete()
                except: pass
            asyncio.ensure_future(upload_task())
        return

async def apply_watermark(video_path: str, out_path: str, text: str,
                          pos_label: str, size_pct: int, border: bool,
                          prog_msg: Message, caption: str, uid: int):
    duration = get_video_duration(video_path)
    pos = POS_MAP.get(pos_label, "main_w-overlay_w-10:10")

    # font size relative to video height (~36 pt at 100%)
    font_size = max(12, int(36 * size_pct / 100))

    border_filter = f":borderw=3:bordercolor=black" if border else ""
    vf = (
        f"drawtext=text='{text}':x={pos.split(':')[0]}:y={pos.split(':')[1]}"
        f":fontsize={font_size}:fontcolor=white{border_filter}"
    )

    start = time.time()
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        out_path
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    last_text = [""]

    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                elapsed_us = int(line.split("=")[1])
                elapsed = time.time() - start
                pct = min((elapsed_us / 1e6) / duration * 100, 99.9) if duration > 0 else 0
                eta_s = ((100 - pct) / pct) * elapsed if pct > 0 else 0

                border_str = "Con Contorno ✅" if border else "Sin Contorno ❌"
                new_text = (
                    f"╭─「 💧 Marca de Agua 」\n"
                    f"┊ {caption[:40] if caption else 'Video'}\n"
                    f"┊ Texto     : {text}\n"
                    f"┊ Posición  : {pos_label}\n"
                    f"┊ Contorno  : {border_str}\n"
                    f"┊ Tamaño    : {size_pct}%\n"
                    f"┊\n"
                    f"┊ ⚙️ Procesando... {pct:.1f}%\n"
                    f"┊ ⏳ Tiempo restante: ~{get_readable_time(eta_s)}\n"
                    f"╰──────────────────────────\n\n"
                    f"{BOT_CREDITS}"
                )
                if new_text != last_text[0]:
                    await safe_edit(prog_msg, new_text)
                    last_text[0] = new_text
            except Exception:
                pass

    proc.wait()

# ─── UPLOAD HELPER ────────────────────────────────────────────────────────────
async def upload_video_to_chat(client: Client, chat_id: int,
                               video_path: str, caption: str, uid: int,
                               prog_msg: Message | None = None,
                               task_id: str | None = None,
                               user_name: str = "Usuario"):
    thumb = extract_thumbnail(video_path)
    duration_s = int(get_video_duration(video_path))
    file_size  = os.path.getsize(video_path) if os.path.exists(video_path) else 0
    start = time.time()
    last_text = [""]

    task_id_used = task_id or str(uid)

    async def progress_cb(current, total):
        if active_tasks.get(task_id_used, {}).get("cancelled"):
            raise Exception("Cancelled")
        pct     = (current / total * 100) if total else 0
        elapsed = time.time() - start
        speed   = current / elapsed if elapsed > 0 else 0
        eta_s   = (total - current) / speed if speed > 0 else 0

        new_text = fmt_upload_bar(
            user_name, pct,
            get_readable_size(current), get_readable_size(total),
            get_readable_size(speed) + "/s",
            get_readable_time(eta_s), get_readable_time(elapsed),
            task_id_used
        )
        if prog_msg and new_text != last_text[0]:
            await safe_edit(prog_msg, new_text)
            last_text[0] = new_text

    try:
        await client.send_video(
            chat_id,
            video=video_path,
            caption=caption,
            thumb=thumb,
            duration=duration_s,
            supports_streaming=True,
            progress=progress_cb,
        )
        if prog_msg:
            try:
                await prog_msg.delete()
            except Exception:
                pass
    except Exception as e:
        if prog_msg:
            await safe_edit(prog_msg, f"❌ Error al subir: {e}")
    finally:
        # cleanup
        try: os.remove(video_path)
        except: pass
        if thumb:
            try: os.remove(thumb)
            except: pass

# ─── HANDLE VIDEO UPLOADS (for watermark) ─────────────────────────────────────
@bot.on_message(filters.video | filters.document)
async def handle_video_upload(client: Client, message: Message):
    uid  = message.from_user.id
    user_name = message.from_user.first_name or "Usuario"

    # check if it's a video file for watermark
    is_vid = message.video is not None
    is_doc = message.document and message.document.mime_type in ("video/mp4", "video/x-matroska", "video/avi")

    if not (is_vid or is_doc):
        return

    status_msg = await message.reply_text("⬇️ Descargando video...")
    file_obj   = message.video or message.document
    dest_path  = DOWNLOAD_DIR + f"upload_{uid}_{int(time.time())}.mp4"

    try:
        await client.download_media(file_obj.file_id, file_name=dest_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ No se pudo descargar el video: {e}")
        return

    caption = message.caption or message.document.file_name if message.document else "Video"

    WATERMARK_SESSIONS[uid] = {
        "state": "ask_action",
        "video_path": dest_path,
        "caption": caption,
    }

    await status_msg.edit_text(
        f"✅ Video recibido.\n¿Qué deseas hacer?",
        reply_markup=watermark_keyboard()
    )

@bot.on_message(filters.text & ~filters.command(
    ["start", "stat", "Stat", "reset", "Reset", "encode", "Encode"]))
async def handle_text(client: Client, message: Message):
    uid       = message.from_user.id
    user_name = message.from_user.first_name or "Usuario"
    text      = message.text.strip()

    # ── Watermark text input ──────────────────────────────
    sess = WATERMARK_SESSIONS.get(uid)
    if sess and sess.get("state") == "ask_text":
        WATERMARK_SESSIONS[uid] = {**sess, "wm_text": text, "state": "ask_pos"}
        await message.reply_text(
            f"📍 Texto: **{text}**\n\nElige la **posición**:",
            reply_markup=wm_pos_keyboard(),
            parse_mode=enums.ParseMode.MARKDOWN
        )
        return

    # ── Cancel check ──────────────────────────────────────
    if re.match(r"^/cancel", text):
        return

    # ── URL detection ─────────────────────────────────────
    url_match = re.search(r"https?://\S+", text)
    if not url_match:
        return

    url     = url_match.group(0)
    add_lat = "-lat" in text.lower()

    if not is_authorized(uid):
        await message.reply_text("⛔ No estás autorizado para usar este bot.")
        return

    task_id = str(uid) + "_" + str(int(time.time()))
    active_tasks[task_id] = {"cancelled": False, "process": None, "user_id": str(uid)}

    prog_msg = await message.reply_text(
        fmt_download_bar(user_name, 0, 0, 1, "0 B/s", "∞", "0s", task_id)
    )

    dest_dir = DOWNLOAD_DIR + task_id + "/"
    os.makedirs(dest_dir, exist_ok=True)

    try:
        # ── Detect Mega ───────────────────────────────────
        is_mega   = "mega.nz" in url or "mega.co.nz" in url
        is_tiktok = "tiktok.com" in url

        if is_mega:
            video_path = await download_mega(url, dest_dir, task_id, prog_msg, user_name)
        elif is_tiktok:
            # check for carousel
            await safe_edit(prog_msg, fmt_download_bar(user_name, 0, 0, 1, "0 B/s", "∞", "0s", task_id))
            await handle_tiktok_carousel(url, message, user_name)
            await prog_msg.delete()
            active_tasks.pop(task_id, None)
            return
        else:
            video_path = await download_ytdlp(
                url, dest_dir, task_id, prog_msg, user_name,
                subtitle_lang="es" if add_lat else None
            )

        if active_tasks.get(task_id, {}).get("cancelled"):
            await safe_edit(prog_msg, "🛑 Descarga cancelada.")
            return

        if not video_path or not os.path.exists(video_path):
            await safe_edit(prog_msg, "❌ No se pudo descargar el video.\nVerifica el enlace.")
            return

        # ── Get title ─────────────────────────────────────
        title = os.path.splitext(os.path.basename(video_path))[0]
        caption = f"🎬 **{title}**\n\n{BOT_CREDITS}"

        # ── Encode if not mp4 ─────────────────────────────
        if not video_path.endswith(".mp4"):
            enc_out = video_path.rsplit(".", 1)[0] + "_enc.mp4"
            await safe_edit(prog_msg,
                fmt_encode_bar(user_name, 0, "0 KB", "? KB", "0 fps", "∞", "0s", task_id)
            )
            ok = await encode_video(video_path, enc_out, task_id, prog_msg, user_name)
            if ok and os.path.exists(enc_out):
                try: os.remove(video_path)
                except: pass
                video_path = enc_out

        if active_tasks.get(task_id, {}).get("cancelled"):
            await safe_edit(prog_msg, "🛑 Proceso cancelado.")
            return

        # ── Upload ────────────────────────────────────────
        await safe_edit(prog_msg,
            fmt_upload_bar(user_name, 0, "0 MB", "? MB", "0 MB/s", "∞", "0s", task_id)
        )
        await upload_video_to_chat(
            client, message.chat.id, video_path, caption,
            uid, prog_msg, task_id, user_name
        )

    except Exception as e:
        await safe_edit(prog_msg, f"❌ Error: {e}")
    finally:
        active_tasks.pop(task_id, None)
        for f in glob.glob(dest_dir + "*"):
            try: os.remove(f)
            except: pass
        try: os.rmdir(dest_dir)
        except: pass

# ─── /encode (torrent/mkv → mp4) ─────────────────────────────────────────────
@bot.on_message(filters.command(["encode", "Encode"]))
async def cmd_encode(client: Client, message: Message):
    uid       = message.from_user.id
    user_name = message.from_user.first_name or "Usuario"
    args      = message.text.split(maxsplit=1)

    if not message.reply_to_message:
        await message.reply_text(
            "📎 Responde a un archivo de video (.mkv, .avi, .mov, .wmv) con `/encode`\n"
            "para convertirlo a MP4."
        )
        return

    reply = message.reply_to_message
    doc   = reply.document or reply.video
    if not doc:
        await message.reply_text("❌ No se detectó ningún archivo de video en ese mensaje.")
        return

    task_id = str(uid) + "_enc_" + str(int(time.time()))
    active_tasks[task_id] = {"cancelled": False, "process": None, "user_id": str(uid)}

    status_msg = await message.reply_text("⬇️ Descargando archivo...")
    dest_path  = DOWNLOAD_DIR + f"enc_in_{task_id}.mkv"

    try:
        await client.download_media(doc.file_id, file_name=dest_path)
    except Exception as e:
        await status_msg.edit_text(f"❌ Error descargando: {e}")
        active_tasks.pop(task_id, None)
        return

    out_path = DOWNLOAD_DIR + f"enc_out_{task_id}.mp4"
    await safe_edit(status_msg,
        fmt_encode_bar(user_name, 0, "0 KB", "? KB", "0 fps", "∞", "0s", task_id)
    )

    ok = await encode_video(dest_path, out_path, task_id, status_msg, user_name)

    if active_tasks.get(task_id, {}).get("cancelled"):
        await safe_edit(status_msg, "🛑 Encode cancelado.")
        active_tasks.pop(task_id, None)
        return

    if ok and os.path.exists(out_path):
        caption = f"🎬 **{os.path.basename(out_path)}**\n\n{BOT_CREDITS}"
        await safe_edit(status_msg,
            fmt_upload_bar(user_name, 0, "0 MB", "? MB", "0 MB/s", "∞", "0s", task_id)
        )
        await upload_video_to_chat(
            client, message.chat.id, out_path, caption,
            uid, status_msg, task_id, user_name
        )
    else:
        await safe_edit(status_msg, "❌ No se pudo codificar el video.")

    active_tasks.pop(task_id, None)
    for f in [dest_path, out_path]:
        try: os.remove(f)
        except: pass

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not API_ID or API_ID == 0:
        print("❌ ERROR: La variable de entorno API_ID no está configurada.")
        print("   Ve a https://my.telegram.org para obtener tu API_ID y API_HASH.")
        print("   Luego configúralos en los Secrets de Replit.")
        sys.exit(1)

    if not API_HASH:
        print("❌ ERROR: La variable de entorno API_HASH no está configurada.")
        sys.exit(1)

    if not BOT_TOKEN:
        print("❌ ERROR: La variable de entorno BOT_TOKEN no está configurada.")
        print("   Crea un bot con @BotFather en Telegram y copia el token.")
        sys.exit(1)

    print("🚀 Bot iniciando...")
    print(f"   API_ID    : {API_ID}")
    print(f"   BOT_TOKEN : {BOT_TOKEN[:10]}...")
    print(f"   Admins    : {ADMIN_IDS}")
    print(f"   MongoDB   : {'Conectado' if db_connected else 'No configurado (modo abierto)'}")
    bot.run()
