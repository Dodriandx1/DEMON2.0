import os
import sys
import time
import asyncio
import subprocess
import psutil
import httpx
import gc
import yt_dlp
import glob
import http.server
import socketserver
import threading
import re
import json
import struct
import base64
import platform
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from pyrogram import Client, filters, enums
from pyrogram.types import Message, CallbackQuery
from pyrogram.errors import MessageNotModified
from Crypto.Cipher import AES
from Crypto.Util import Counter

# ─── CONFIGURACIÓN PRINCIPAL ────────────────────────────────────────────────
API_ID    = int(os.environ.get("API_ID", "0"))
API_HASH  = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

MEGA_EMAIL    = os.environ.get("MEGA_EMAIL", "")
MEGA_PASSWORD = os.environ.get("MEGA_PASSWORD", "")

SOCIAL_USERNAME = os.environ.get("SOCIAL_USERNAME", "")
SOCIAL_PASSWORD = os.environ.get("SOCIAL_PASSWORD", "")

# Owner / admins
_raw_admin_ids = os.environ.get("ADMIN_IDS", "0")
ADMIN_ID  = int(_raw_admin_ids.split(",")[0].strip()) if _raw_admin_ids.strip() else 0
AUTH_FILE = "authorized_users.json"

DOWNLOAD_DIR = "/tmp/downloads/"
os.makedirs(DOWNLOAD_DIR, mode=0o777, exist_ok=True)

start_time = time.time()

active_tasks: dict  = {}
_task_handles: dict = {}
_ydl_stop: dict     = {}
last_updates: dict  = {}
_wm_sessions: dict  = {}
_torrent_sessions: dict = {}

download_queue: asyncio.Queue = asyncio.Queue()

_stats = {"downloads": 0}

BOT_SIGNATURE = "✪ Bot By → @The_canst & @Ryota_YT"


# ─── SISTEMA DE AUTORIZACIÓN ─────────────────────────────────────────────────
authorized_users = {}

if os.path.exists(AUTH_FILE):
    with open(AUTH_FILE, "r") as _f:
        try:
            _data = json.load(_f)
            if isinstance(_data, list):
                for _uid in _data:
                    authorized_users[str(_uid)] = {"role": "user", "username": "", "name": ""}
            elif isinstance(_data, dict):
                authorized_users = _data
        except Exception:
            pass

def save_auth_users():
    with open(AUTH_FILE, "w") as f:
        json.dump(authorized_users, f)

def is_auth(uid: int) -> bool:
    return uid == ADMIN_ID or str(uid) in authorized_users

def is_admin(uid: int) -> bool:
    if uid == ADMIN_ID:
        return True
    user_data = authorized_users.get(str(uid))
    return bool(user_data and user_data.get("role") == "admin")


# ─── KEEP-ALIVE ───────────────────────────────────────────────────────────────
def keep_alive():
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Running")
        def log_message(self, *args):
            pass

    port = int(os.environ.get("PORT", 8000))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), Handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()


# ─── UTILIDADES ───────────────────────────────────────────────────────────────
def get_readable_size(size) -> str:
    if size is None or size == 0:
        return "0B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return "0B"

def get_readable_time(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"

def make_bar(percentage: float, width: int = 13) -> str:
    filled = int(percentage / 100 * width)
    if filled >= width:
        return "⬢" * width
    return "⬢" * filled + "◉" + "◌" * (width - filled - 1)

def get_platform_icon(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u: return "▶️"
    if "tiktok.com" in u:                      return "🎵"
    if "instagram.com" in u:                   return "📸"
    if "twitter.com" in u or "x.com" in u:     return "🐦"
    if "facebook.com" in u or "fb.com" in u:   return "📘"
    if "reddit.com" in u:                       return "🤖"
    if "mega.nz" in u:                          return "☁️"
    if "mediafire.com" in u:                    return "🗂️"
    if "spotify.com" in u:                      return "🎧"
    if "soundcloud.com" in u:                   return "🎶"
    if "pinterest.com" in u:                    return "📌"
    if "threads.net" in u:                      return "🧵"
    if "snapchat.com" in u:                     return "👻"
    if "tumblr.com" in u:                       return "📝"
    return "🌐"

def extract_thumbnail(video_path: str):
    thumb = video_path + ".jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-ss", "00:00:01",
             "-vframes", "1", "-vf", "scale=320:-1", "-q:v", "2", thumb, "-y"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=True
        )
        return thumb if os.path.exists(thumb) else None
    except Exception:
        return None

def get_video_meta(video_path: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=10
        )
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        width, height, duration = 0, 0, 0
        for line in lines:
            parts = line.split(",")
            if len(parts) == 2:
                try:
                    width, height = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            elif len(parts) == 1:
                try:
                    duration = int(float(parts[0]))
                except ValueError:
                    pass
        return {"width": width, "height": height, "duration": duration}
    except Exception:
        return {"width": 0, "height": 0, "duration": 0}

def get_spanish_sub_index(input_path: str):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=index:stream_tags=language",
             "-of", "csv=p=0", input_path],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split(",")
            if len(parts) >= 2:
                idx  = parts[0]
                lang = parts[1].lower()
                if lang in ("spa", "es", "lat"):
                    return idx
    except Exception:
        pass
    return None


# ─── MOTOR MEGA NATIVO ────────────────────────────────────────────────────────
_MEGA_API   = "https://g.api.mega.co.nz/cs"
_MEGA_CHUNK = 4 * 1024 * 1024

def _mega_b64decode(s: str) -> bytes:
    s = s.replace('-', '+').replace('_', '/')
    s += '=' * (-len(s) % 4)
    return base64.b64decode(s)

def _mega_b64encode(b: bytes) -> str:
    return base64.b64encode(b).decode().replace('+', '-').replace('/', '_').rstrip('=')

def _a32(b: bytes) -> list:
    b += b'\x00' * (-len(b) % 4)
    return list(struct.unpack('>' + 'I' * (len(b) // 4), b))

def _a32_bytes(a: list) -> bytes:
    return struct.pack('>' + 'I' * len(a), *a)

def _aes_cbc_enc_a32(data: list, key: list) -> list:
    c = AES.new(_a32_bytes(key), AES.MODE_CBC, iv=b'\x00' * 16)
    return _a32(c.encrypt(_a32_bytes(data)))

def _aes_cbc_dec_a32(data: list, key: list) -> list:
    c = AES.new(_a32_bytes(key), AES.MODE_CBC, iv=b'\x00' * 16)
    return _a32(c.decrypt(_a32_bytes(data)))

def _mega_prepare_key(password: str) -> list:
    pw   = _a32(password.encode('utf-8'))
    pkey = [0x93C467E3, 0x7DB0C7A4, 0xD1BE3F81, 0x0152CB56]
    for _ in range(0x10000):
        for i in range(0, len(pw), 4):
            block = (pw[i:i+4] + [0, 0, 0, 0])[:4]
            pkey  = _aes_cbc_enc_a32(pkey, block)
    return pkey

def _mega_stringhash(s: str, aes_key: list) -> str:
    h = [0, 0, 0, 0]
    for i, v in enumerate(_a32(s.encode('utf-8'))):
        h[i % 4] ^= v
    for _ in range(0x4000):
        h = _aes_cbc_enc_a32(h, aes_key)
    return _mega_b64encode(_a32_bytes([h[0], h[2]]))

def _mega_parse_url(url: str):
    m = re.search(r'mega\.nz/file/([^#\s]+)#([^\s&]+)', url)
    if m: return m.group(1), m.group(2)
    m = re.search(r'mega\.nz/#!([^!\s]+)!([^\s&]+)', url)
    if m: return m.group(1), m.group(2)
    return None, None

def _mega_decode_attrs(at_b64: str, aes_key_bytes: bytes) -> str:
    raw   = _mega_b64decode(at_b64)
    raw  += b'\x00' * (-len(raw) % 16)
    plain = AES.new(aes_key_bytes, AES.MODE_CBC, iv=b'\x00' * 16).decrypt(raw)
    plain = plain.decode('utf-8', errors='ignore').rstrip('\x00')
    if plain.startswith('MEGA'):
        try:
            return json.loads(plain[4:]).get('n', '')
        except Exception:
            pass
    return ''

async def _mega_api(payload: list, sid: str = '') -> list:
    params = {'id': 1}
    if sid: params['sid'] = sid
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_MEGA_API, params=params, json=payload)
    return r.json()

def _mega_mpi_read(buf: bytes, pos: int):
    bits     = (buf[pos] << 8) | buf[pos + 1]
    byte_len = (bits + 7) // 8
    val      = int.from_bytes(buf[pos + 2: pos + 2 + byte_len], 'big')
    return val, pos + 2 + byte_len

async def mega_login(email: str, password: str) -> dict:
    pw_key = await asyncio.to_thread(_mega_prepare_key, password)
    uh     = _mega_stringhash(email.lower(), pw_key)
    data   = await _mega_api([{"a": "us", "user": email.lower(), "uh": uh}])
    resp   = data[0]
    if isinstance(resp, int):
        codes = {-2: "Contraseña incorrecta.", -3: "Demasiados intentos.", -9: "Cuenta no encontrada."}
        raise Exception(f"MEGA login {resp}: {codes.get(resp, 'error desconocido')}")
    enc_mk = _a32(_mega_b64decode(resp['k']))
    mk     = _aes_cbc_dec_a32(enc_mk, pw_key)
    if 'tsid' in resp:
        tsid_bytes = _mega_b64decode(resp['tsid'])
        verify = AES.new(_a32_bytes(mk), AES.MODE_CBC, iv=b'\x00'*16).encrypt(tsid_bytes[:16])
        if verify == tsid_bytes[16:]:
            return {'sid': resp['tsid'], 'master_key': mk}
    if 'csid' in resp and 'privk' in resp:
        try:
            privk_enc  = _mega_b64decode(resp['privk'])
            privk_enc += b'\x00' * (-len(privk_enc) % 16)
            privk      = AES.new(_a32_bytes(mk), AES.MODE_CBC, iv=b'\x00'*16).decrypt(privk_enc)
            pos = 0
            p, pos  = _mega_mpi_read(privk, pos)
            q, pos  = _mega_mpi_read(privk, pos)
            d, pos  = _mega_mpi_read(privk, pos)
            _u, pos = _mega_mpi_read(privk, pos)
            n          = p * q
            csid_bytes = _mega_b64decode(resp['csid'])
            csid_int   = int.from_bytes(csid_bytes, 'big')
            m          = pow(csid_int, d, n)
            m_bytes    = m.to_bytes((m.bit_length() + 7) // 8, 'big')
            sid        = _mega_b64encode(m_bytes[:43])
            return {'sid': sid, 'master_key': mk}
        except Exception as e:
            raise Exception(f"MEGA RSA session decrypt falló: {e}")
    raise Exception("MEGA login: respuesta inesperada del servidor.")

async def mega_download(url: str, dest_dir: str, task_id: str, progress_cb=None):
    handle, key_b64 = _mega_parse_url(url)
    if not handle:
        raise ValueError("URL de MEGA no válida. Debe ser mega.nz/file/HANDLE#KEY")
    sid = ''
    if MEGA_EMAIL and MEGA_PASSWORD:
        try:
            session = await mega_login(MEGA_EMAIL, MEGA_PASSWORD)
            sid = session['sid']
        except Exception:
            pass
    raw = _mega_b64decode(key_b64)
    if len(raw) < 32:
        raise ValueError("Clave MEGA inválida en el URL.")
    k             = struct.unpack('>8I', raw[:32])
    aes_key_bytes = struct.pack('>4I', k[0]^k[4], k[1]^k[5], k[2]^k[6], k[3]^k[7])
    iv_int        = (k[4] << 96) | (k[5] << 64)
    payload       = [{"a": "g", "g": 1, "p": handle}]
    data          = await _mega_api(payload, sid=sid)
    item          = data[0]
    if isinstance(item, int):
        codes = {-2: "Enlace inválido o expirado.", -9: "Objeto no encontrado.",
                 -16: "Cuota de descarga excedida.", -18: "Recurso no disponible."}
        raise Exception(f"MEGA error {item}: {codes.get(item, 'desconocido')}")
    dl_url = item.get('g')
    total  = item.get('s', 0)
    if not dl_url:
        raise Exception("MEGA no devolvió URL de descarga.")
    filename  = _mega_decode_attrs(item.get('at', ''), aes_key_bytes) or f"mega_{handle}"
    dest_path = os.path.join(dest_dir, f"{task_id}_{filename}")
    ctr    = Counter.new(128, initial_value=iv_int, little_endian=False)
    cipher = AES.new(aes_key_bytes, AES.MODE_CTR, counter=ctr)
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as hclient:
        async with hclient.stream('GET', dl_url) as resp:
            downloaded = 0
            with open(dest_path, 'wb') as f:
                async for chunk in resp.aiter_bytes(_MEGA_CHUNK):
                    await asyncio.sleep(0)
                    orig_len  = len(chunk)
                    if orig_len % 16: chunk += b'\x00' * (16 - orig_len % 16)
                    decrypted = cipher.decrypt(chunk)[:orig_len]
                    f.write(decrypted)
                    downloaded += orig_len
                    if progress_cb and total > 0:
                        await progress_cb(downloaded, total)
    title = filename.rsplit('.', 1)[0] if '.' in filename else filename
    return dest_path, title


# ─── PANELES DE PROGRESO ──────────────────────────────────────────────────────
async def safe_edit(msg: Message, text: str, reply_markup=None):
    try:
        await msg.edit_text(text, parse_mode=None, reply_markup=reply_markup)
    except MessageNotModified:
        pass
    except Exception:
        pass

def download_panel(uname: str, percentage: float, status: str, done_bytes: int,
                   total_bytes: int, speed_bps: float, elapsed: float, eta: float,
                   engine: str, mode: str, task_id: str) -> str:
    bar = make_bar(percentage)
    return (
        f"╭ Task By → 「{uname}」\n"
        f"┊ [{bar}] {percentage:.2f}%\n"
        f"┊ Status   : {status}\n"
        f"┊ Done     : {get_readable_size(done_bytes)}\n"
        f"┊ Total    : {get_readable_size(total_bytes)}\n"
        f"┊ Speed    : {get_readable_size(speed_bps)}/s\n"
        f"┊ ETA      : {get_readable_time(eta)}\n"
        f"┊ Past     : {get_readable_time(elapsed)}\n"
        f"┊ Engine   : {engine}\n"
        f"╰ Mode     : {mode}\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_SIGNATURE}"
    )

def encoding_panel(uname: str, percentage: float, done_bytes: int, total_bytes: int,
                   fps: float, elapsed: float, eta: float, task_id: str) -> str:
    bar = make_bar(percentage)
    return (
        f"╭ Task By → 「{uname}」\n"
        f"┊ [{bar}] {percentage:.2f}%\n"
        f"┊ Status   : Encoding\n"
        f"┊ Done     : {get_readable_size(done_bytes)}\n"
        f"┊ Total    : {get_readable_size(total_bytes)}\n"
        f"┊ Speed    : {fps:.2f} fps\n"
        f"┊ ETA      : {get_readable_time(eta)}\n"
        f"┊ Past     : {get_readable_time(elapsed)}\n"
        f"┊ Engine   : FFmpeg\n"
        f"╰ Mode     : #FFENC\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_SIGNATURE}"
    )

def upload_panel(uname: str, percentage: float, done_bytes: int, total_bytes: int,
                 speed_bps: float, elapsed: float, eta: float, task_id: str) -> str:
    bar = make_bar(percentage)
    return (
        f"╭ Task By → 「{uname}」\n"
        f"┊ [{bar}] {percentage:.2f}%\n"
        f"┊ Status   : Upload\n"
        f"┊ Done     : {get_readable_size(done_bytes)}\n"
        f"┊ Total    : {get_readable_size(total_bytes)}\n"
        f"┊ Speed    : {get_readable_size(speed_bps)}/s\n"
        f"┊ ETA      : {get_readable_time(eta)}\n"
        f"┊ Past     : {get_readable_time(elapsed)}\n"
        f"┊ Engine   : Pyrogram\n"
        f"╰ Mode     : #TLGUP\n"
        f"⋗ Stop : /cancel_{task_id}\n\n"
        f"{BOT_SIGNATURE}"
    )

async def download_progress(current: int, total: int, msg: Message, start_t: float,
                             uname: str, task_id: str, engine: str, mode: str):
    if active_tasks.get(task_id) == "CANCELLED":
        raise asyncio.CancelledError("USER_CANCELLED")
    now = time.time()
    if now - last_updates.get(task_id + "_dl", 0) < 3 and current < total:
        return
    last_updates[task_id + "_dl"] = now
    elapsed = now - start_t
    pct     = (current / total * 100) if total > 0 else 0
    speed   = current / elapsed if elapsed > 0 else 0
    eta     = (total - current) / speed if speed > 0 else 0
    panel   = download_panel(uname, pct, "Download", current, total, speed, elapsed, eta, engine, mode, task_id)
    await safe_edit(msg, panel)

async def upload_progress(current: int, total: int, msg: Message, start_t: float,
                          uname: str, task_id: str):
    if active_tasks.get(task_id) == "CANCELLED":
        raise asyncio.CancelledError("USER_CANCELLED")
    now = time.time()
    if now - last_updates.get(task_id + "_up", 0) < 3 and current < total:
        return
    last_updates[task_id + "_up"] = now
    elapsed = now - start_t
    pct     = (current / total * 100) if total > 0 else 0
    speed   = current / elapsed if elapsed > 0 else 0
    eta     = (total - current) / speed if speed > 0 else 0
    panel   = upload_panel(uname, pct, current, total, speed, elapsed, eta, task_id)
    await safe_edit(msg, panel)


async def _ensure_jpeg(path: str) -> str:
    lower = path.lower()
    if not lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".avif")):
        return path
    out = path.rsplit(".", 1)[0] + "_conv.jpg"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", path, "-q:v", "2", "-f", "mjpeg", out,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=30)
        if os.path.exists(out) and os.path.getsize(out) > 500:
            return out
    except Exception:
        pass
    return path


# ─── SUBIDA INTELIGENTE ───────────────────────────────────────────────────────
async def upload_smart_file(client: Client, message: Message, path: str,
                             msg: Message, uname: str, task_id: str, title: str = ""):
    fname   = os.path.basename(path)
    display = title.strip() if title.strip() else fname
    lower   = fname.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")):
        icon = "🖼️"
    elif lower.endswith((".mp3", ".m4a", ".wav", ".flac", ".ogg")):
        icon = "🎵"
    else:
        icon = "🎬"
    caption = f"{icon} <b>{display}</b>\n\n{BOT_SIGNATURE}"
    start_t = time.time()

    if lower.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
        thumb = extract_thumbnail(path)
        meta  = get_video_meta(path)
        try:
            await client.send_video(
                chat_id=message.chat.id, video=path, thumb=thumb,
                supports_streaming=True,
                width=meta["width"] or None, height=meta["height"] or None,
                duration=meta["duration"] or None,
                caption=caption, parse_mode=enums.ParseMode.HTML,
                progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await client.send_video(
                    chat_id=message.chat.id, video=path, thumb=thumb,
                    supports_streaming=True,
                    caption=caption, parse_mode=enums.ParseMode.HTML,
                    progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await client.send_document(
                    chat_id=message.chat.id, document=path, thumb=thumb,
                    caption=caption, parse_mode=enums.ParseMode.HTML,
                    progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
                )
        finally:
            if thumb and os.path.exists(thumb):
                try: os.remove(thumb)
                except Exception: pass

    elif lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
        photo_path = await _ensure_jpeg(path)
        try:
            await client.send_photo(
                chat_id=message.chat.id, photo=photo_path, caption=caption,
                parse_mode=enums.ParseMode.HTML,
                progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await client.send_document(
                chat_id=message.chat.id, document=photo_path, caption=caption,
                parse_mode=enums.ParseMode.HTML,
                progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
            )
        finally:
            if photo_path != path and os.path.exists(photo_path):
                try: os.remove(photo_path)
                except Exception: pass

    elif lower.endswith(".gif"):
        await client.send_animation(
            chat_id=message.chat.id, animation=path, caption=caption,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
        )
    elif lower.endswith((".mp3", ".m4a", ".wav", ".flac", ".ogg")):
        await client.send_audio(
            chat_id=message.chat.id, audio=path, caption=caption,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
        )
    else:
        await client.send_document(
            chat_id=message.chat.id, document=path, caption=caption,
            parse_mode=enums.ParseMode.HTML,
            progress=upload_progress, progress_args=(msg, start_t, uname, task_id)
        )


# ─── RECODIFICADOR ────────────────────────────────────────────────────────────
def probe_video(input_path: str) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True
        )
        lines       = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        codec       = lines[0] if lines else "unknown"
        duration    = float(lines[1]) if len(lines) > 1 else 0.0
        audio       = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True
        )
        audio_codec = audio.stdout.strip().splitlines()[0].strip() if audio.stdout.strip() else "unknown"
        return {"codec": codec, "duration": duration, "audio_codec": audio_codec}
    except Exception:
        return {"codec": "unknown", "duration": 0.0, "audio_codec": "unknown"}

async def encode_video(input_path: str, output_path: str, msg: Message,
                       uname: str, task_id: str) -> bool:
    info        = await asyncio.to_thread(probe_video, input_path)
    codec       = info["codec"]
    total_dur   = info["duration"]
    audio_codec = info["audio_codec"]
    input_size  = os.path.getsize(input_path)
    is_h264     = codec == "h264"
    is_aac_compat = audio_codec in ("aac", "mp3", "mp4a")

    # Siempre usar -map 0:v:0 y -map 0:a? para evitar streams de subtítulos
    # y codecs incompatibles con el contenedor MP4
    base = ["ffmpeg", "-threads", "0", "-i", input_path,
            "-map", "0:v:0", "-map", "0:a?"]

    if is_h264 and is_aac_compat:
        cmd = base + ["-c:v", "copy", "-c:a", "copy",
                      "-movflags", "+faststart", "-y", output_path]
    elif is_h264:
        cmd = base + ["-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                      "-movflags", "+faststart", "-y", output_path]
    else:
        cmd = base + ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                      "-c:a", "aac", "-b:a", "128k",
                      "-movflags", "+faststart", "-progress", "pipe:1",
                      "-nostats", "-y", output_path]

    proc    = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    start_t = time.time()
    fps_val = 0.0
    time_done = 0.0

    try:
        while True:
            if active_tasks.get(task_id) == "CANCELLED":
                proc.kill()
                await proc.wait()
                return False
            line_bytes = await proc.stdout.readline()
            if not line_bytes: break
            line = line_bytes.decode().strip()
            if line.startswith("fps="):
                try: fps_val = float(line.split("=")[1])
                except Exception: pass
            if line.startswith("out_time_ms="):
                try: time_done = int(line.split("=")[1]) / 1_000_000
                except Exception: pass
            now = time.time()
            if now - last_updates.get(task_id + "_enc", 0) >= 2:
                last_updates[task_id + "_enc"] = now
                elapsed = now - start_t
                pct     = min((time_done / total_dur * 100) if total_dur > 0 else 0, 99)
                eta     = ((total_dur - time_done) / fps_val * 25) if fps_val > 0 else 0
                panel   = encoding_panel(uname, pct, int(input_size * pct / 100),
                                         input_size, fps_val, elapsed, eta, task_id)
                await safe_edit(msg, panel)
    except (asyncio.CancelledError, Exception):
        try: proc.kill(); await proc.wait()
        except Exception: pass
        raise

    await proc.wait()
    if proc.returncode != 0:
        stderr_out = (await proc.stderr.read()).decode(errors="ignore")[-300:]
        print(f"[encode_video] FFmpeg error (rc={proc.returncode}): {stderr_out}")
    return proc.returncode == 0 and os.path.exists(output_path)


# ─── ENCODE DE ARCHIVO RECIBIDO ───────────────────────────────────────────────
async def procesar_encode(client: Client, message: Message, file_id: str,
                          uname: str, uid: int, want_subs: bool = False,
                          original_name: str = "video"):
    task_id = f"{uid}_{int(time.time())}"
    active_tasks[task_id] = "RUNNING"
    msg = await message.reply_text(
        f"╭ Task By → 「{uname}」\n"
        f"┊ [{make_bar(0)}] 0.00%\n"
        f"┊ Status   : Descargando...\n"
        f"┊ Archivo  : {original_name[:40]}\n"
        f"╰ Mode     : #Encode\n\n{BOT_SIGNATURE}"
    )
    input_path  = os.path.join(DOWNLOAD_DIR, f"{task_id}_input.mkv")
    output_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_output.mp4")
    try:
        await client.download_media(file_id, file_name=input_path)
        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n"
            f"┊ [{make_bar(0)}] 0.00%\n"
            f"┊ Status   : Convirtiendo a MP4...\n"
            f"┊ Archivo  : {original_name[:40]}\n"
            f"╰ Mode     : #Encode\n\n{BOT_SIGNATURE}"
        )
        success = False
        if not want_subs:
            # Fast-copy: mapear solo video+audio para evitar streams incompatibles con MP4
            cmd = ["ffmpeg", "-y", "-i", input_path,
                   "-map", "0:v:0", "-map", "0:a?",
                   "-c:v", "copy", "-c:a", "copy",
                   "-movflags", "+faststart", output_path]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr_bytes = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(output_path):
                success = True
            else:
                print(f"[encode] fast-copy falló (rc={proc.returncode}): {stderr_bytes.decode(errors='ignore')[-400:]}")
        if not success or want_subs:
            if os.path.exists(output_path):
                try: os.remove(output_path)
                except: pass
            success = await encode_video(input_path, output_path, msg, uname, task_id)
        if not success or not os.path.exists(output_path):
            raise Exception("Conversión fallida — revisa los logs del bot para ver el error de FFmpeg")
        await safe_edit(msg, upload_panel(uname, 0, 0, os.path.getsize(output_path), 0, 0, 0, task_id))
        await upload_smart_file(client, message, output_path, msg, uname, task_id,
                                title=f"{original_name} [MP4]")
    except Exception as e:
        await safe_edit(msg, f"❌ Error:\n{str(e)[:150]}\n\n{BOT_SIGNATURE}")
    finally:
        active_tasks.pop(task_id, None)
        for p in [input_path, output_path]:
            try: os.remove(p)
            except: pass


# ─── NÚCLEO DE DESCARGA ───────────────────────────────────────────────────────
async def procesar_descarga(client: Client, message: Message, url: str,
                             uname: str, uid: int, queue_label: str,
                             want_subs: bool = False):
    mirrors = {
        "flashwish.com": "streamwish.com", "callistanise.com": "vidhide.com",
        "swishdesu.com": "streamwish.com", "filelions.com": "streamwish.com",
        "filelions.to": "streamwish.com", "vidhidepro.com": "vidhide.com",
        "vidhideplus.com": "vidhide.com",
    }
    for mirror, main in mirrors.items():
        if mirror in url.lower():
            url = url.replace(mirror, main)
            break

    VIDEO_HOSTS = ["streamwish", "voe", "vidhide", "filemoon", "mixdrop",
                   "mp4upload", "streamtape", "flashwish", "callistanise",
                   "filelions", "swishdesu"]
    IMAGE_EXTS  = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

    is_mega           = "mega.nz" in url
    is_mf             = "mediafire.com" in url
    is_tiktok         = "tiktok.com" in url.lower()
    is_spotify        = "spotify.com" in url.lower()
    is_soundcloud     = "soundcloud.com" in url.lower()
    is_video_host     = any(h in url.lower() for h in VIDEO_HOSTS)
    is_direct_img     = any(url.lower().split("?")[0].endswith(ext) for ext in IMAGE_EXTS)
    _CAROUSEL_DOMAINS = ("instagram.com", "twitter.com", "x.com", "facebook.com",
                         "fb.com", "reddit.com", "tumblr.com", "threads.net",
                         "pinterest.com", "snapchat.com")
    is_carousel_platform = any(d in url.lower() for d in _CAROUSEL_DOMAINS)
    is_social         = (url.startswith("http") and not any([
        is_mega, is_mf, is_video_host, is_direct_img, is_tiktok, is_spotify, is_soundcloud
    ]))

    task_id = f"{uid}_{int(time.time())}"
    active_tasks[task_id] = "RUNNING"

    _current = asyncio.current_task()
    if _current: _task_handles[task_id] = _current

    _stop_evt = threading.Event()
    _ydl_stop[task_id] = _stop_evt

    msg = await message.reply_text(
        f"╭ Task By → 「{uname}」\n"
        f"┊ [{make_bar(0)}] 0.00%\n"
        f"┊ Status   : Analyzing...\n"
        f"╰ Queue    : {queue_label}\n\n"
        f"{BOT_SIGNATURE}"
    )

    path        = None
    encoded_path = None
    sub_path    = None
    video_title = ""

    try:
        # 1. MEGA
        if is_mega:
            engine, mode, start_t = "direct", "#MEGA", time.time()
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ [{make_bar(0)}] 0.00%\n"
                f"┊ Status   : Connecting to MEGA...\n"
                f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
            async def _mega_progress(curr, total):
                await download_progress(curr, total, msg, start_t, uname, task_id, engine, mode)
            path, video_title = await mega_download(url, DOWNLOAD_DIR, task_id, progress_cb=_mega_progress)

        # 2. MEDIAFIRE
        elif is_mf:
            engine, mode = "httpx", "#MediaFire"
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as h:
                r    = await h.get(url)
                soup = BeautifulSoup(r.text, "html.parser")
                btn  = soup.find("a", {"id": "downloadButton"})
                if not btn: raise Exception("Mediafire: botón de descarga no encontrado.")
                d_link   = btn.get("href")
                filename = d_link.split("/")[-1].split("?")[0]
                path     = os.path.join(DOWNLOAD_DIR, filename)
                video_title = os.path.splitext(filename)[0]
                start_t = time.time()
                async with h.stream("GET", d_link) as resp:
                    total = int(resp.headers.get("content-length", 0))
                    with open(path, "wb") as f:
                        curr = 0
                        async for chunk in resp.aiter_bytes(chunk_size=4 * 1024 * 1024):
                            await asyncio.sleep(0)
                            if active_tasks.get(task_id) == "CANCELLED":
                                raise asyncio.CancelledError("USER_CANCELLED")
                            f.write(chunk)
                            curr += len(chunk)
                            await download_progress(curr, total, msg, start_t, uname, task_id, engine, mode)

        # 3. IMÁGENES DIRECTAS
        elif is_direct_img:
            engine, mode = "httpx", "#Image"
            filename = url.split("/")[-1].split("?")[0]
            if not filename or "." not in filename: filename = "image.jpg"
            path        = os.path.join(DOWNLOAD_DIR, f"{task_id}_{filename}")
            video_title = filename
            start_t     = time.time()
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as h:
                async with h.stream("GET", url) as resp:
                    total = int(resp.headers.get("content-length", 0))
                    with open(path, "wb") as f:
                        curr = 0
                        async for chunk in resp.aiter_bytes(chunk_size=4 * 1024 * 1024):
                            await asyncio.sleep(0)
                            if active_tasks.get(task_id) == "CANCELLED":
                                raise asyncio.CancelledError("USER_CANCELLED")
                            f.write(chunk)
                            curr += len(chunk)
                            await download_progress(curr, total, msg, start_t, uname, task_id, engine, mode)

        # 4. TIKTOK
        elif is_tiktok:
            engine, mode, start_t = "TikWM API", "#TikTok", time.time()
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ [{make_bar(0)}] 0.00%\n"
                f"┊ Status   : Conectando a TikTok API...\n"
                f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
            api_req = f"https://www.tikwm.com/api/?url={url}&hd=1"
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as h:
                tk_data = (await h.get(api_req)).json()
            if tk_data.get("code") == 0:
                data_obj    = tk_data.get("data", {})
                video_title = data_obj.get("title", "TikTok")
                images      = data_obj.get("images")
                video_url   = data_obj.get("hdplay") or data_obj.get("play")
                if images and isinstance(images, list):
                    total_imgs = len(images)
                    files_tik  = []
                    for idx, img_url in enumerate(images):
                        if active_tasks.get(task_id) == "CANCELLED":
                            raise asyncio.CancelledError("USER_CANCELLED")
                        await safe_edit(msg,
                            f"╭ Task By → 「{uname}」\n┊ 🎵 Álbum TikTok\n"
                            f"┊ 📥 Foto {idx+1}/{total_imgs}...\n"
                            f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
                        img_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_{idx}.jpg")
                        async with httpx.AsyncClient() as h:
                            with open(img_path, "wb") as f:
                                f.write((await h.get(img_url)).content)
                        files_tik.append(img_path)
                    from pyrogram.types import InputMediaPhoto
                    album_caption = f"🖼️ <b>{video_title}</b>\n\n{BOT_SIGNATURE}" if video_title else BOT_SIGNATURE
                    group = []
                    for idx, f in enumerate(files_tik):
                        cap   = album_caption if idx == 0 else None
                        group.append(InputMediaPhoto(f, caption=cap, parse_mode=enums.ParseMode.HTML))
                    if group:
                        await safe_edit(msg,
                            f"╭ Task By → 「{uname}」\n"
                            f"┊ ⬆️ Subiendo álbum ({total_imgs} fotos)...\n"
                            f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
                        for i in range(0, len(group), 10):
                            await client.send_media_group(message.chat.id, group[i:i+10])
                    _stats["downloads"] += 1
                    await msg.delete()
                    for f in files_tik:
                        try: os.remove(f)
                        except: pass
                    return
                elif video_url:
                    path = os.path.join(DOWNLOAD_DIR, f"{task_id}.mp4")
                    async with httpx.AsyncClient(timeout=120.0) as h:
                        async with h.stream("GET", video_url) as r:
                            total = int(r.headers.get("content-length", 0))
                            with open(path, "wb") as f:
                                curr = 0
                                async for chunk in r.aiter_bytes(chunk_size=4 * 1024 * 1024):
                                    if active_tasks.get(task_id) == "CANCELLED":
                                        raise asyncio.CancelledError("USER_CANCELLED")
                                    f.write(chunk)
                                    curr += len(chunk)
                                    await download_progress(curr, total, msg, start_t, uname, task_id, engine, mode)
                else:
                    raise Exception("La API de TikTok no devolvió contenido multimedia.")
            else:
                raise Exception("Fallo en la extracción del enlace de TikTok.")

        # 5. SPOTIFY / SOUNDCLOUD
        elif is_spotify or is_soundcloud:
            engine, mode, start_t = "yt-dlp", "#AudioStream", time.time()
            loop = asyncio.get_running_loop()
            captured = {"title": ""}
            def ydl_hook_audio(d):
                if _stop_evt.is_set() or active_tasks.get(task_id) == "CANCELLED":
                    raise ValueError("USER_CANCELLED")
                if d["status"] == "downloading":
                    curr  = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if total > 0:
                        asyncio.run_coroutine_threadsafe(
                            download_progress(curr, total, msg, start_t, uname, task_id, engine, mode), loop)
            def run_ydl_audio():
                base_opts = {
                    "outtmpl": f"{DOWNLOAD_DIR}{task_id}_%(playlist_index)s%(title)s.%(ext)s",
                    "noplaylist": False,
                    "progress_hooks": [ydl_hook_audio],
                    "quiet": True, "no_warnings": True,
                    "format": "bestaudio/best",
                    "postprocessors": [{"key": "FFmpegExtractAudio",
                                        "preferredcodec": "mp3", "preferredquality": "192"}],
                }
                def _extract(opts):
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        target_url = f"ytsearch1:{url}" if is_spotify else url
                        info = ydl.extract_info(target_url, download=True)
                        if info:
                            if 'entries' in info and info['entries']:
                                captured["title"] = info['entries'][0].get("title", "Audio Track")
                            else:
                                captured["title"] = info.get("title", "Audio Track")
                try: _extract(base_opts)
                except Exception as _e:
                    if "USER_CANCELLED" in str(_e) or isinstance(_e, ValueError):
                        raise asyncio.CancelledError("USER_CANCELLED")
                    raise Exception("No se pudo extraer el audio.")
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ [{make_bar(0)}] 0.00%\n"
                f"┊ Status   : Procesando Audio...\n"
                f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
            await asyncio.to_thread(run_ydl_audio)
            video_title = captured["title"]
            files = sorted(glob.glob(f"{DOWNLOAD_DIR}{task_id}_*.mp3"), key=os.path.getsize)
            if not files: raise Exception("No se pudo descargar el audio.")
            if len(files) > 1:
                await safe_edit(msg,
                    f"╭ Task By → 「{uname}」\n┊ Status : Subiendo Playlist...\n"
                    f"╰ Pistas : {len(files)}\n\n{BOT_SIGNATURE}")
                for idx, f in enumerate(files):
                    if active_tasks.get(task_id) == "CANCELLED":
                        raise asyncio.CancelledError("USER_CANCELLED")
                    await client.send_audio(message.chat.id, f,
                        caption=f"🎵 <b>Pista {idx+1}</b>\n\n{BOT_SIGNATURE}",
                        parse_mode=enums.ParseMode.HTML)
                    os.remove(f)
                await msg.delete()
                return
            else:
                path = files[0]

        # 6. REDES SOCIALES / VIDEO HOSTS
        elif is_social or is_video_host:
            engine  = "yt-dlp"
            _icon   = get_platform_icon(url)
            mode    = f"{_icon} #VideoHoster" if is_video_host else f"{_icon} #SocialMedia"
            start_t = time.time()
            loop    = asyncio.get_running_loop()
            captured = {"title": ""}
            _YT_CLIENTS = ["ios", "android", "web_creator", "mweb", "tv_embedded", "web"]

            def ydl_hook(d):
                if _stop_evt.is_set() or active_tasks.get(task_id) == "CANCELLED":
                    raise ValueError("USER_CANCELLED")
                if d["status"] == "downloading":
                    curr  = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if total > 0:
                        asyncio.run_coroutine_threadsafe(
                            download_progress(curr, total, msg, start_t, uname, task_id, engine, mode), loop)

            def _is_cancelled(e):
                return "USER_CANCELLED" in str(e) or isinstance(e, (ValueError, asyncio.CancelledError))

            def _is_blocked(e):
                s = str(e).lower()
                return any(p in s for p in [
                    "sign in", "not a bot", "429", "too many requests",
                    "403", "blocked", "precondition", "rate limit",
                    "confirm your age", "nsig extraction failed",
                    "unable to extract", "uploader has not made",
                    "requested format is not available",
                    "format is not available", "no video formats found",
                ])

            def _is_youtube(u):
                return "youtube.com" in u or "youtu.be" in u

            def run_ydl():
                base_opts = {
                    "outtmpl": f"{DOWNLOAD_DIR}{task_id}_%(playlist_index)s%(title)s.%(ext)s",
                    "noplaylist": False,
                    "progress_hooks": [ydl_hook],
                    "quiet": True, "no_warnings": True,
                    "concurrent_fragment_downloads": 10,
                    "http_chunk_size": 10 * 1024 * 1024,
                    "retries": 3, "fragment_retries": 3,
                }
                if SOCIAL_USERNAME and SOCIAL_PASSWORD and not _is_youtube(url):
                    base_opts["username"] = SOCIAL_USERNAME
                    base_opts["password"] = SOCIAL_PASSWORD
                if is_video_host:
                    base_opts["format"] = "best"
                    base_opts["http_headers"] = {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Referer": url,
                    }
                elif not is_carousel_platform:
                    base_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                if want_subs:
                    base_opts["writesubtitles"]    = True
                    base_opts["writeautomaticsub"] = True
                    base_opts["subtitleslangs"]    = ["es", "es-419", "es-MX", "es-ES", "es-CO", "es-AR"]
                    base_opts["subtitlesformat"]   = "srt/vtt/best"
                if os.path.exists("telegram-bot/cookies.txt"):
                    base_opts["cookiefile"] = "telegram-bot/cookies.txt"
                elif os.path.exists("cookies.txt"):
                    base_opts["cookiefile"] = "cookies.txt"

                def _extract(opts):
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        if info:
                            captured["title"] = (
                                info.get("title", "") or info.get("webpage_url_basename", "")
                            )

                last_error = None
                if _is_youtube(url):
                    _COMBINED_CLIENTS = {"ios", "android", "mweb", "tv_embedded"}
                    _FMT_COMBINED = "best[ext=mp4]/best"
                    _FMT_SPLIT    = ("bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                                     "/bestvideo+bestaudio/best[ext=mp4]/best")
                    _UA = {
                        "ios":     "com.google.ios.youtube/19.29.1 CFNetwork/1474 Darwin/23.0.0",
                        "android": "com.google.android.youtube/18.11.34 (Linux; U; Android 12; GB) gzip",
                        "mweb":    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                    "AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"),
                    }
                    for _client in _YT_CLIENTS:
                        if _stop_evt.is_set(): raise ValueError("USER_CANCELLED")
                        opts = dict(base_opts)
                        opts["format"] = _FMT_COMBINED if _client in _COMBINED_CLIENTS else _FMT_SPLIT
                        opts["extractor_args"] = {"youtube": {"player_client": [_client],
                                                               "skip": ["translated_subs"]}}
                        if _client in _UA: opts["http_headers"] = {"User-Agent": _UA[_client]}
                        try:
                            _extract(opts); return
                        except Exception as e:
                            if _is_cancelled(e): raise ValueError("USER_CANCELLED")
                            last_error = e
                            if _is_blocked(e) and _client != _YT_CLIENTS[-1]:
                                continue
                            raise
                    raise last_error or Exception("YouTube bloqueó todos los clientes.")
                else:
                    def _is_no_video(e):
                        s = str(e).lower()
                        return any(p in s for p in [
                            "there is no video in this post", "no video in this post",
                            "no video formats found", "no suitable formats",
                            "empty media response", "login required",
                            "rate-limit reached", "requested content is not available",
                            "this content is not available", "checkpoint required",
                            "cookies",
                        ])
                    try:
                        _extract(base_opts)
                    except Exception as _e:
                        if _is_cancelled(_e): raise ValueError("USER_CANCELLED")
                        if _is_no_video(_e): return
                        fallback = dict(base_opts)
                        fallback.pop("format", None)
                        try:
                            _extract(fallback)
                        except Exception as _e2:
                            if _is_cancelled(_e2): raise ValueError("USER_CANCELLED")
                            if _is_no_video(_e2): return
                            raise _e2

            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ [{make_bar(0)}] 0.00%\n"
                f"┊ Status   : Extracting...\n"
                f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
            await asyncio.to_thread(run_ydl)
            video_title = captured["title"]

            files = sorted(glob.glob(f"{DOWNLOAD_DIR}{task_id}_*"), key=os.path.getsize)

            # FALLBACK CAROUSEL / OG:IMAGE
            if not files:
                await safe_edit(msg,
                    f"╭ Task By → 「{uname}」\n┊ Status   : Buscando Medios de Respaldo...\n"
                    f"╰ Mode     : #UniversalFallback\n\n{BOT_SIGNATURE}")

                async def _download_media_url(h, media_url, idx, is_video=False):
                    try:
                        r = await h.get(media_url, headers=headers, timeout=30.0)
                        if r.status_code != 200 or len(r.content) < 1000: return None
                        ct  = r.headers.get("content-type", "").lower()
                        if is_video or "video" in ct: ext = ".mp4"
                        elif "webp" in ct: ext = ".webp"
                        elif "png" in ct:  ext = ".png"
                        elif "gif" in ct:  ext = ".gif"
                        else:
                            hd = r.content[:12]
                            if hd[:4] == b'RIFF' and hd[8:12] == b'WEBP': ext = ".webp"
                            elif hd[:8] == b'\x89PNG\r\n\x1a\n': ext = ".png"
                            elif hd[:3] == b'\xff\xd8\xff': ext = ".jpg"
                            else: ext = ".jpg"
                        raw = os.path.join(DOWNLOAD_DIR, f"{task_id}_fb{idx:02d}{ext}")
                        with open(raw, "wb") as f: f.write(r.content)
                        if ext not in (".jpg", ".mp4", ".gif"):
                            out = os.path.join(DOWNLOAD_DIR, f"{task_id}_fb{idx:02d}.jpg")
                            try:
                                proc = await asyncio.create_subprocess_exec(
                                    "ffmpeg", "-y", "-i", raw, "-q:v", "2", out,
                                    stdout=asyncio.subprocess.DEVNULL,
                                    stderr=asyncio.subprocess.DEVNULL,
                                )
                                await asyncio.wait_for(proc.wait(), timeout=30)
                                if os.path.exists(out) and os.path.getsize(out) > 0:
                                    try: os.remove(raw)
                                    except Exception: pass
                                    return out
                            except Exception: pass
                        return raw
                    except Exception:
                        return None

                try:
                    headers = {
                        "User-Agent": (
                            "Mozilla/5.0 (Linux; Android 12; SM-G991B) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Mobile Safari/537.36 Instagram/285.0.0.21.107"
                        ),
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as h:
                        resp      = await h.get(url, headers=headers)
                        page_text = resp.text
                        soup      = BeautifulSoup(page_text, "html.parser")
                        carousel_urls: list = []

                        if "instagram.com" in url:
                            def _walk(obj, depth=0):
                                if depth > 20 or not obj: return
                                if isinstance(obj, dict):
                                    vu = obj.get("video_url")
                                    if not vu and isinstance(obj.get("video_versions"), list):
                                        vu = obj["video_versions"][0].get("url") if obj["video_versions"] else None
                                    du = obj.get("display_url")
                                    if not du and isinstance(obj.get("image_versions2"), dict):
                                        cands = obj["image_versions2"].get("candidates", [{}])
                                        du = cands[0].get("url") if cands else None
                                    if vu:
                                        carousel_urls.append((vu.replace("\\u0026", "&"), True))
                                    elif du:
                                        carousel_urls.append((du.replace("\\u0026", "&"), False))
                                    else:
                                        edges = (obj.get("edge_sidecar_to_children") or {}).get("edges", [])
                                        for edge in edges:
                                            _walk(edge.get("node", {}), depth + 1)
                                        for v in obj.values():
                                            if isinstance(v, (dict, list)):
                                                _walk(v, depth + 1)
                                elif isinstance(obj, list):
                                    for item in obj:
                                        _walk(item, depth + 1)
                            for sc in soup.find_all("script", type="application/json"):
                                try:
                                    _walk(json.loads(sc.string or ""))
                                    if carousel_urls: break
                                except Exception: continue
                            if not carousel_urls:
                                vids = re.findall(r'"video_url"\s*:\s*"(https://[^"]+)"', page_text)
                                imgs = re.findall(r'"display_url"\s*:\s*"(https://[^"]+)"', page_text)
                                for v in vids:
                                    carousel_urls.append((v.replace("\\/", "/").replace("\\u0026", "&"), True))
                                for i in imgs:
                                    carousel_urls.append((i.replace("\\/", "/").replace("\\u0026", "&"), False))
                            seen, deduped = set(), []
                            for u2, iv in carousel_urls:
                                key = u2.split("?")[0]
                                if key not in seen:
                                    seen.add(key); deduped.append((u2, iv))
                            carousel_urls = deduped

                        if carousel_urls:
                            dl_tasks = [_download_media_url(h, mu, idx, iv)
                                        for idx, (mu, iv) in enumerate(carousel_urls[:20])]
                            results = await asyncio.gather(*dl_tasks)
                            files   = [p for p in results if p]
                            if soup.title: video_title = soup.title.string.strip()

                        if not files:
                            meta_img = (soup.find("meta", property="og:image") or
                                        soup.find("meta", attrs={"name": "twitter:image"}))
                            if meta_img and meta_img.get("content"):
                                img_url = meta_img["content"]
                                if "pinimg.com" in img_url:
                                    img_url = re.sub(r'/(236x|474x|736x)/', '/originals/', img_url)
                                p = await _download_media_url(h, img_url, 0)
                                if p:
                                    files = [p]
                                    if soup.title: video_title = soup.title.string.strip()
                except Exception:
                    pass

            if not files:
                raise Exception(
                    "No se pudo descargar. Posible bloqueo de IP, enlace privado o formato no soportado."
                )

            if len(files) > 1:
                total_files = len(files)
                await safe_edit(msg,
                    f"╭ Task By → 「{uname}」\n┊ 🗂️ Álbum detectado: {total_files} archivos\n"
                    f"┊ ⬆️ Preparando envío...\n╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
                from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
                album_caption = f"🖼️ <b>{video_title}</b>\n\n{BOT_SIGNATURE}" if video_title else BOT_SIGNATURE
                converted: list = []
                group = []
                for idx, f in enumerate(files):
                    fl  = f.lower()
                    cap = album_caption if idx == 0 else None
                    parse = enums.ParseMode.HTML if cap else None
                    if fl.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
                        photo_path = await _ensure_jpeg(f)
                        if photo_path != f: converted.append(photo_path)
                        group.append(InputMediaPhoto(photo_path, caption=cap, parse_mode=parse))
                    elif fl.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
                        group.append(InputMediaVideo(f, caption=cap, parse_mode=parse, supports_streaming=True))
                    elif fl.endswith(".gif"):
                        group.append(InputMediaDocument(f, caption=cap, parse_mode=parse))
                if group:
                    for i in range(0, len(group), 10):
                        batch = group[i:i+10]
                        await safe_edit(msg,
                            f"╭ Task By → 「{uname}」\n"
                            f"┊ ⬆️ Subiendo {i+1}–{min(i+10, total_files)} de {total_files}...\n"
                            f"╰ Mode     : {mode}\n\n{BOT_SIGNATURE}")
                        await client.send_media_group(message.chat.id, batch)
                _stats["downloads"] += 1
                await msg.delete()
                for f in files + converted:
                    try: os.remove(f)
                    except: pass
                return
            else:
                path = files[0]
                if want_subs:
                    for _sext in ("srt", "vtt", "ass", "ssa"):
                        _scands = glob.glob(f"{DOWNLOAD_DIR}{task_id}_*.{_sext}")
                        if _scands: sub_path = _scands[0]; break

        else:
            raise Exception("Enlace no soportado por el sistema.")

        # CODIFICACIÓN
        if path and os.path.exists(path):
            lower_path = path.lower()
            if lower_path.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov")):
                info      = await asyncio.to_thread(probe_video, path)
                is_h264   = info["codec"] == "h264"
                is_aac    = info["audio_codec"] in ("aac", "mp3", "mp4a")
                needs_enc = not (is_h264 and is_aac and lower_path.endswith(".mp4"))

                if want_subs and sub_path and os.path.exists(sub_path):
                    burn_sub = sub_path
                    if sub_path.endswith(".vtt"):
                        srt_path = sub_path[:-4] + ".srt"
                        vtt2srt  = await asyncio.create_subprocess_exec(
                            "ffmpeg", "-y", "-i", sub_path, srt_path,
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                        await vtt2srt.wait()
                        if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
                            burn_sub = srt_path
                    await safe_edit(msg,
                        f"╭ Task By → 「{uname}」\n┊ 🔤 Quemando subtítulos ES...\n"
                        f"╰ Mode     : #SubsMode\n\n{BOT_SIGNATURE}")
                    encoded_path = path + "_sub.mp4"
                    abs_sub = os.path.abspath(burn_sub)
                    sub_proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-i", path,
                        "-vf", f"subtitles={abs_sub}:charenc=UTF-8",
                        "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        encoded_path,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await sub_proc.wait()
                    if sub_proc.returncode == 0 and os.path.exists(encoded_path) and os.path.getsize(encoded_path) > 100_000:
                        try: os.remove(path)
                        except Exception: pass
                        path = encoded_path
                    else:
                        try:
                            if encoded_path and os.path.exists(encoded_path): os.remove(encoded_path)
                        except Exception: pass
                        encoded_path = None
                        await safe_edit(msg,
                            f"╭ Task By → 「{uname}」\n┊ ⚠️ Subtítulos ES no disponibles\n"
                            f"┊ ⬆️ Subiendo sin subtítulos...\n╰ Mode     : #SubsMode\n\n{BOT_SIGNATURE}")
                        if needs_enc:
                            encoded_path = path + "_out.mp4"
                            await safe_edit(msg, encoding_panel(uname, 0, 0, os.path.getsize(path), 0, 0, 0, task_id))
                            if await encode_video(path, encoded_path, msg, uname, task_id) and os.path.exists(encoded_path):
                                try: os.remove(path)
                                except Exception: pass
                                path = encoded_path
                            else:
                                if encoded_path and os.path.exists(encoded_path): os.remove(encoded_path)
                                encoded_path = None

                elif want_subs and not sub_path:
                    await safe_edit(msg,
                        f"╭ Task By → 「{uname}」\n┊ ⚠️ No hay subtítulos ES disponibles\n"
                        f"┊ ⬆️ Subiendo sin subtítulos...\n╰ Mode     : #SubsMode\n\n{BOT_SIGNATURE}")
                    if needs_enc:
                        encoded_path = path + "_out.mp4"
                        await safe_edit(msg, encoding_panel(uname, 0, 0, os.path.getsize(path), 0, 0, 0, task_id))
                        if await encode_video(path, encoded_path, msg, uname, task_id) and os.path.exists(encoded_path):
                            try: os.remove(path)
                            except Exception: pass
                            path = encoded_path
                        else:
                            if encoded_path and os.path.exists(encoded_path): os.remove(encoded_path)
                            encoded_path = None

                elif needs_enc:
                    encoded_path = path + "_out.mp4"
                    await safe_edit(msg, encoding_panel(uname, 0, 0, os.path.getsize(path), 0, 0, 0, task_id))
                    if await encode_video(path, encoded_path, msg, uname, task_id) and os.path.exists(encoded_path):
                        try: os.remove(path)
                        except Exception: pass
                        path = encoded_path
                    else:
                        if encoded_path and os.path.exists(encoded_path): os.remove(encoded_path)
                        encoded_path = None

        # SUBIDA
        if path and os.path.exists(path):
            await safe_edit(msg, upload_panel(uname, 0, 0, os.path.getsize(path), 0, 0, 0, task_id))
            await upload_smart_file(client, message, path, msg, uname, task_id, title=video_title)
            _stats["downloads"] += 1
            try: await msg.delete()
            except Exception: pass
        else:
            await safe_edit(msg, f"❌ No se encontró archivo descargado.\n\n{BOT_SIGNATURE}")

    except (asyncio.CancelledError, Exception) as e:
        is_cancel = isinstance(e, asyncio.CancelledError) or "USER_CANCELLED" in str(e)
        err       = "🛑 Descarga cancelada." if is_cancel else f"❌ Error: {str(e)[:200]}"
        try:
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ {err}\n╰──────────────\n\n{BOT_SIGNATURE}")
        except Exception: pass
        if not is_cancel:
            async def _del(m=msg):
                await asyncio.sleep(30)
                try: await m.delete()
                except Exception: pass
            asyncio.create_task(_del())

    finally:
        active_tasks.pop(task_id, None)
        _task_handles.pop(task_id, None)
        _ydl_stop.pop(task_id, None)
        for f in glob.glob(f"{DOWNLOAD_DIR}{task_id}_*"):
            try: os.remove(f)
            except: pass
        if sub_path and os.path.exists(sub_path):
            try: os.remove(sub_path)
            except: pass


# ─── AUDIO ────────────────────────────────────────────────────────────────────
async def procesar_audio(client: Client, message: Message, url: str, uname: str, task_id: str):
    msg = await message.reply_text(
        f"╭ Task By → 「{uname}」\n┊ 🎵 Iniciando descarga de audio...\n"
        f"╰ Mode     : #AudioMode\n\n{BOT_SIGNATURE}"
    )
    path = None; thumb_path = None
    try:
        active_tasks[task_id] = "RUNNING"
        loop     = asyncio.get_running_loop()
        start_t  = time.time()
        captured = {"title": "", "artist": "", "thumb_url": ""}

        def ydl_hook(d):
            if active_tasks.get(task_id) == "CANCELLED": raise ValueError("USER_CANCELLED")
            if d["status"] == "downloading":
                curr  = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                if total > 0:
                    asyncio.run_coroutine_threadsafe(
                        download_progress(curr, total, msg, start_t, uname, task_id, "yt-dlp", "#AudioMode"), loop)

        def run_download():
            opts = {
                "outtmpl": f"{DOWNLOAD_DIR}{task_id}_%(title)s.%(ext)s",
                "format": "bestaudio/best", "noplaylist": True,
                "quiet": True, "no_warnings": True, "writethumbnail": True,
                "progress_hooks": [ydl_hook],
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                    {"key": "FFmpegMetadata", "add_metadata": True},
                    {"key": "EmbedThumbnail"},
                ],
            }
            if os.path.exists("telegram-bot/cookies.txt"): opts["cookiefile"] = "telegram-bot/cookies.txt"
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    captured["title"]  = info.get("title", "")
                    captured["artist"] = info.get("uploader") or info.get("artist") or info.get("channel", "")

        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n┊ [{make_bar(0)}] 0.00%\n"
            f"┊ Status   : Descargando audio...\n╰ Mode     : #AudioMode\n\n{BOT_SIGNATURE}")
        await asyncio.to_thread(run_download)

        mp3_files = sorted(glob.glob(f"{DOWNLOAD_DIR}{task_id}_*.mp3"), key=os.path.getsize, reverse=True)
        if not mp3_files: raise Exception("No se pudo generar el audio.")
        path = mp3_files[0]

        for ext in ("jpg", "jpeg", "png", "webp"):
            cands = glob.glob(f"{DOWNLOAD_DIR}{task_id}_*.{ext}")
            if cands:
                raw_thumb  = cands[0]
                thumb_path = raw_thumb.rsplit(".", 1)[0] + "_thumb.jpg"
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-i", raw_thumb, "-q:v", "2",
                        "-vf", "scale=320:320:force_original_aspect_ratio=decrease", thumb_path,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await asyncio.wait_for(proc.wait(), timeout=20)
                    if not (os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0):
                        thumb_path = None
                except Exception: thumb_path = None
                try: os.remove(raw_thumb)
                except Exception: pass
                break

        title   = captured["title"] or os.path.basename(path)
        artist  = captured["artist"] or "Desconocido"
        caption = f"🎵 <b>{title}</b>\n👤 {artist}\n\n{BOT_SIGNATURE}"
        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n┊ ⬆️ Subiendo audio...\n"
            f"╰ Mode     : #AudioMode\n\n{BOT_SIGNATURE}")
        start_up = time.time()
        await client.send_audio(
            chat_id=message.chat.id, audio=path, thumb=thumb_path,
            title=title[:64], performer=artist[:64],
            caption=caption, parse_mode=enums.ParseMode.HTML,
            progress=upload_progress, progress_args=(msg, start_up, uname, task_id))
        _stats["downloads"] += 1
        try: await msg.delete()
        except Exception: pass

    except (Exception, asyncio.CancelledError) as e:
        is_cancel = isinstance(e, asyncio.CancelledError) or "USER_CANCELLED" in str(e)
        err = "🛑 Cancelado." if is_cancel else f"❌ Error: {str(e)[:200]}"
        try: await safe_edit(msg, f"╭ Task By → 「{uname}」\n┊ {err}\n╰──────────────\n\n{BOT_SIGNATURE}")
        except Exception: pass
    finally:
        active_tasks.pop(task_id, None)
        for f in glob.glob(f"{DOWNLOAD_DIR}{task_id}_*"):
            try: os.remove(f)
            except: pass


# ─── PLAYLIST ─────────────────────────────────────────────────────────────────
MAX_PLAYLIST_TRACKS = 50

async def procesar_playlist(client: Client, message: Message, url: str, uname: str, task_id: str):
    msg = await message.reply_text(
        f"╭ Task By → 「{uname}」\n┊ 📋 Extrayendo playlist...\n"
        f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}"
    )
    try:
        active_tasks[task_id] = "RUNNING"
        def extract_info_only():
            opts = {"quiet": True, "no_warnings": True,
                    "extract_flat": "in_playlist", "skip_download": True}
            if os.path.exists("telegram-bot/cookies.txt"): opts["cookiefile"] = "telegram-bot/cookies.txt"
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        info = await asyncio.to_thread(extract_info_only)
        if not info: raise Exception("No se pudo extraer la playlist.")
        entries = info.get("entries") or []
        if not entries:
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ ℹ️ Solo hay una pista, descargando como audio...\n"
                f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}")
            await procesar_audio(client, message, url, uname, task_id)
            try: await msg.delete()
            except Exception: pass
            return
        total          = min(len(entries), MAX_PLAYLIST_TRACKS)
        playlist_title = info.get("title") or "Playlist"
        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n┊ 📋 <b>{playlist_title[:60]}</b>\n"
            f"┊ 🎵 {total} pistas\n┊ ⏳ Iniciando...\n"
            f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}")
        sent = 0; errors = 0
        loop = asyncio.get_running_loop()
        for idx, entry in enumerate(entries[:MAX_PLAYLIST_TRACKS], start=1):
            if active_tasks.get(task_id) == "CANCELLED":
                raise asyncio.CancelledError("USER_CANCELLED")
            track_url  = entry.get("url") or entry.get("webpage_url") or entry.get("id")
            track_name = entry.get("title") or f"Pista {idx}"
            if not track_url: errors += 1; continue
            track_id = f"{task_id}_{idx:03d}"
            start_t  = time.time()
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n"
                f"┊ [{make_bar(int((idx-1)/total*100))}] {idx-1}/{total}\n"
                f"┊ 🎵 Descargando: <b>{track_name[:50]}</b>\n"
                f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}")
            captured: dict = {"title": "", "artist": ""}
            track_path = None; track_thumb = None
            def ydl_hook_pl(d, _tid=track_id):
                if active_tasks.get(task_id) == "CANCELLED": raise ValueError("USER_CANCELLED")
                if d["status"] == "downloading":
                    curr  = d.get("downloaded_bytes", 0)
                    total_b = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if total_b > 0:
                        asyncio.run_coroutine_threadsafe(
                            download_progress(curr, total_b, msg, start_t, uname, _tid, "yt-dlp", "#PlaylistMode"),
                            loop)
            def run_track(_url=track_url, _tid=track_id):
                opts = {
                    "outtmpl": f"{DOWNLOAD_DIR}{_tid}_%(title)s.%(ext)s",
                    "format": "bestaudio/best", "noplaylist": True,
                    "quiet": True, "no_warnings": True, "writethumbnail": True,
                    "progress_hooks": [ydl_hook_pl],
                    "postprocessors": [
                        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
                        {"key": "FFmpegMetadata", "add_metadata": True},
                        {"key": "EmbedThumbnail"},
                    ],
                }
                if os.path.exists("telegram-bot/cookies.txt"): opts["cookiefile"] = "telegram-bot/cookies.txt"
                with yt_dlp.YoutubeDL(opts) as ydl:
                    inf = ydl.extract_info(_url, download=True)
                    if inf:
                        captured["title"]  = inf.get("title", track_name)
                        captured["artist"] = inf.get("uploader") or inf.get("artist") or inf.get("channel", "")
            try:
                await asyncio.to_thread(run_track)
                mp3s = sorted(glob.glob(f"{DOWNLOAD_DIR}{track_id}_*.mp3"), key=os.path.getsize, reverse=True)
                if not mp3s: errors += 1; continue
                track_path = mp3s[0]
                for ext in ("jpg", "jpeg", "png", "webp"):
                    cands = glob.glob(f"{DOWNLOAD_DIR}{track_id}_*.{ext}")
                    if cands:
                        raw_t  = cands[0]
                        conv_t = raw_t.rsplit(".", 1)[0] + "_th.jpg"
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "ffmpeg", "-y", "-i", raw_t, "-q:v", "2",
                                "-vf", "scale=320:320:force_original_aspect_ratio=decrease", conv_t,
                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                            await asyncio.wait_for(proc.wait(), timeout=20)
                            if os.path.exists(conv_t) and os.path.getsize(conv_t) > 0: track_thumb = conv_t
                            try: os.remove(raw_t)
                            except Exception: pass
                        except Exception: pass
                        break
                title_s  = (captured["title"] or track_name)[:64]
                artist_s = (captured["artist"] or "Desconocido")[:64]
                cap_text = (f"🎵 <b>{title_s}</b>\n👤 {artist_s}\n"
                            f"📋 {playlist_title[:40]} — {idx}/{total}\n\n{BOT_SIGNATURE}")
                await safe_edit(msg,
                    f"╭ Task By → 「{uname}」\n"
                    f"┊ [{make_bar(int((idx-1)/total*100))}] {idx-1}/{total}\n"
                    f"┊ ⬆️ Subiendo: <b>{title_s[:40]}</b>\n"
                    f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}")
                await client.send_audio(chat_id=message.chat.id, audio=track_path,
                    thumb=track_thumb, title=title_s, performer=artist_s,
                    caption=cap_text, parse_mode=enums.ParseMode.HTML)
                sent += 1
            except (asyncio.CancelledError, ValueError) as e:
                if "USER_CANCELLED" in str(e) or isinstance(e, asyncio.CancelledError):
                    raise asyncio.CancelledError("USER_CANCELLED")
                errors += 1
            except Exception: errors += 1
            finally:
                for f in glob.glob(f"{DOWNLOAD_DIR}{track_id}_*"):
                    try: os.remove(f)
                    except: pass
                if track_thumb and os.path.exists(track_thumb):
                    try: os.remove(track_thumb)
                    except: pass
        _stats["downloads"] += sent
        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n┊ ✅ Playlist completada\n"
            f"┊ 🎵 Enviadas : {sent}/{total}\n┊ ❌ Errores  : {errors}\n"
            f"╰ Mode     : #PlaylistMode\n\n{BOT_SIGNATURE}")
        await asyncio.sleep(5)
        try: await msg.delete()
        except Exception: pass
    except (Exception, asyncio.CancelledError) as e:
        is_cancel = isinstance(e, asyncio.CancelledError) or "USER_CANCELLED" in str(e)
        err = "🛑 Playlist cancelada." if is_cancel else f"❌ Error: {str(e)[:200]}"
        try: await safe_edit(msg, f"╭ Task By → 「{uname}」\n┊ {err}\n╰──────────────\n\n{BOT_SIGNATURE}")
        except Exception: pass
    finally:
        active_tasks.pop(task_id, None)
        for f in glob.glob(f"{DOWNLOAD_DIR}{task_id}_*"):
            try: os.remove(f)
            except: pass


# ─── TORRENT ──────────────────────────────────────────────────────────────────
_TORRENT_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".flv", ".webm"}

def _parse_aria2c_files(text: str) -> list:
    files = []; current_idx = None; current_path = None; current_size = "?"
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r"^(\d+)\|(.*)", stripped)
        if m:
            if current_idx is not None and current_path:
                ext = os.path.splitext(current_path)[1].lower()
                if ext in _TORRENT_VIDEO_EXTS:
                    files.append((current_idx, os.path.basename(current_path), current_size))
            current_idx = int(m.group(1)); current_path = m.group(2).strip().rstrip("/"); current_size = "?"
        elif "Length:" in stripped:
            m2 = re.search(r"\(([^)]+)\)", stripped)
            if m2: current_size = m2.group(1)
    if current_idx is not None and current_path:
        ext = os.path.splitext(current_path)[1].lower()
        if ext in _TORRENT_VIDEO_EXTS:
            files.append((current_idx, os.path.basename(current_path), current_size))
    return files

async def _fetch_aria2c_file_list(torrent_path: str) -> list:
    try:
        proc = await asyncio.create_subprocess_exec(
            "aria2c", "--show-files", torrent_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return _parse_aria2c_files(stdout.decode("utf-8", errors="replace"))
    except Exception:
        return []

def _torrent_selection_kb(files: list, uid: int, task_id: str):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for file_idx, fname, size in files[:20]:
        label = f"📺 {fname[:38]} ({size})"
        rows.append([InlineKeyboardButton(label, callback_data=f"tr_sel:{uid}:{task_id}:{file_idx}")])
    rows.append([
        InlineKeyboardButton("📦 Todos",    callback_data=f"tr_all:{uid}:{task_id}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"tr_cxl:{uid}:{task_id}"),
    ])
    return InlineKeyboardMarkup(rows)

async def _torrent_encode_and_send(client: Client, message: Message, msg,
                                    uname: str, task_id: str, dl_dir: str,
                                    select_indices=None, want_subs: bool = False):
    import shutil
    encoded_path = None; input_path = None
    try:
        video_files = []
        for root, _, files in os.walk(dl_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in _TORRENT_VIDEO_EXTS and not f.endswith(".aria2"):
                    fp = os.path.join(root, f)
                    video_files.append((os.path.getsize(fp), fp))
        if not video_files: raise Exception("No se encontró archivo de video en el torrent.")
        video_files.sort(reverse=True)
        input_path = video_files[0][1]
        input_name = os.path.splitext(os.path.basename(input_path))[0]
        size_gb    = video_files[0][0] / 1024 ** 3
        encoded_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_out.mp4")
        sub_path = None

        if want_subs:
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ 🔍 Buscando subtítulos...\n"
                f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
            for root, _, files in os.walk(dl_dir):
                for f in files:
                    if f.lower().endswith((".srt", ".vtt", ".ass", ".ssa")):
                        sub_path = os.path.join(root, f); break
                if sub_path: break
            if not sub_path:
                idx = await asyncio.to_thread(get_spanish_sub_index, input_path)
                if idx is not None:
                    extracted_sub = os.path.join(DOWNLOAD_DIR, f"{task_id}_extracted.srt")
                    ext_proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-i", input_path, "-map", f"0:{idx}",
                        "-c:s", "srt", extracted_sub,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                    await ext_proc.wait()
                    if os.path.exists(extracted_sub) and os.path.getsize(extracted_sub) > 0:
                        sub_path = extracted_sub

        needs_standard_encode = True
        if sub_path:
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ 🔤 Quemando subtítulos ES...\n"
                f"┊ 🎬 {input_name[:40]}\n╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
            abs_sub  = os.path.abspath(sub_path)
            sub_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path,
                "-vf", f"subtitles={abs_sub}:charenc=UTF-8",
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", encoded_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await sub_proc.wait()
            if sub_proc.returncode == 0 and os.path.exists(encoded_path):
                needs_standard_encode = False
            else:
                if os.path.exists(encoded_path): os.remove(encoded_path)
                sub_path = None

        if needs_standard_encode:
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ 🎬 {input_name[:50]}\n"
                f"┊ 📦 Tamaño : {size_gb:.2f} GB\n┊ ⚙️ Convirtiendo a MP4...\n"
                f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
            copy_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", input_path, "-c:v", "copy", "-c:a", "copy",
                "-movflags", "+faststart", encoded_path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await copy_proc.wait()
            copy_ok = (copy_proc.returncode == 0 and os.path.exists(encoded_path)
                       and os.path.getsize(encoded_path) > 1024 * 1024)
            if not copy_ok:
                await safe_edit(msg,
                    f"╭ Task By → 「{uname}」\n┊ ⚙️ Re-encodando H264...\n"
                    f"┊ 📁 {input_name[:50]}\n╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
                enc_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", input_path,
                    "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", encoded_path,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await enc_proc.wait()
                if enc_proc.returncode != 0 or not os.path.exists(encoded_path):
                    raise Exception("ffmpeg no pudo convertir el video.")

        final_size = os.path.getsize(encoded_path) / 1024 ** 3
        await safe_edit(msg,
            f"╭ Task By → 「{uname}」\n┊ ⬆️ Subiendo MP4 ({final_size:.2f} GB)...\n"
            f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
        await upload_smart_file(client, message, encoded_path, msg, uname, task_id, title=input_name)
        _stats["downloads"] += 1
        try: await msg.delete()
        except Exception: pass
    finally:
        shutil.rmtree(dl_dir, ignore_errors=True)
        if encoded_path and os.path.exists(encoded_path):
            try: os.remove(encoded_path)
            except Exception: pass
        temp_sub = os.path.join(DOWNLOAD_DIR, f"{task_id}_extracted.srt")
        if os.path.exists(temp_sub):
            try: os.remove(temp_sub)
            except Exception: pass

async def _run_torrent_download(client, message, msg, uname, task_id, source, dl_dir, select_indices=None):
    cmd = ["aria2c", "--dir", dl_dir, "--seed-time=0",
           "--max-connection-per-server=16", "--split=16",
           "--bt-stop-timeout=600", "--summary-interval=0",
           "--console-log-level=warn", "--file-allocation=none", "--continue=true"]
    if select_indices:
        cmd.append(f"--select-file={','.join(str(i) for i in select_indices)}")
    cmd.append(source)
    proc    = await asyncio.create_subprocess_exec(*cmd,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    start_t = time.time(); last_edit = 0.0

    async def _monitor():
        nonlocal last_edit
        while proc.returncode is None:
            if active_tasks.get(task_id) == "CANCELLED":
                proc.terminate(); return
            try:
                total_dl = sum(
                    os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(dl_dir)
                    for f in fs if not f.endswith(".aria2") and not f.endswith(".torrent")
                )
                if time.time() - last_edit > 8:
                    elapsed = time.time() - start_t
                    await safe_edit(msg,
                        f"╭ Task By → 「{uname}」\n┊ 🧲 Descargando torrent...\n"
                        f"┊ 📦 Descargado : {get_readable_size(total_dl)}\n"
                        f"┊ ⏱️ Tiempo     : {get_readable_time(int(elapsed))}\n"
                        f"┊ ⚠️ /cancel para detener\n"
                        f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
                    last_edit = time.time()
            except Exception: pass
            await asyncio.sleep(5)

    monitor_task = asyncio.create_task(_monitor())
    await proc.wait()
    monitor_task.cancel()
    if active_tasks.get(task_id) == "CANCELLED":
        raise asyncio.CancelledError("USER_CANCELLED")
    if proc.returncode != 0:
        raise Exception("aria2c terminó con error.")

async def procesar_torrent(client: Client, message: Message, source: str,
                            uname: str, task_id: str, is_magnet: bool = True, want_subs: bool = False):
    import shutil
    uid      = message.from_user.id
    dl_dir   = os.path.join(DOWNLOAD_DIR, task_id)
    meta_dir = os.path.join(DOWNLOAD_DIR, f"{task_id}_meta")
    encoded_path = None
    msg = await message.reply_text(
        f"╭ Task By → 「{uname}」\n┊ 🧲 Obteniendo información del torrent...\n"
        f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
    try:
        active_tasks[task_id] = "RUNNING"
        os.makedirs(dl_dir, exist_ok=True)
        torrent_path = source
        if is_magnet:
            os.makedirs(meta_dir, exist_ok=True)
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n┊ 🔍 Resolviendo magnet (hasta 90s)...\n"
                f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}")
            meta_proc = await asyncio.create_subprocess_exec(
                "aria2c", "--dir", meta_dir, "--bt-metadata-only",
                "--bt-save-metadata", "--seed-time=0", "--bt-stop-timeout=90",
                "--summary-interval=0", "--console-log-level=warn", source,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            try: await asyncio.wait_for(meta_proc.wait(), timeout=120)
            except asyncio.TimeoutError: meta_proc.terminate()
            found = glob.glob(os.path.join(meta_dir, "*.torrent"))
            if found: torrent_path = found[0]
        file_list = []
        if os.path.isfile(torrent_path):
            file_list = await _fetch_aria2c_file_list(torrent_path)
        if len(file_list) > 1:
            _torrent_sessions[str(uid)] = {
                "source": source if is_magnet else torrent_path,
                "torrent_path": torrent_path, "dl_dir": dl_dir,
                "meta_dir": meta_dir, "task_id": task_id, "uname": uname,
                "chat_id": message.chat.id, "files": file_list,
                "msg_id": msg.id, "want_subs": want_subs,
            }
            kb = _torrent_selection_kb(file_list, uid, task_id)
            listed = "\n".join(f"  {i+1}. 📺 {fname} ({size})"
                               for i, (_, fname, size) in enumerate(file_list[:20]))
            await safe_edit(msg,
                f"╭ Task By → 「{uname}」\n"
                f"┊ 🗂️ {len(file_list)} episodios encontrados:\n┊\n"
                f"{''.join(f'┊{l}{chr(10)}' for l in listed.splitlines())}"
                f"┊\n┊ Selecciona cuál descargar:\n"
                f"╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}",
                reply_markup=kb)
            return
        await _run_torrent_download(client, message, msg, uname, task_id, source, dl_dir)
        await _torrent_encode_and_send(client, message, msg, uname, task_id, dl_dir, want_subs=want_subs)
    except (Exception, asyncio.CancelledError) as e:
        is_cancel = isinstance(e, asyncio.CancelledError) or "USER_CANCELLED" in str(e)
        err = "🛑 Descarga cancelada." if is_cancel else f"❌ Error: {str(e)[:200]}"
        try: await safe_edit(msg, f"╭ Task By → 「{uname}」\n┊ {err}\n╰──────────────\n\n{BOT_SIGNATURE}", reply_markup=None)
        except Exception: pass
    finally:
        active_tasks.pop(task_id, None)
        _torrent_sessions.pop(str(uid), None)
        shutil.rmtree(meta_dir, ignore_errors=True)


# ─── COLA DE TRABAJO ──────────────────────────────────────────────────────────
async def queue_worker():
    print("[worker] Cola iniciada, esperando tareas...")
    while True:
        item = await download_queue.get()
        client, message, url, uname, uid, label = item[:6]
        want_subs = item[6] if len(item) > 6 else False
        print(f"[worker] Procesando: {url[:60]} para {uname}")
        try:
            await procesar_descarga(client, message, url, uname, uid, label, want_subs=want_subs)
        except Exception as e:
            print(f"[worker] error: {e}")
        finally:
            download_queue.task_done()


# ─── CLIENTE BOT ──────────────────────────────────────────────────────────────
bot = Client("bot_session", api_id=API_ID, api_hash=API_HASH,
             bot_token=BOT_TOKEN, workdir="/tmp")


# ─── COMANDOS ─────────────────────────────────────────────────────────────────
@bot.on_message(filters.command("coms"))
async def cmd_coms(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    await message.reply_text(
        "📋 Comandos disponibles:\n\n"
        "🔓 Usuarios autorizados:\n"
        "• Envía un link para descargar\n"
        "• Envía un video para agregar marca de agua\n"
        "• /start — Iniciar el bot\n"
        "• /ping — Ver latencia\n"
        "• /queue — Ver cola de descargas\n"
        "• /audio <link> — Descargar audio MP3\n"
        "• /playlist <link> — Descargar playlist completa\n"
        "• /encode <magnet> — Descargar torrent y convertir a MP4\n"
        "• /cancel — Cancelar descarga activa\n\n"
        "🔐 Solo administradores:\n"
        "• /id — Autorizar usuario (responde su mensaje)\n"
        "• /addid <ID> — Autorizar usuario por ID directo\n"
        "• /rmid <ID> — Quitar autorización por ID\n"
        "• /users — Ver usuarios autorizados\n"
        "• /stat — Ver estadísticas del servidor\n"
        "• /reset — Cancelar todo y limpiar\n"
        "• /admin — Dar rango admin\n"
        "• /remadmin — Quitar rango admin\n"
        "• /cancelarID — Quitar autorización (responde su mensaje)\n"
        "• /getcode — Obtener código fuente en .txt\n"
        "• /coms — Ver esta lista\n\n"
        f"{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command("start"))
async def cmd_start(client: Client, message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name
    auth_status = "✅ Autorizado" if is_auth(uid) else "⛔ No Autorizado"
    await message.reply_text(
        f"🚀 ¡Hola, {name}!\n\n"
        f"Soy un bot descargador de medios. Envíame un enlace de:\n"
        f"YouTube • TikTok • Instagram • Twitter/X • Facebook\n"
        f"Pinterest • Mega.nz • Mediafire • y más 💾\n\n"
        f"🆔 **Tu ID:** `{uid}`\n"
        f"🔐 **Estado:** {auth_status}\n\n"
        f"*(Si no estás autorizado, pídele al admin tu ID)*\n\n"
        f"{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command(["getcode", "codigo", "code"]))
async def cmd_getcode(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    code_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    try:
        await message.reply_document(
            document=code_path,
            file_name="main.py.txt",
            caption=f"📄 Código fuente del bot\n\n{BOT_SIGNATURE}"
        )
    except Exception as e:
        await message.reply_text(f"❌ Error al enviar el código: {e}")

@bot.on_message(filters.command(["stat", "Stat", "STAT"]))
async def cmd_stat(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    uptime   = get_readable_time(time.time() - start_time)
    ram      = psutil.virtual_memory()
    disk     = psutil.disk_usage("/tmp")
    cpu      = psutil.cpu_percent(interval=0.5)
    server   = platform.node()
    plat     = platform.system() + " " + platform.release()
    cpu_info = platform.processor() or platform.machine()
    active   = len(active_tasks)
    in_queue = download_queue.qsize()
    await message.reply_text(
        f"╭─ Status Panel\n"
        f"┊ 🕐 Time on  : {uptime}\n"
        f"┊ 🧠 RAM      : {ram.percent:.1f}% ({get_readable_size(ram.used)}/{get_readable_size(ram.total)})\n"
        f"┊ 💾 Storage  : {get_readable_size(disk.used)}/{get_readable_size(disk.total)}\n"
        f"┊ 🖥️ Server   : {server}\n"
        f"┊ ⚙️ Platform : {plat}\n"
        f"┊ 🔧 CPU      : {cpu}% — {cpu_info}\n"
        f"┊ ⚡ Activas  : {active} | En cola: {in_queue}\n"
        f"┊ 📥 Descargas: {_stats['downloads']} (sesión)\n"
        f"╰─ Engine    : yt-dlp / Pyrogram\n\n"
        f"{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command(["ping", "Ping"]))
async def cmd_ping(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    t1 = time.time()
    m  = await message.reply_text("🏓 Calculando latencia...")
    latency = (time.time() - t1) * 1000
    await m.edit_text(
        f"╭─ Ping\n┊ 🏓 Pong!\n┊ ⚡ Latencia : {latency:.0f} ms\n"
        f"╰─ Estado    : Online ✅\n\n{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command(["audio", "Audio", "mp3"]))
async def cmd_audio(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    uid   = message.from_user.id
    uname = message.from_user.username or message.from_user.first_name or str(uid)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].startswith("http"):
        await message.reply_text(
            f"╭─ Uso correcto:\n┊ /audio <enlace>\n┊\n"
            f"┊ Ejemplo:\n┊ /audio https://youtu.be/...\n"
            f"╰─ Soporta YouTube, SoundCloud y más\n\n{BOT_SIGNATURE}"
        )
        return
    task_id = f"{uid}_{int(time.time())}"
    asyncio.create_task(procesar_audio(client, message, parts[1].strip(), uname, task_id))

@bot.on_message(filters.command(["playlist", "Playlist", "pl"]))
async def cmd_playlist(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    uid   = message.from_user.id
    uname = message.from_user.username or message.from_user.first_name or str(uid)
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].startswith("http"):
        await message.reply_text(
            f"╭─ Uso correcto:\n┊ /playlist <enlace>\n┊\n"
            f"┊ Ejemplo:\n┊ /playlist https://youtube.com/playlist?list=...\n"
            f"╰─ Máx. {MAX_PLAYLIST_TRACKS} pistas\n\n{BOT_SIGNATURE}"
        )
        return
    task_id = f"{uid}_{int(time.time())}"
    asyncio.create_task(procesar_playlist(client, message, parts[1].strip(), uname, task_id))

@bot.on_message(filters.command(["queue", "Queue", "cola"]))
async def cmd_queue(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    active   = len(active_tasks)
    in_queue = download_queue.qsize()
    status   = "✅ Sin tareas pendientes" if active == 0 and in_queue == 0 else f"⚡ {active} activa(s), ⏳ {in_queue} en espera"
    await message.reply_text(
        f"╭─ Cola de Descargas\n┊ {status}\n"
        f"┊ 📥 Total sesión: {_stats['downloads']}\n"
        f"╰──────────────────\n\n{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command(["encode", "Encode", "torrent"]))
async def cmd_encode(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    uid       = message.from_user.id
    uname     = message.from_user.username or message.from_user.first_name or str(uid)
    want_subs = bool(re.search(r"-lat", message.text or "", re.IGNORECASE))

    # Respondiendo a un archivo de video
    if message.reply_to_message:
        reply = message.reply_to_message
        # Archivo de video
        if reply.video or (reply.document and (reply.document.mime_type or "").startswith("video/")):
            file_id   = reply.video.file_id if reply.video else reply.document.file_id
            file_name = (reply.video.file_name if reply.video else reply.document.file_name) or "video"
            asyncio.create_task(procesar_encode(client, message, file_id, uname, uid, want_subs, original_name=file_name))
            return
        # Archivo .torrent
        if reply.document and (reply.document.file_name or "").lower().endswith(".torrent"):
            task_id      = f"{uid}_{int(time.time())}"
            torrent_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.torrent")
            await message.reply_text("🧲 Procesando archivo .torrent...")
            await client.download_media(reply, file_name=torrent_path)
            asyncio.create_task(procesar_torrent(client, message, torrent_path, uname, task_id,
                                                  is_magnet=False, want_subs=want_subs))
            return

    # Argumento: magnet link
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "╭ **Uso correcto de /encode**\n┊\n"
            "┊ **Convertir .mkv a MP4:**\n"
            "┊ Responde al archivo con `/encode` o `/encode -lat`\n┊\n"
            "┊ **Descargar Torrent:**\n"
            "┊ `/encode <magnet link>`\n"
            "┊ `/encode magnet:?xt=urn:btih:... -lat`\n┊\n"
            "┊ `-lat` = Quemar subtítulos español\n"
            f"╰──────────────────\n\n{BOT_SIGNATURE}"
        )
        return

    arg_text = re.sub(r"\s*-lat\s*", " ", parts[1], flags=re.IGNORECASE).strip()
    src_match = re.search(r'magnet:\?[^\s]+|https?://[^\s]+', arg_text)
    if not src_match:
        await message.reply_text(f"⚠️ El enlace debe ser un magnet link o URL de torrent.\n\n{BOT_SIGNATURE}")
        return
    source  = src_match.group(0)
    task_id = f"{uid}_{int(time.time())}"
    asyncio.create_task(procesar_torrent(client, message, source, uname, task_id,
                                          is_magnet=source.startswith("magnet:"), want_subs=want_subs))


# ─── CANCELACIONES ────────────────────────────────────────────────────────────
@bot.on_message(filters.regex(r"^/cancel_([0-9]+_[0-9]+)"))
async def cmd_cancel_id(client: Client, message: Message):
    if not is_auth(message.from_user.id): return
    task_id = message.matches[0].group(1)
    if task_id in active_tasks:
        active_tasks[task_id] = "CANCELLED"
        if task_id in _ydl_stop: _ydl_stop[task_id].set()
        handle = _task_handles.get(task_id)
        if handle and not handle.done(): handle.cancel()
        await message.reply_text("🛑 Deteniendo proceso de inmediato...")
    else:
        await message.reply_text("⚠️ No hay ninguna tarea activa con ese ID.")

@bot.on_message(filters.command(["cancel", "cancelar"]))
async def cmd_cancel_global(client: Client, message: Message):
    uid = message.from_user.id
    if not is_auth(uid): return
    canceled_any = False
    for tid in list(active_tasks.keys()):
        if tid.startswith(f"{uid}_"):
            active_tasks[tid] = "CANCELLED"
            if tid in _ydl_stop: _ydl_stop[tid].set()
            handle = _task_handles.get(tid)
            if handle and not handle.done(): handle.cancel()
            canceled_any = True
    if canceled_any:
        await message.reply_text("🛑 Todas tus descargas han sido canceladas.")
    else:
        await message.reply_text("⚠️ No tienes tareas activas en este momento.")


# ─── ADMINISTRACIÓN ───────────────────────────────────────────────────────────
@bot.on_message(filters.command("id") & filters.reply)
async def cmd_auth_user(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    target = message.reply_to_message.from_user
    if target:
        authorized_users[str(target.id)] = {
            "role": "user", "username": target.username or "", "name": target.first_name or ""
        }
        save_auth_users()
        await message.reply_text(
            f"✅ **Acceso concedido.**\n"
            f"El usuario [{target.first_name}](tg://user?id={target.id}) (`{target.id}`) ahora puede usar el bot."
        )

@bot.on_message(filters.command(["addid", "Addid"]))
async def cmd_add_by_id(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text(
            f"╭─ Uso correcto:\n┊ /addid <ID numérico>\n"
            f"╰─ Ejemplo: `/addid 7666238795`\n\n{BOT_SIGNATURE}"
        )
        return
    new_uid = parts[1]
    authorized_users[new_uid] = {"role": "user", "username": "", "name": ""}
    save_auth_users()
    await message.reply_text(
        f"✅ **ID `{new_uid}` autorizado.**\n"
        f"Ese usuario ya puede usar el bot.\n\n{BOT_SIGNATURE}"
    )

@bot.on_message(filters.command(["removebyid", "rmid"]))
async def cmd_remove_by_id(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.strip().split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply_text(f"╭─ Uso: /rmid <ID numérico>\n╰─ Ejemplo: `/rmid 7666238795`\n\n{BOT_SIGNATURE}")
        return
    uid_str = parts[1]
    if uid_str in authorized_users:
        del authorized_users[uid_str]
        save_auth_users()
        await message.reply_text(f"❌ ID `{uid_str}` eliminado de la lista.\n\n{BOT_SIGNATURE}")
    else:
        await message.reply_text(f"⚠️ El ID `{uid_str}` no estaba en la lista.")

@bot.on_message(filters.command(["cancelarID", "cancelarid", "Removeid", "removeid"]) & filters.reply)
async def cmd_remove_auth(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    target = message.reply_to_message.from_user
    if target and str(target.id) in authorized_users:
        del authorized_users[str(target.id)]
        save_auth_users()
        await message.reply_text(
            f"❌ **Acceso revocado.**\n"
            f"El usuario [{target.first_name}](tg://user?id={target.id}) (`{target.id}`) ya no tiene permiso."
        )

@bot.on_message(filters.command("admin") & filters.reply)
async def cmd_set_admin(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    target = message.reply_to_message.from_user
    if target:
        authorized_users[str(target.id)] = {
            "role": "admin", "username": target.username or "", "name": target.first_name or ""
        }
        save_auth_users()
        await message.reply_text(f"🛡 **Rango Admin otorgado** a [{target.first_name}](tg://user?id={target.id}).")

@bot.on_message(filters.command("remadmin") & filters.reply)
async def cmd_rem_admin(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    target = message.reply_to_message.from_user
    if target and str(target.id) in authorized_users:
        authorized_users[str(target.id)]["role"] = "user"
        save_auth_users()
        await message.reply_text(f"👤 **Rango Admin retirado** a [{target.first_name}](tg://user?id={target.id}).")

@bot.on_message(filters.command("users"))
async def cmd_users(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    text = "╭─ Usuarios autorizados\n┊ 👑 Owners : @The_canst, @Ryota_YT\n"
    count = 1
    for str_uid, info in authorized_users.items():
        role_icon    = " 🛡 admin" if info.get("role") == "admin" else ""
        username     = info.get("username")
        display_name = f"@{username}" if username else f"id:{str_uid}"
        text += f"┊ {count}. {display_name} ({str_uid}){role_icon}\n"
        count += 1
    text += f"╰─ Total : {count - 1}\n\n{BOT_SIGNATURE}"
    await message.reply_text(text)

@bot.on_message(filters.command(["reset", "Reset", "RESET"]))
async def cmd_reset(client: Client, message: Message):
    if not is_admin(message.from_user.id): return
    msg = await message.reply_text("♻️ Reiniciando...")
    cancelled = len(active_tasks)
    for tid in list(active_tasks.keys()):
        active_tasks[tid] = "CANCELLED"
    freed = 0
    for f in glob.glob(f"{DOWNLOAD_DIR}*"):
        try:
            freed += os.path.getsize(f)
            os.remove(f)
        except Exception: pass
    gc.collect()
    await msg.edit_text(
        f"╭─「 Reset Completado ✅ 」\n"
        f"┊ 🔄 Estado    : Online\n"
        f"┊ 🕐 Uptime    : 0s\n"
        f"┊ 🧹 Liberado  : {get_readable_size(freed)}\n"
        f"┊ ⛔ Descargas : {'Canceladas' if cancelled > 0 else 'Ninguna activa'}\n"
        f"╰─ Engine      : CRDWV2\n\n"
        f"{BOT_SIGNATURE}"
    )


# ─── MARCA DE AGUA ────────────────────────────────────────────────────────────
WM_POS_LABELS = {
    "topleft":  "↖ Arriba Izq",
    "topright": "↗ Arriba Der",
    "center":   "✦ Centro",
    "botleft":  "↙ Abajo Izq",
    "botright": "↘ Abajo Der",
}
WM_POS_FFMPEG = {
    "topleft":  ("10", "10"),
    "topright": ("w-text_w-10", "10"),
    "center":   ("(w-text_w)/2", "(h-text_h)/2"),
    "botleft":  ("10", "h-text_h-20"),
    "botright": ("w-text_w-10", "h-text_h-20"),
}

def _wm_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")

def apply_watermark(input_path: str, output_path: str, text: str, pos: str,
                    outline: bool, size_pct: int, stop_evt=None) -> None:
    meta     = get_video_meta(input_path)
    height   = meta.get("height") or 720
    width    = meta.get("width") or 1280
    scale_filter = "scale='min(1280,iw)':-2" if max(width, height) > 1280 else "null"
    fontsize = max(14, int(height * 0.08 * (size_pct / 100)))
    x, y     = WM_POS_FFMPEG[pos]
    safe_txt = _wm_escape(text)
    vf       = (f"{scale_filter},"
                f"drawtext=text='{safe_txt}':fontsize={fontsize}:fontcolor=white:x={x}:y={y}")
    if outline: vf += ":borderw=3:bordercolor=black@0.85"
    else:       vf += ":shadowx=2:shadowy=2:shadowcolor=black@0.6"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
           "-i", input_path, "-vf", vf,
           "-map", "0:v:0", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
           "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart", "-y", output_path]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    while True:
        if stop_evt is not None and stop_evt.is_set():
            proc.kill(); raise Exception("USER_CANCELLED")
        rc = proc.poll()
        if rc is not None:
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            if rc != 0: raise Exception(f"ffmpeg: {err[-400:]}")
            return
        time.sleep(1)

def estimate_wm_time(video_path: str) -> int:
    meta = get_video_meta(video_path)
    dur  = max(1, meta.get("duration", 10))
    return min(300, max(5, int(dur * 0.08)))

def _wm_progress_text(sess: dict, phase: str, remaining: int) -> str:
    timer          = get_readable_time(max(0, remaining))
    source_caption = (sess.get("caption", "") or "").strip()
    source_line    = f"┊ <b>{source_caption[:80]}</b>\n" if source_caption else ""
    return (
        f"╭─「 💧 Marca de Agua 」\n{source_line}"
        f"{_wm_summary(sess)}"
        f"┊\n┊ {phase}\n┊ ⏳ Tiempo restante: <b>~{timer}</b>\n"
        f"╰──────────────────────────\n\n{BOT_SIGNATURE}"
    )

def _wm_summary(sess: dict) -> str:
    pos_label     = WM_POS_LABELS.get(sess.get("pos", "center"), "—")
    outline_label = "Con Contorno ✅" if sess.get("outline") else "Sin Contorno 🚫"
    size          = sess.get("size", 50)
    txt           = (sess.get("text", "") or "")[:40]
    return (f"┊ Texto    : <b>{txt}</b>\n┊ Posición : <b>{pos_label}</b>\n"
            f"┊ Contorno : <b>{outline_label}</b>\n┊ Tamaño   : <b>{size}%</b>\n")

def _wm_kb_pos():
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↖ Arriba Izq", callback_data="wm_pos:topleft"),
         InlineKeyboardButton("↗ Arriba Der", callback_data="wm_pos:topright")],
        [InlineKeyboardButton("✦ Centro",      callback_data="wm_pos:center")],
        [InlineKeyboardButton("↙ Abajo Izq",  callback_data="wm_pos:botleft"),
         InlineKeyboardButton("↘ Abajo Der",  callback_data="wm_pos:botright")],
        [InlineKeyboardButton("❌ Cancelar",   callback_data="wm_cancel")],
    ])

def _wm_kb_outline():
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Con Contorno", callback_data="wm_outline:yes"),
         InlineKeyboardButton("🚫 Sin Contorno", callback_data="wm_outline:no")],
        [InlineKeyboardButton("❌ Cancelar",      callback_data="wm_cancel")],
    ])

def _wm_kb_size():
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔹 25%",  callback_data="wm_size:25"),
         InlineKeyboardButton("🔸 50%",  callback_data="wm_size:50"),
         InlineKeyboardButton("🔶 75%",  callback_data="wm_size:75"),
         InlineKeyboardButton("🔴 100%", callback_data="wm_size:100")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="wm_cancel")],
    ])

@bot.on_message(filters.video | (filters.document & ~filters.forwarded))
async def handle_video_upload(client: Client, message: Message):
    uid = message.from_user.id
    if not is_auth(uid): return

    # Archivo .torrent enviado directamente
    if message.document:
        fname = (message.document.file_name or "").lower()
        mime  = message.document.mime_type or ""
        if fname.endswith(".torrent") or mime == "application/x-bittorrent":
            uname        = message.from_user.username or message.from_user.first_name or str(uid)
            task_id      = f"{uid}_{int(time.time())}"
            torrent_path = os.path.join(DOWNLOAD_DIR, f"{task_id}.torrent")
            caption_text = message.caption or ""
            want_subs    = bool(re.search(r"(?:^|\s)-lat(?:\s|$)", caption_text, re.IGNORECASE))
            await message.reply_text(
                f"╭─「 🧲 Torrent detectado 」\n┊ 📄 {message.document.file_name}\n"
                f"┊ ⏳ Descargando y procesando...\n"
                f"╰─ Usa /cancel para detener\n\n{BOT_SIGNATURE}")
            await client.download_media(message, file_name=torrent_path)
            asyncio.create_task(procesar_torrent(client, message, torrent_path, uname, task_id,
                                                  is_magnet=False, want_subs=want_subs))
            return

    if message.video:
        file_id = message.video.file_id
    elif message.document:
        mime = message.document.mime_type or ""
        if not mime.startswith("video/"): return
        file_id = message.document.file_id
    else:
        return

    _wm_sessions[str(uid)] = {
        "step":    "awaiting_text",
        "file_id": file_id,
        "chat_id": message.chat.id,
        "caption": message.caption or "",
        "text":    "", "pos": "center", "outline": True, "size": 50,
    }
    await message.reply_text(
        "╭─「 💧 Marca de Agua 」\n┊\n┊ Video recibido.\n"
        "┊ Escribe el texto que quieres\n┊ usar como marca de agua:\n┊\n"
        f"╰─ /wm_cancel para cancelar\n\n{BOT_SIGNATURE}", quote=True)

@bot.on_message(filters.command("wm_cancel"))
async def cmd_wm_cancel(client: Client, message: Message):
    _wm_sessions.pop(str(message.from_user.id), None)
    await message.reply_text(f"❌ Marca de agua cancelada.\n\n{BOT_SIGNATURE}")

@bot.on_callback_query(filters.regex(r"^wm_"))
async def handle_wm_callback(client: Client, cb: CallbackQuery):
    uid  = str(cb.from_user.id)
    data = cb.data
    sess = _wm_sessions.get(uid)
    if data == "wm_cancel" or not sess:
        _wm_sessions.pop(uid, None)
        await cb.message.edit_text(f"❌ Marca de agua cancelada.\n\n{BOT_SIGNATURE}")
        await cb.answer(); return
    await cb.answer()
    if data.startswith("wm_pos:") and sess.get("step") == "awaiting_pos":
        sess["pos"] = data.split(":")[1]
        sess["step"] = "awaiting_outline"
        await cb.message.edit_text(
            f"╭─「 💧 Marca de Agua 」\n┊\n"
            f"┊ Texto    : <b>{sess['text'][:40]}</b>\n"
            f"┊ Posición : <b>{WM_POS_LABELS[sess['pos']]}</b>\n┊\n"
            f"╰─ ¿Con o sin contorno?\n\n{BOT_SIGNATURE}",
            parse_mode=enums.ParseMode.HTML, reply_markup=_wm_kb_outline())
    elif data.startswith("wm_outline:") and sess.get("step") == "awaiting_outline":
        sess["outline"] = data.split(":")[1] == "yes"
        sess["step"]    = "awaiting_size"
        ol_label = "Con Contorno ✅" if sess["outline"] else "Sin Contorno 🚫"
        await cb.message.edit_text(
            f"╭─「 💧 Marca de Agua 」\n┊\n"
            f"┊ Texto    : <b>{sess['text'][:40]}</b>\n"
            f"┊ Posición : <b>{WM_POS_LABELS[sess['pos']]}</b>\n"
            f"┊ Contorno : <b>{ol_label}</b>\n┊\n"
            f"╰─ Elige el tamaño de la letra:\n\n{BOT_SIGNATURE}",
            parse_mode=enums.ParseMode.HTML, reply_markup=_wm_kb_size())
    elif data.startswith("wm_size:") and sess.get("step") == "awaiting_size":
        sess["size"] = int(data.split(":")[1])
        sess["step"] = "processing"
        _wm_sessions.pop(uid, None)
        uname    = cb.from_user.first_name
        proc_msg = await cb.message.edit_text(
            f"╭─「 💧 Marca de Agua 」\n┊\n{_wm_summary(sess)}"
            f"┊\n┊ ⏬ Descargando video...\n"
            f"╰──────────────────────────\n\n{BOT_SIGNATURE}",
            parse_mode=enums.ParseMode.HTML)
        asyncio.create_task(process_watermark(client, cb, sess, proc_msg, uname))

async def process_watermark(client: Client, cb: CallbackQuery,
                              sess: dict, proc_msg: Message, uname: str):
    task_id    = f"wm_{cb.from_user.id}_{int(time.time())}"
    chat_id    = sess["chat_id"]
    input_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_in.mp4")
    output_path = os.path.join(DOWNLOAD_DIR, f"{task_id}_wm.mp4")
    stop_evt    = threading.Event()
    timer_task  = None
    source_caption = sess.get("caption", "").strip()
    try:
        await client.download_media(sess["file_id"], file_name=input_path)
        eta_secs  = estimate_wm_time(input_path)
        total_eta = eta_secs
        await safe_edit(proc_msg, _wm_progress_text(sess, "⚙️ Aplicando marca de agua...", total_eta))

        async def _wm_timer():
            started = time.time()
            while True:
                elapsed   = int(time.time() - started)
                remaining = max(0, total_eta - elapsed)
                phase     = "⚙️ Finalizando..." if remaining == 0 else "⚙️ Aplicando marca de agua..."
                try: await safe_edit(proc_msg, _wm_progress_text(sess, phase, remaining))
                except Exception: pass
                if remaining == 0: break
                await asyncio.sleep(2)

        timer_task = asyncio.create_task(_wm_timer())
        await asyncio.to_thread(apply_watermark,
                                 input_path, output_path,
                                 sess["text"], sess["pos"], sess["outline"], sess["size"], stop_evt)
        await safe_edit(proc_msg,
            f"╭─「 💧 Marca de Agua 」\n┊\n┊ ✅ Procesado — subiendo...\n"
            f"╰──────────────────────────\n\n{BOT_SIGNATURE}")
        start_t = time.time()
        caption = source_caption if source_caption else BOT_SIGNATURE
        await client.send_video(
            chat_id=chat_id, video=output_path,
            caption=caption, parse_mode=enums.ParseMode.HTML,
            supports_streaming=True,
            progress=upload_progress,
            progress_args=(proc_msg, start_t, uname, task_id))
        try: await proc_msg.delete()
        except Exception: pass
    except Exception as e:
        await safe_edit(proc_msg, f"❌ Error en marca de agua:\n{e}\n\n{BOT_SIGNATURE}")
    finally:
        stop_evt.set()
        if timer_task: timer_task.cancel()
        for p in [input_path, output_path]:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception: pass


# ─── RECEPTOR DE TEXTO (WM text + links) ─────────────────────────────────────
_EXCLUDE_CMDS = ["start", "stat", "Stat", "STAT", "reset", "Reset", "RESET",
                 "id", "Removeid", "removeid", "cancelarID", "cancelarid",
                 "addid", "Addid", "rmid", "removebyid",
                 "getcode", "codigo", "code",
                 "coms", "cancel", "cancelar", "admin", "remadmin", "users",
                 "wm_cancel", "audio", "Audio", "mp3", "ping", "Ping",
                 "queue", "Queue", "cola", "playlist", "Playlist", "pl",
                 "encode", "Encode", "torrent"]

@bot.on_message(
    filters.text
    & ~filters.command(_EXCLUDE_CMDS)
    & ~filters.regex(r"^/cancel_"),
    group=1,
)
async def handle_text_input(client: Client, message: Message):
    uid      = message.from_user.id
    raw_text = message.text.strip()
    print(f"[handler] msg de uid={uid} auth={is_auth(uid)} texto={raw_text[:60]}")

    # Input de texto para marca de agua
    uid_str = str(uid)
    sess = _wm_sessions.get(uid_str)
    if sess and sess.get("step") == "awaiting_text":
        txt = raw_text
        if not txt: return
        sess["text"] = txt
        sess["step"] = "awaiting_pos"
        await message.reply_text(
            f"╭─「 💧 Marca de Agua 」\n┊\n┊ Texto: <b>{txt[:40]}</b>\n┊\n"
            f"╰─ Elige la posición:\n\n{BOT_SIGNATURE}",
            parse_mode=enums.ParseMode.HTML, reply_markup=_wm_kb_pos())
        return

    # Links
    if not is_auth(uid):
        await message.reply_text(
            f"⛔ **Acceso denegado.**\n"
            f"No estás autorizado. Tu ID es `{uid}`.\n"
            f"Pídele al admin que ejecute: `/addid {uid}`")
        return

    # Si está en flujo WM y envía un link, cancelar WM
    if _wm_sessions.get(uid_str) and re.search(r"https?://", raw_text):
        _wm_sessions.pop(uid_str, None)
        await message.reply_text(
            f"⚠️ Sesión de marca de agua cancelada automáticamente.\n"
            f"Procesando tu enlace...\n\n{BOT_SIGNATURE}")

    want_subs = bool(re.search(r"(?:^|\s)-lat(?:\s|$)", raw_text, re.IGNORECASE))
    urls = re.findall(r"https?://[^\s]+", raw_text)
    if not urls: return

    uname = message.from_user.first_name
    for i, url in enumerate(urls, 1):
        url   = re.sub(r"\s*-lat\s*$", "", url, flags=re.IGNORECASE).strip()
        label = f"Cola: {i}/{len(urls)}"
        await download_queue.put((client, message, url, uname, uid, label, want_subs))

    q        = download_queue.qsize()
    subs_note = " 🔤 (-lat: subtítulos ES)" if want_subs else ""
    if len(urls) == 1:
        await message.reply_text(
            f"📥 Enlace añadido a la cola.{subs_note}\n"
            f"🚦 Tareas en espera: {q}\n\n{BOT_SIGNATURE}")
    else:
        await message.reply_text(
            f"📥 {len(urls)} enlaces añadidos a la cola.{subs_note}\n"
            f"🚦 Total en espera: {q}\n\n{BOT_SIGNATURE}")


# ─── TORRENT CALLBACK ─────────────────────────────────────────────────────────
@bot.on_callback_query(filters.regex(r"^tr_"))
async def handle_torrent_callback(client: Client, cb: CallbackQuery):
    import shutil
    uid   = str(cb.from_user.id)
    data  = cb.data
    parts = data.split(":")
    if len(parts) < 3 or parts[1] != uid:
        await cb.answer("❌ No puedes controlar la selección de otro usuario.", show_alert=True)
        return
    sess = _torrent_sessions.get(uid)
    if not sess:
        await cb.answer("⚠️ Sesión expirada. Envía el torrent de nuevo.", show_alert=True)
        try: await cb.message.edit_reply_markup(reply_markup=None)
        except Exception: pass
        return
    action   = parts[0]
    task_id  = sess["task_id"]
    uname    = sess["uname"]
    source   = sess["source"]
    dl_dir   = sess["dl_dir"]
    meta_dir = sess.get("meta_dir", "")
    files    = sess["files"]
    msg      = cb.message
    want_subs = sess.get("want_subs", False)
    await cb.answer()

    if action == "tr_cxl":
        _torrent_sessions.pop(uid, None)
        active_tasks.pop(task_id, None)
        shutil.rmtree(dl_dir, ignore_errors=True)
        shutil.rmtree(meta_dir, ignore_errors=True)
        await msg.edit_text(
            f"╭ Task By → 「{uname}」\n┊ 🛑 Descarga cancelada.\n"
            f"╰──────────────\n\n{BOT_SIGNATURE}", reply_markup=None)
        return

    if action == "tr_sel":
        file_idx       = int(parts[3]) if len(parts) > 3 else None
        select_indices = [file_idx] if file_idx else None
        fname          = next((f for i, f, s in files if i == file_idx), f"archivo {file_idx}")
    else:
        select_indices = None
        fname          = f"todos ({len(files)} episodios)"

    _torrent_sessions.pop(uid, None)
    await msg.edit_text(
        f"╭ Task By → 「{uname}」\n┊ 🧲 Descargando: {str(fname)[:50]}\n"
        f"┊ ⚠️ /cancel para detener\n╰ Mode     : #TorrentMode\n\n{BOT_SIGNATURE}",
        reply_markup=None)

    async def _do_download():
        try:
            await _run_torrent_download(client, cb.message, msg, uname, task_id,
                                         source, dl_dir, select_indices=select_indices)
            await _torrent_encode_and_send(client, cb.message, msg, uname, task_id,
                                            dl_dir, want_subs=want_subs)
        except (Exception, asyncio.CancelledError) as e:
            is_cancel = isinstance(e, asyncio.CancelledError) or "USER_CANCELLED" in str(e)
            err = "🛑 Descarga cancelada." if is_cancel else f"❌ Error: {str(e)[:200]}"
            try: await safe_edit(msg, f"╭ Task By → 「{uname}」\n┊ {err}\n╰──────────────\n\n{BOT_SIGNATURE}")
            except Exception: pass
        finally:
            active_tasks.pop(task_id, None)
            shutil.rmtree(dl_dir, ignore_errors=True)
            shutil.rmtree(meta_dir, ignore_errors=True)

    asyncio.create_task(_do_download())


# ─── MAIN ─────────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        me = await bot.get_me()
        print(f"Bot iniciado ✓ — @{me.username} (ID: {me.id})")
        print(f"Admin principal ID: {ADMIN_ID}")
        asyncio.get_event_loop().create_task(queue_worker())
        await asyncio.Event().wait()

if __name__ == "__main__":
    if not API_ID or API_ID == 0:
        print("❌ ERROR: Configura API_ID en los Secrets de Replit")
        sys.exit(1)
    if not API_HASH:
        print("❌ ERROR: Configura API_HASH en los Secrets de Replit")
        sys.exit(1)
    if not BOT_TOKEN:
        print("❌ ERROR: Configura BOT_TOKEN en los Secrets de Replit")
        sys.exit(1)
    print("🚀 Iniciando bot...")
    bot.run(main())
