#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║     نظام إدارة التعلم (LMS) - بوت تيليغرام                     ║
║     الإصدار الكامل: VIP + هدية + Pagination + Media Groups     ║
║     python-telegram-bot v21+ | SQLite3 | Async                  ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import sqlite3
import traceback
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_IDS = set(
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "123456789").split(",")
    if x.strip().isdigit()
)
DB_PATH = os.getenv("DB_PATH", "/app/data/lms_school.db")
GIFT_POINTS = int(os.getenv("GIFT_POINTS", "3"))
VIP_CATEGORY_ID = int(os.getenv("VIP_CATEGORY_ID", "0"))
FREE_GIFT_CATEGORY_ID = int(os.getenv("FREE_GIFT_CATEGORY_ID", "0"))
PAGE_SIZE = 5
GROUP_PAGE_SIZE = 5

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

(
    ST_CAT_NAME, ST_CAT_EDIT_NAME, ST_CAT_REORDER,
    ST_CONTENT_NAME, ST_CONTENT_TYPE, ST_CONTENT_DATA,
    ST_CONTENT_EDIT_NAME, ST_CONTENT_EDIT_DATA,
    ST_BROADCAST_MSG, ST_BROADCAST_CONFIRM,
    ST_ADD_ADMIN_ID, ST_ADD_CHANNEL,
    ST_ADD_OWNER_ID, ST_REMOVE_OWNER_ID,
    ST_VIP_ADD, ST_VIP_DEL,
    ST_GROUP_NAME, ST_GROUP_ITEMS,
) = range(18)


# ─────────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.cursor().executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            first_name  TEXT    NOT NULL,
            username    TEXT,
            joined_at   TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS owners (
            user_id     INTEGER PRIMARY KEY,
            added_by    INTEGER,
            added_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS admins (
            user_id     INTEGER PRIMARY KEY
        );
        CREATE TABLE IF NOT EXISTS channels (
            channel_username TEXT PRIMARY KEY,
            channel_title    TEXT,
            invite_link      TEXT
        );
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id   INTEGER DEFAULT NULL,
            name        TEXT    NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (parent_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS contents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id  INTEGER NOT NULL,
            content_type TEXT    NOT NULL CHECK(content_type IN ('text','photo','video','document','link')),
            content_data TEXT    NOT NULL,
            name         TEXT    NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS content_groups (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id  INTEGER NOT NULL,
            name         TEXT    NOT NULL,
            sort_order   INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS group_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id     INTEGER NOT NULL,
            content_type TEXT    NOT NULL CHECK(content_type IN ('photo','video','document','text')),
            content_data TEXT    NOT NULL,
            caption      TEXT    DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (group_id) REFERENCES content_groups(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id     INTEGER PRIMARY KEY,
            added_by    INTEGER,
            added_at    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_points (
            user_id     INTEGER PRIMARY KEY,
            points      INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()
    logger.info("Database initialized.")


# ── Users ────────────────────────────────────────────────────────

def db_upsert_user(user_id, first_name, username):
    c = get_db()
    c.execute("INSERT OR REPLACE INTO users (user_id,first_name,username) VALUES(?,?,?)",
              (user_id, first_name, username))
    c.commit(); c.close()

def db_get_all_users():
    c = get_db()
    rows = c.execute("SELECT user_id FROM users").fetchall()
    c.close(); return [r["user_id"] for r in rows]

def db_count_users():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]; c.close(); return n

# ── Owners ───────────────────────────────────────────────────────

def db_is_owner(user_id):
    if user_id in OWNER_IDS: return True
    c = get_db(); r = c.execute("SELECT 1 FROM owners WHERE user_id=?", (user_id,)).fetchone(); c.close()
    return r is not None

def db_add_owner(user_id, added_by):
    c = get_db(); c.execute("INSERT OR IGNORE INTO owners (user_id,added_by) VALUES(?,?)", (user_id, added_by))
    c.commit(); c.close()

def db_remove_owner(user_id):
    c = get_db(); c.execute("DELETE FROM owners WHERE user_id=?", (user_id,)); c.commit(); c.close()

def db_get_all_owners():
    c = get_db(); rows = c.execute("SELECT user_id,added_by,added_at FROM owners").fetchall(); c.close(); return rows

# ── Admins ───────────────────────────────────────────────────────

def db_is_admin(user_id):
    if db_is_owner(user_id): return True
    c = get_db(); r = c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone(); c.close()
    return r is not None

def db_add_admin(user_id):
    c = get_db(); c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES(?)", (user_id,)); c.commit(); c.close()

def db_count_admins():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM admins").fetchone()[0]; c.close(); return n

# ── VIP ──────────────────────────────────────────────────────────

def db_is_vip(user_id):
    if db_is_admin(user_id): return True
    c = get_db(); r = c.execute("SELECT 1 FROM vip_users WHERE user_id=?", (user_id,)).fetchone(); c.close()
    return r is not None

def db_add_vip(user_id, added_by):
    c = get_db(); c.execute("INSERT OR REPLACE INTO vip_users (user_id,added_by) VALUES(?,?)", (user_id, added_by))
    c.commit(); c.close()

def db_remove_vip(user_id):
    c = get_db(); c.execute("DELETE FROM vip_users WHERE user_id=?", (user_id,)); c.commit(); c.close()

def db_count_vip():
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM vip_users").fetchone()[0]; c.close(); return n

# ── Points ───────────────────────────────────────────────────────

def db_get_points(user_id):
    c = get_db(); r = c.execute("SELECT points FROM user_points WHERE user_id=?", (user_id,)).fetchone()
    c.close(); return r["points"] if r else 0

def db_is_first_visit(user_id):
    c = get_db(); r = c.execute("SELECT 1 FROM user_points WHERE user_id=?", (user_id,)).fetchone()
    c.close(); return r is None

def db_add_points(user_id, points):
    c = get_db()
    c.execute("INSERT INTO user_points (user_id,points) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET points=points+?",
              (user_id, points, points))
    c.commit()
    total = c.execute("SELECT points FROM user_points WHERE user_id=?", (user_id,)).fetchone()["points"]
    c.close(); return total

# ── Channels ─────────────────────────────────────────────────────

def db_get_channels():
    c = get_db(); rows = c.execute("SELECT * FROM channels").fetchall(); c.close(); return rows

def db_add_channel(username, title, invite_link):
    c = get_db()
    c.execute("INSERT OR REPLACE INTO channels (channel_username,channel_title,invite_link) VALUES(?,?,?)",
              (username, title, invite_link))
    c.commit(); c.close()

def db_remove_channel(username):
    c = get_db(); c.execute("DELETE FROM channels WHERE channel_username=?", (username,)); c.commit(); c.close()

# ── Categories ───────────────────────────────────────────────────

def db_get_subcategories(parent_id):
    c = get_db()
    if parent_id == 0:
        rows = c.execute("SELECT * FROM categories WHERE parent_id IS NULL ORDER BY sort_order,id").fetchall()
    else:
        rows = c.execute("SELECT * FROM categories WHERE parent_id=? ORDER BY sort_order,id", (parent_id,)).fetchall()
    c.close(); return rows

def db_get_category(cat_id):
    c = get_db(); r = c.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone(); c.close(); return r

def db_add_category(parent_id, name):
    c = get_db(); actual = None if parent_id == 0 else parent_id
    c.execute("INSERT INTO categories (parent_id,name,sort_order) VALUES(?,?,0)", (actual, name))
    new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.commit(); c.close(); return new_id

def db_edit_category_name(cat_id, name):
    c = get_db(); c.execute("UPDATE categories SET name=? WHERE id=?", (name, cat_id)); c.commit(); c.close()

def db_delete_category(cat_id):
    c = get_db(); c.execute("DELETE FROM categories WHERE id=?", (cat_id,)); c.commit(); c.close()

# ── Contents ─────────────────────────────────────────────────────

def db_get_contents(category_id):
    c = get_db()
    rows = c.execute("SELECT * FROM contents WHERE category_id=? ORDER BY sort_order,id", (category_id,)).fetchall()
    c.close(); return rows

def db_get_content(content_id):
    c = get_db(); r = c.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone(); c.close(); return r

def db_add_content(category_id, content_type, content_data, name):
    c = get_db()
    c.execute("INSERT INTO contents (category_id,content_type,content_data,name,sort_order) VALUES(?,?,?,?,0)",
              (category_id, content_type, content_data, name))
    new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.commit(); c.close(); return new_id

def db_edit_content_name(content_id, name):
    c = get_db(); c.execute("UPDATE contents SET name=? WHERE id=?", (name, content_id)); c.commit(); c.close()

def db_edit_content_data(content_id, content_data, content_type):
    c = get_db()
    c.execute("UPDATE contents SET content_data=?,content_type=? WHERE id=?", (content_data, content_type, content_id))
    c.commit(); c.close()

def db_delete_content(content_id):
    c = get_db(); c.execute("DELETE FROM contents WHERE id=?", (content_id,)); c.commit(); c.close()

# ── Content Groups ───────────────────────────────────────────────

def db_get_groups(category_id):
    c = get_db()
    rows = c.execute("SELECT * FROM content_groups WHERE category_id=? ORDER BY sort_order,id", (category_id,)).fetchall()
    c.close(); return rows

def db_get_group(group_id):
    c = get_db(); r = c.execute("SELECT * FROM content_groups WHERE id=?", (group_id,)).fetchone(); c.close(); return r

def db_add_group(category_id, name):
    c = get_db()
    c.execute("INSERT INTO content_groups (category_id,name,sort_order) VALUES(?,?,0)", (category_id, name))
    new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.commit(); c.close(); return new_id

def db_delete_group(group_id):
    c = get_db(); c.execute("DELETE FROM content_groups WHERE id=?", (group_id,)); c.commit(); c.close()

def db_get_group_items(group_id):
    c = get_db()
    rows = c.execute("SELECT * FROM group_items WHERE group_id=? ORDER BY sort_order,id", (group_id,)).fetchall()
    c.close(); return rows

def db_add_group_item(group_id, content_type, content_data, caption=""):
    c = get_db()
    c.execute("INSERT INTO group_items (group_id,content_type,content_data,caption,sort_order) VALUES(?,?,?,?,0)",
              (group_id, content_type, content_data, caption))
    new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.commit(); c.close(); return new_id

def db_count_group_items(group_id):
    c = get_db(); n = c.execute("SELECT COUNT(*) FROM group_items WHERE group_id=?", (group_id,)).fetchone()[0]
    c.close(); return n

# ── Reorder ──────────────────────────────────────────────────────

def _reorder_item(item_id, item_type, direction):
    c = get_db()
    table = "categories" if item_type == "cat" else "contents"
    r = c.execute(f"SELECT sort_order FROM {table} WHERE id=?", (item_id,)).fetchone()
    if r:
        c.execute(f"UPDATE {table} SET sort_order=? WHERE id=?", (max(0, r[0] + direction), item_id))
    c.commit(); c.close()


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

CONTENT_EMOJI = {"text": "📝", "photo": "🖼️", "video": "🎥", "document": "📄", "link": "🔗"}

def _truncate(text, max_len=28):
    return text if len(text) <= max_len else text[:max_len - 1] + "…"

async def check_subscription(bot, user_id):
    channels = db_get_channels()
    if not channels: return True, []
    missing = []
    for ch in channels:
        try:
            # التأكد إذا كان المحفوظ ID أرقام أم معرف نصي
            cid = ch['channel_username']
            target = int(cid) if cid.lstrip('-').isdigit() else f"@{cid.lstrip('@')}"
            
            m = await bot.get_chat_member(chat_id=target, user_id=user_id)
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                missing.append(ch)
        except TelegramError:
            missing.append(ch)
    return len(missing) == 0, missing

def subscription_required_keyboard(missing):
    buttons = []
    for ch in missing:
        link = ch["invite_link"]
        # لو مفيش رابط محفوظ، يعمل رابط افتراضي كاحتياطي
        if not link or link == "غير_متوفر":
            link = f"https://t.me/{ch['channel_username'].lstrip('@')}"
            
        title = ch["channel_title"] or "قناة الاشتراك"
        buttons.append([InlineKeyboardButton(f"📢 {title}", url=link)])
        
    buttons.append([InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)

def build_admin_reply_keyboard(user_id=0):
    keyboard = [
        ["📂 إدارة المحتوى", "📢 إدارة القنوات"],
        ["📣 إرسال رسالة جماعية", "👤 إضافة مشرف"],
        ["⭐ إدارة VIP", "📊 إحصائيات"],
        ["💾 نسخ احتياطي", "👁️ وضع الطالب"],
        ["🚪 خروج من لوحة التحكم"],
    ]
    if db_is_owner(user_id):
        keyboard.insert(2, ["👑 إدارة الأونرز"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def build_admin_reply_keyboard_student_mode():
    return ReplyKeyboardMarkup([["🔙 العودة إلى لوحة التحكم"]], resize_keyboard=True)

def _extract_content_from_message(msg):
    if msg.photo:    return "photo",    msg.photo[-1].file_id
    if msg.video:    return "video",    msg.video.file_id
    if msg.document: return "document", msg.document.file_id
    if msg.text:
        t = msg.text.strip()
        if t.startswith("http") or "t.me/" in t: return "link", t
        return "text", t
    return None, None

async def send_content_to_user(bot, chat_id, content, reply_markup=None):
    ctype, cdata, cname = content["content_type"], content["content_data"], content["name"]
    match ctype:
        case "text":
            await bot.send_message(chat_id=chat_id, text=f"<b>{cname}</b>\n\n{cdata}",
                                   parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        case "photo":
            await bot.send_photo(chat_id=chat_id, photo=cdata, caption=f"<b>{cname}</b>",
                                 parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        case "video":
            await bot.send_video(chat_id=chat_id, video=cdata, caption=f"<b>{cname}</b>",
                                 parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        case "document":
            await bot.send_document(chat_id=chat_id, document=cdata, caption=f"<b>{cname}</b>",
                                    parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        case "link":
            if "t.me/c/" in cdata or "t.me/" in cdata:
                try:
                    parts = cdata.rstrip("/").split("/")
                    msg_id = int(parts[-1])
                    channel_id = int(f"-100{parts[-2]}") if "t.me/c/" in cdata else f"@{parts[-2]}"
                    await bot.copy_message(chat_id=chat_id, from_chat_id=channel_id,
                                           message_id=msg_id, reply_markup=reply_markup)
                    return
                except Exception: pass
            await bot.send_message(chat_id=chat_id,
                                   text=f"<b>{cname}</b>\n\n🔗 <a href='{cdata}'>افتح الرابط</a>",
                                   parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        case _:
            await bot.send_message(chat_id=chat_id, text=f"❓ نوع غير معروف: {ctype}")


# ─────────────────────────────────────────────────────────────────
# 📦 MEDIA GROUP SENDER — إرسال المجموعة 5 في 5
# ─────────────────────────────────────────────────────────────────

async def send_group_page(bot, chat_id, group_id, page, is_admin=False):
    """
    يرسل صفحة من عناصر المجموعة (GROUP_PAGE_SIZE في كل مرة).
    - الصور والفيديوهات → media_group مع بعض
    - الملفات          → فردي
    - النصوص          → رسائل عادية
    ثم رسالة تحكم فيها أزرار التنقل بين الصفحات.
    """
    items = db_get_group_items(group_id)
    group = db_get_group(group_id)

    if not items:
        await bot.send_message(chat_id=chat_id, text="📭 هذه المجموعة فارغة.")
        return

    total       = len(items)
    total_pages = max(1, (total + GROUP_PAGE_SIZE - 1) // GROUP_PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * GROUP_PAGE_SIZE
    page_items  = items[start : start + GROUP_PAGE_SIZE]
    group_name  = group["name"] if group else "مجموعة"

    media_items = [i for i in page_items if i["content_type"] in ("photo", "video")]
    doc_items   = [i for i in page_items if i["content_type"] == "document"]
    text_items  = [i for i in page_items if i["content_type"] == "text"]

    # ── صور وفيديو → media_group ─────────────────────────────
    if media_items:
        media_list = []
        for idx, item in enumerate(media_items):
            cap = item["caption"] or ""
            if idx == 0:
                cap = f"<b>{group_name}</b>\n📄 {page+1}/{total_pages}\n\n{cap}".strip()
            if item["content_type"] == "photo":
                media_list.append(InputMediaPhoto(
                    media=item["content_data"],
                    caption=cap or None,
                    parse_mode=ParseMode.HTML if cap else None,
                ))
            else:
                media_list.append(InputMediaVideo(
                    media=item["content_data"],
                    caption=cap or None,
                    parse_mode=ParseMode.HTML if cap else None,
                ))
        # تيليغرام يقبل max 10 في media_group
        for i in range(0, len(media_list), 10):
            await bot.send_media_group(chat_id=chat_id, media=media_list[i:i+10])
            await asyncio.sleep(0.3)

    # ── نصوص ─────────────────────────────────────────────────
    for item in text_items:
        await bot.send_message(chat_id=chat_id, text=item["content_data"], parse_mode=ParseMode.HTML)
        await asyncio.sleep(0.1)

    # ── ملفات فردي ───────────────────────────────────────────
    for item in doc_items:
        cap = item["caption"] or ""
        await bot.send_document(
            chat_id=chat_id,
            document=item["content_data"],
            caption=cap or None,
            parse_mode=ParseMode.HTML if cap else None,
        )
        await asyncio.sleep(0.1)

    # ── رسالة التحكم + أزرار التنقل ──────────────────────────
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"grp_{group_id}_{page-1}"))
    nav_row.append(InlineKeyboardButton(
        f"📄 {page+1}/{total_pages}  ({total} ملف)", callback_data="pg_info"
    ))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"grp_{group_id}_{page+1}"))

    nav_buttons = [nav_row]

    if is_admin:
        cat_id = group["category_id"] if group else 0
        nav_buttons.append([
            InlineKeyboardButton("🗑️ حذف المجموعة",   callback_data=f"a_dg_{group_id}"),
            InlineKeyboardButton("➕ إضافة ملفات",     callback_data=f"a_ag_{group_id}"),
        ])
        nav_buttons.append([
            InlineKeyboardButton("🔙 رجوع للفئة", callback_data=f"nav_{cat_id}")
        ])

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📦 <b>{group_name}</b>\n"
            f"الصفحة <b>{page+1}</b> من <b>{total_pages}</b> | إجمالي <b>{total}</b> ملف"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(nav_buttons),
    )


# ─────────────────────────────────────────────────────────────────
# PAGINATION KEYBOARD
# ─────────────────────────────────────────────────────────────────

def build_category_page_keyboard(parent_id, page, is_admin, path_stack):
    subcats  = db_get_subcategories(parent_id)
    contents = db_get_contents(parent_id)
    groups   = db_get_groups(parent_id)

    all_items = (
        [("cat",  c) for c in subcats] +
        [("cont", c) for c in contents] +
        [("grp",  g) for g in groups]
    )
    total_items = len(all_items)
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    page_items  = all_items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    buttons = []
    for itype, item in page_items:
        if itype == "cat":
            buttons.append([InlineKeyboardButton(
                f"📁 {_truncate(item['name'])}", callback_data=f"nav_{item['id']}"
            )])
        elif itype == "grp":
            count = db_count_group_items(item["id"])
            buttons.append([InlineKeyboardButton(
                f"📦 {_truncate(item['name'])}  ({count} ملف)",
                callback_data=f"grp_{item['id']}_0"
            )])
        else:
            emoji = CONTENT_EMOJI.get(item["content_type"], "📌")
            buttons.append([InlineKeyboardButton(
                f"{emoji} {_truncate(item['name'])}", callback_data=f"cnt_{item['id']}"
            )])

    # شريط الصفحات
    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"pg_{parent_id}_{page-1}"))
        nav_row.append(InlineKeyboardButton(f"📄 {page+1}/{total_pages}", callback_data="pg_info"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"pg_{parent_id}_{page+1}"))
        buttons.append(nav_row)

    # أزرار الأدمن
    if is_admin:
        buttons.append([
            InlineKeyboardButton("➕ فئة فرعية",       callback_data=f"a_nc_{parent_id}"),
            InlineKeyboardButton("➕ محتوى",            callback_data=f"a_nx_{parent_id}"),
        ])
        buttons.append([
            InlineKeyboardButton("➕ مجموعة ملفات 📦", callback_data=f"a_ng_{parent_id}"),
        ])
        if parent_id != 0:
            buttons.append([
                InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"a_ec_{parent_id}"),
                InlineKeyboardButton("🗑️ حذف الفئة",   callback_data=f"a_dc_{parent_id}"),
            ])
            buttons.append([InlineKeyboardButton("🔃 إعادة الترتيب", callback_data=f"a_rc_{parent_id}")])

    # زر رجوع
    if path_stack:
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back_{path_stack[-1]}")])
    elif parent_id != 0:
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="back_0")])

    return InlineKeyboardMarkup(buttons), page, total_pages


def build_content_admin_keyboard(content_id, category_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"a_en_{content_id}"),
         InlineKeyboardButton("🔄 تعديل الملف", callback_data=f"a_ed_{content_id}")],
        [InlineKeyboardButton("🗑️ حذف المحتوى", callback_data=f"a_dl_{content_id}")],
        [InlineKeyboardButton("🔙 رجوع للفئة",  callback_data=f"nav_{category_id}")],
    ])


# ─────────────────────────────────────────────────────────────────
# MAIN MENU
# ─────────────────────────────────────────────────────────────────

async def show_main_menu(update, context, edit=False):
    user_id  = update.effective_user.id
    is_vip   = db_is_vip(user_id)
    points   = db_get_points(user_id)
    admin    = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
    subcats  = db_get_subcategories(0)
    buttons  = []

    if is_vip:
        buttons.append([InlineKeyboardButton("⭐ قسم الـ VIP (المدفوع) ⭐", callback_data="nav_vip")])
    else:
        buttons.append([InlineKeyboardButton("🔒 قسم الـ VIP (المدفوع)",   callback_data="vip_locked")])

    buttons.append([InlineKeyboardButton(
        f"🎁 القسم المجاني ({points} نقاط هدية)", callback_data="nav_free"
    )])
    for cat in subcats:
        buttons.append([InlineKeyboardButton(f"📁 {_truncate(cat['name'])}", callback_data=f"nav_{cat['id']}")])
    if admin:
        buttons.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="open_admin_panel")])

    msg_text = "🏠 <b>القائمة الرئيسية — اختر قسماً:</b>"
    msg_obj  = update.message or (update.callback_query.message if update.callback_query else None)

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                msg_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest: pass
    elif msg_obj:
        await msg_obj.reply_text(msg_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────────────────────────
# SHOW CATEGORY
# ─────────────────────────────────────────────────────────────────

async def show_category(update, context, parent_id, page=0, edit=True):
    user_id    = update.effective_user.id
    admin      = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
    path_stack = context.user_data.get("path_stack", [])

    title = "🏠 <b>الرئيسية — اختر فئة:</b>" if parent_id == 0 else (
        f"📂 <b>{db_get_category(parent_id)['name']}</b>"
        if db_get_category(parent_id) else "📂 <b>الفئة</b>"
    )

    keyboard, cur_page, total_pages = build_category_page_keyboard(parent_id, page, admin, path_stack)

    total_count = (len(db_get_subcategories(parent_id)) +
                   len(db_get_contents(parent_id)) +
                   len(db_get_groups(parent_id)))

    if total_count == 0:
        title += "\n\n<i>لا يوجد محتوى في هذه الفئة بعد.</i>"
    elif total_pages > 1:
        title += f"\n<i>الصفحة {cur_page+1} من {total_pages} — إجمالي {total_count} عنصر</i>"

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(title, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except BadRequest: pass
    else:
        msg_obj = update.message or (update.callback_query.message if update.callback_query else None)
        if msg_obj:
            await msg_obj.reply_text(title, parse_mode=ParseMode.HTML, reply_markup=keyboard)


# ─────────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_upsert_user(user.id, user.first_name, user.username)

    ok, missing = await check_subscription(context.bot, user.id)
    if not ok:
        await update.message.reply_text(
            "🔒 <b>يجب عليك الاشتراك في القنوات التالية للمتابعة:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=subscription_required_keyboard(missing),
        )
        return

    context.user_data.update({"path_stack": [], "current_cat": 0, "current_page": 0})
    admin_mode = db_is_admin(user.id) and not context.user_data.get("student_test_mode", False)

    if admin_mode:
        await update.message.reply_text(
            f"👑 <b>مرحباً {user.first_name}!</b>\nلوحة تحكم المشرف جاهزة.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_reply_keyboard(user.id),
        )
    else:
        if db_is_first_visit(user.id):
            db_add_points(user.id, GIFT_POINTS)
        points = db_get_points(user.id)
        await update.message.reply_text(
            f"🔥 <b>أهلاً بك في البوت التقني الأضخم!</b>\n\n"
            f"🎁 لقد حصلت على <b>{points} نقاط</b> هدية في القسم المجاني\n\n"
            f"👇 اختر القسم الذي تريد استكشافه بالأسفل",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_main_menu(update, context, edit=False)


# ─────────────────────────────────────────────────────────────────
# CALLBACK ROUTER
# ─────────────────────────────────────────────────────────────────

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id

    # تحقق الاشتراك
    if data == "check_sub":
        ok, missing = await check_subscription(context.bot, user_id)
        if ok:
            db_upsert_user(user_id, update.effective_user.first_name, update.effective_user.username)
            await query.edit_message_text("✅ تم التحقق! اضغط /start للبدء.")
        else:
            try:
                # محاولة تعديل الأزرار لو كان في قنوات جديدة انضافت
                await query.edit_message_reply_markup(reply_markup=subscription_required_keyboard(missing))
            except BadRequest:
                # لو الأزرار هي هي، نطلعله رسالة منبثقة بدل ما الكود يضرب
                await query.answer("❌ لم تقم بالاشتراك في جميع القنوات بعد!", show_alert=True)
        return

    if data == "pg_info":
        return

    if not data.startswith("a_") and data not in ("check_sub", "open_admin_panel"):
        ok, missing = await check_subscription(context.bot, user_id)
        if not ok:
            await query.edit_message_text(
                "🔒 <b>يجب الاشتراك في القنوات أولاً:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=subscription_required_keyboard(missing),
            )
            return

    if data == "main_menu":
        context.user_data.update({"path_stack": [], "current_cat": 0, "current_page": 0})
        await show_main_menu(update, context, edit=True); return

    if data == "open_admin_panel":
        if db_is_admin(user_id):
            await query.message.reply_text("⚙️ لوحة التحكم:", reply_markup=build_admin_reply_keyboard(user_id))
        return

    if data == "vip_locked":
        await query.answer("🔒 هذا القسم حصري للمشتركين VIP!\nتواصل مع الإدارة.", show_alert=True); return

    if data == "nav_vip":
        if db_is_vip(user_id):
            if VIP_CATEGORY_ID == 0:
                await query.answer("⭐ لم يتم تحديد قسم VIP بعد.", show_alert=True)
            else:
                context.user_data.update({"path_stack": [0], "current_cat": VIP_CATEGORY_ID, "current_page": 0})
                await show_category(update, context, parent_id=VIP_CATEGORY_ID, page=0, edit=True)
        else:
            await query.answer("🔒 هذا القسم للـ VIP فقط!", show_alert=True)
        return

    if data == "nav_free":
        if FREE_GIFT_CATEGORY_ID == 0:
            await query.answer("🎁 لم يتم تحديد القسم المجاني بعد.", show_alert=True)
        else:
            context.user_data.update({"path_stack": [0], "current_cat": FREE_GIFT_CATEGORY_ID, "current_page": 0})
            await show_category(update, context, parent_id=FREE_GIFT_CATEGORY_ID, page=0, edit=True)
        return

    # ── Pagination الفئات ──────────────────────────────────────
    if data.startswith("pg_"):
        parts = data.split("_")
        if len(parts) == 3:
            context.user_data["current_page"] = int(parts[2])
            await show_category(update, context, parent_id=int(parts[1]), page=int(parts[2]), edit=True)
        return

    # ── فتح مجموعة ملفات: grp_<id>_<page> ────────────────────
    if data.startswith("grp_"):
        parts = data.split("_")
        if len(parts) == 3:
            admin = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
            await send_group_page(
                context.bot, query.message.chat_id,
                int(parts[1]), int(parts[2]), is_admin=admin
            )
        return

    parts_data = data.split("_")
    match parts_data[0]:

        case "nav":
            cat_id = int(parts_data[1])
            stack  = context.user_data.get("path_stack", [])
            stack.append(context.user_data.get("current_cat", 0))
            context.user_data.update({"path_stack": stack, "current_cat": cat_id, "current_page": 0})
            await show_category(update, context, parent_id=cat_id, page=0, edit=True)

        case "back":
            target = int(parts_data[1])
            stack  = context.user_data.get("path_stack", [])
            while stack and stack[-1] != target: stack.pop()
            if stack: stack.pop()
            context.user_data.update({"path_stack": stack, "current_cat": target, "current_page": 0})
            if target == 0: await show_main_menu(update, context, edit=True)
            else:           await show_category(update, context, parent_id=target, page=0, edit=True)

        case "cnt":
            content = db_get_content(int(parts_data[1]))
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True); return
            admin = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
            rm = build_content_admin_keyboard(content["id"], content["category_id"]) if admin else None
            await send_content_to_user(context.bot, query.message.chat_id, content, reply_markup=rm)

        case "a":
            if not db_is_admin(user_id):
                await query.answer("⛔ غير مصرح.", show_alert=True); return
            await handle_admin_callback(update, context, data)

        case _: pass


# ─────────────────────────────────────────────────────────────────
# ADMIN CALLBACKS
# ─────────────────────────────────────────────────────────────────

async def handle_admin_callback(update, context, data):
    query  = update.callback_query
    parts  = data.split("_")
    action = parts[1]
    item_id = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else 0

    match action:

        # ── إضافة فئة ─────────────────────────────────────────
        case "nc":
            context.user_data.update({"new_cat_parent": item_id, "awaiting": "new_category_name"})
            await query.edit_message_text(
                "📁 <b>إضافة فئة فرعية</b>\n\nأرسل اسم الفئة:", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{item_id}")]]),
            )

        # ── إضافة محتوى ───────────────────────────────────────
        case "nx":
            context.user_data.update({"new_cont_cat": item_id, "awaiting": "new_content_name"})
            await query.edit_message_text(
                "📌 <b>إضافة محتوى — الخطوة 1/2</b>\n\nأرسل <b>اسم</b> المحتوى:", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{item_id}")]]),
            )

        # ── إضافة مجموعة جديدة ────────────────────────────────
        case "ng":
            context.user_data.update({"new_group_cat": item_id, "awaiting": "new_group_name"})
            await query.edit_message_text(
                "📦 <b>إضافة مجموعة ملفات جديدة</b>\n\n"
                "الخطوة 1: أرسل <b>اسم المجموعة</b>:", parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{item_id}")]]),
            )

        # ── إضافة ملفات لمجموعة موجودة ────────────────────────
        case "ag":
            group = db_get_group(item_id)
            count = db_count_group_items(item_id)
            context.user_data.update({"adding_to_group": item_id, "awaiting": "add_group_item"})
            await query.message.reply_text(
                f"📎 <b>إضافة ملفات للمجموعة «{group['name'] if group else ''}»</b>\n\n"
                f"العناصر الحالية: <b>{count}</b>\n\n"
                f"أرسل صورة 🖼️ أو فيديو 🎥 أو ملف 📄 أو نص 📝\n"
                f"عند الانتهاء أرسل /done",
                parse_mode=ParseMode.HTML,
            )

        # ── حذف مجموعة (تأكيد) ────────────────────────────────
        case "dg":
            group = db_get_group(item_id)
            if not group:
                await query.answer("❌ المجموعة غير موجودة.", show_alert=True); return
            await query.message.reply_text(
                f"⚠️ <b>تأكيد الحذف</b>\n\nحذف المجموعة <b>«{group['name']}»</b> مع كل محتوياتها؟",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ نعم احذف", callback_data=f"a_dgy_{item_id}"),
                    InlineKeyboardButton("❌ إلغاء",    callback_data=f"nav_{group['category_id']}"),
                ]]),
            )

        # ── حذف مجموعة (تنفيذ) ────────────────────────────────
        case "dgy":
            group = db_get_group(item_id)
            if group:
                cat_id = group["category_id"]
                db_delete_group(item_id)
                await query.answer("✅ تم حذف المجموعة.", show_alert=True)
                await show_category(update, context, parent_id=cat_id, page=0, edit=False)

        # ── تعديل اسم فئة ─────────────────────────────────────
        case "ec":
            cat = db_get_category(item_id)
            if not cat:
                await query.answer("❌ الفئة غير موجودة.", show_alert=True); return
            context.user_data.update({"edit_cat_id": item_id, "awaiting": "edit_category_name"})
            parent = cat["parent_id"] or 0
            await query.edit_message_text(
                f"✏️ <b>تعديل اسم الفئة</b>\n\nالاسم الحالي: <i>{cat['name']}</i>\n\nأرسل الاسم الجديد:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{parent}")]]),
            )

        # ── حذف فئة (تأكيد) ───────────────────────────────────
        case "dc":
            cat = db_get_category(item_id)
            if not cat:
                await query.answer("❌ الفئة غير موجودة.", show_alert=True); return
            parent = cat["parent_id"] or 0
            await query.edit_message_text(
                f"⚠️ <b>تأكيد الحذف</b>\n\nحذف <b>«{cat['name']}»</b> مع كل محتوياتها؟",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ نعم احذف", callback_data=f"a_cy_{item_id}"),
                    InlineKeyboardButton("❌ إلغاء",    callback_data=f"nav_{parent}"),
                ]]),
            )

        # ── حذف فئة (تنفيذ) ───────────────────────────────────
        case "cy":
            cat = db_get_category(item_id)
            if cat:
                parent = cat["parent_id"] or 0
                db_delete_category(item_id)
                await query.answer("✅ تم الحذف.", show_alert=True)
                context.user_data.update({"current_cat": parent, "current_page": 0})
                stack = context.user_data.get("path_stack", [])
                if stack and stack[-1] == item_id: stack.pop()
                context.user_data["path_stack"] = stack
                if parent == 0: await show_main_menu(update, context, edit=True)
                else:           await show_category(update, context, parent_id=parent, page=0, edit=True)

        case "rc": await show_reorder_menu(update, context, item_id)
        case "ru":
            _reorder_item(item_id, "cat", -1)
            await show_reorder_menu(update, context, context.user_data.get("reorder_parent", 0))
        case "rd":
            _reorder_item(item_id, "cat", +1)
            await show_reorder_menu(update, context, context.user_data.get("reorder_parent", 0))

        case "en":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True); return
            context.user_data.update({"edit_cont_id": item_id, "awaiting": "edit_content_name"})
            await query.message.reply_text(
                f"✏️ <b>تعديل اسم المحتوى</b>\n\nالاسم الحالي: <i>{content['name']}</i>\n\nأرسل الاسم الجديد:",
                parse_mode=ParseMode.HTML,
            )

        case "ed":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True); return
            context.user_data.update({"edit_cont_id": item_id, "awaiting": "edit_content_data"})
            ctype_ar = {"text": "نص", "photo": "صورة", "video": "فيديو", "document": "ملف", "link": "رابط"}.get(content["content_type"], "")
            await query.message.reply_text(
                f"🔄 <b>تعديل المحتوى</b>\n\nالنوع الحالي: <b>{ctype_ar}</b>\n\nأرسل المحتوى الجديد:",
                parse_mode=ParseMode.HTML,
            )

        case "dl":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True); return
            await query.message.reply_text(
                f"⚠️ <b>تأكيد الحذف</b>\n\nحذف: <b>«{content['name']}»</b>؟",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ نعم احذف", callback_data=f"a_dy_{item_id}"),
                    InlineKeyboardButton("❌ إلغاء",    callback_data=f"nav_{content['category_id']}"),
                ]]),
            )

        case "dy":
            content = db_get_content(item_id)
            if content:
                cat_id = content["category_id"]
                db_delete_content(item_id)
                await query.answer("✅ تم حذف المحتوى.", show_alert=True)
                await show_category(update, context, parent_id=cat_id, page=0, edit=False)

        case "rch":
            username = "_".join(parts[2:])
            db_remove_channel(username)
            await query.answer(f"✅ تم حذف @{username}.", show_alert=True)
            await show_channels_panel(update, context)

        case _:
            await query.answer("❓ أمر غير معروف.", show_alert=True)


async def show_reorder_menu(update, context, parent_id):
    query   = update.callback_query
    context.user_data["reorder_parent"] = parent_id
    subcats  = db_get_subcategories(parent_id)
    contents = db_get_contents(parent_id)
    if not subcats and not contents:
        await query.answer("لا توجد عناصر.", show_alert=True); return
    buttons = []
    for cat in subcats:
        buttons.append([
            InlineKeyboardButton("⬆️", callback_data=f"a_ru_{cat['id']}"),
            InlineKeyboardButton(f"📁 {_truncate(cat['name'], 18)}", callback_data="pg_info"),
            InlineKeyboardButton("⬇️", callback_data=f"a_rd_{cat['id']}"),
        ])
    for cont in contents:
        emoji = CONTENT_EMOJI.get(cont["content_type"], "📌")
        buttons.append([
            InlineKeyboardButton("⬆️", callback_data=f"a_rcu_{cont['id']}"),
            InlineKeyboardButton(f"{emoji} {_truncate(cont['name'], 18)}", callback_data="pg_info"),
            InlineKeyboardButton("⬇️", callback_data=f"a_rcd_{cont['id']}"),
        ])
    buttons.append([InlineKeyboardButton("✅ تم", callback_data=f"nav_{parent_id}")])
    await query.edit_message_text("🔃 <b>إعادة الترتيب</b>\n\nاضغط ⬆️ / ⬇️:",
                                  parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────────────────────────
# AWAITING INPUT
# ─────────────────────────────────────────────────────────────────

async def handle_awaiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db_is_admin(user_id): return
    awaiting = context.user_data.get("awaiting")
    if not awaiting: return

    msg  = update.message
    text = msg.text or ""

    match awaiting:

        case "new_category_name":
            if not text.strip(): await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً."); return
            parent_id = context.user_data.pop("new_cat_parent", 0)
            context.user_data.pop("awaiting", None)
            db_add_category(parent_id, text.strip())
            await msg.reply_text(f"✅ تم إضافة الفئة <b>«{text.strip()}»</b>.", parse_mode=ParseMode.HTML)
            await show_category(update, context, parent_id=parent_id, page=0, edit=False)

        case "new_content_name":
            if not text.strip(): await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً."); return
            context.user_data.update({"new_cont_name": text.strip(), "awaiting": "new_content_data"})
            await msg.reply_text(
                "📎 <b>الخطوة 2/2 — أرسل المحتوى:</b>\n\nصورة 🖼️ | فيديو 🎥 | ملف 📄 | نص 📝 | رابط 🔗",
                parse_mode=ParseMode.HTML,
            )

        case "new_content_data":
            cat_id = context.user_data.pop("new_cont_cat", 0)
            name   = context.user_data.pop("new_cont_name", "محتوى")
            context.user_data.pop("awaiting", None)
            ctype, cdata = _extract_content_from_message(msg)
            if not ctype: await msg.reply_text("❌ لم أتمكن من استخراج المحتوى."); return
            db_add_content(cat_id, ctype, cdata, name)
            ctype_ar = {"text": "نص", "photo": "صورة", "video": "فيديو", "document": "ملف", "link": "رابط"}.get(ctype, ctype)
            await msg.reply_text(f"✅ تم إضافة <b>«{name}»</b> ({ctype_ar}).", parse_mode=ParseMode.HTML)
            await show_category(update, context, parent_id=cat_id, page=0, edit=False)

        # ── مجموعة: استقبال الاسم ────────────────────────────
        case "new_group_name":
            if not text.strip(): await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً."); return
            cat_id   = context.user_data.get("new_group_cat", 0)
            group_id = db_add_group(cat_id, text.strip())
            context.user_data.update({"adding_to_group": group_id, "awaiting": "add_group_item"})
            await msg.reply_text(
                f"✅ تم إنشاء المجموعة <b>«{text.strip()}»</b>!\n\n"
                f"📎 <b>الآن أضف الملفات:</b>\n\n"
                f"أرسل صورة 🖼️ أو فيديو 🎥 أو ملف 📄 أو نص 📝\n"
                f"يمكنك إرسال أي عدد من الملفات.\n\n"
                f"✅ عند الانتهاء أرسل /done",
                parse_mode=ParseMode.HTML,
            )

        # ── مجموعة: استقبال الملفات ──────────────────────────
        case "add_group_item":
            group_id = context.user_data.get("adding_to_group")
            if not group_id:
                context.user_data.pop("awaiting", None); return

            # /done → ينهي الإضافة
            if text.strip() == "/done":
                await _finish_group(update, context, group_id); return

            ctype, cdata = _extract_content_from_message(msg)
            if not ctype:
                await msg.reply_text("❌ نوع غير مدعوم. أرسل صورة/فيديو/ملف/نص  أو /done للإنهاء."); return
            if ctype == "link":
                await msg.reply_text("⚠️ الروابط غير مدعومة في المجموعات. أرسل ملف أو نص.\nأو /done للإنهاء."); return

            caption = msg.caption or ""
            db_add_group_item(group_id, ctype, cdata, caption)
            count    = db_count_group_items(group_id)
            ctype_ar = {"text": "نص", "photo": "صورة", "video": "فيديو", "document": "ملف"}.get(ctype, ctype)
            await msg.reply_text(
                f"✅ تمت الإضافة! ({ctype_ar})\n"
                f"إجمالي العناصر الآن: <b>{count}</b>\n\n"
                f"أرسل المزيد أو /done للإنهاء.",
                parse_mode=ParseMode.HTML,
            )

        case "edit_category_name":
            if not text.strip(): await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً."); return
            cat_id = context.user_data.pop("edit_cat_id", None)
            context.user_data.pop("awaiting", None)
            db_edit_category_name(cat_id, text.strip())
            await msg.reply_text(f"✅ تم تحديث الاسم إلى <b>«{text.strip()}»</b>.", parse_mode=ParseMode.HTML)
            await show_category(update, context, parent_id=cat_id, page=0, edit=False)

        case "edit_content_name":
            if not text.strip(): await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً."); return
            cont_id = context.user_data.pop("edit_cont_id", None)
            context.user_data.pop("awaiting", None)
            db_edit_content_name(cont_id, text.strip())
            await msg.reply_text(f"✅ تم تحديث الاسم إلى <b>«{text.strip()}»</b>.", parse_mode=ParseMode.HTML)

        case "edit_content_data":
            cont_id = context.user_data.pop("edit_cont_id", None)
            context.user_data.pop("awaiting", None)
            ctype, cdata = _extract_content_from_message(msg)
            if not ctype: await msg.reply_text("❌ لم أتمكن من استخراج المحتوى."); return
            db_edit_content_data(cont_id, cdata, ctype)
            ctype_ar = {"text": "نص", "photo": "صورة", "video": "فيديو", "document": "ملف", "link": "رابط"}.get(ctype, ctype)
            await msg.reply_text(f"✅ تم تحديث المحتوى ({ctype_ar}).", parse_mode=ParseMode.HTML)

        case _: pass


async def _finish_group(update, context, group_id):
    """ينهي إضافة عناصر المجموعة ويظهر الفئة."""
    count = db_count_group_items(group_id)
    group = db_get_group(group_id)
    cat_id = group["category_id"] if group else context.user_data.get("new_group_cat", 0)
    context.user_data.pop("awaiting", None)
    context.user_data.pop("adding_to_group", None)
    context.user_data.pop("new_group_cat", None)
    await update.message.reply_text(
        f"✅ <b>تم حفظ المجموعة!</b>\n\n"
        f"المجموعة: <b>«{group['name'] if group else ''}»</b>\n"
        f"إجمالي الملفات: <b>{count}</b>",
        parse_mode=ParseMode.HTML,
    )
    await show_category(update, context, parent_id=cat_id, page=0, edit=False)


# ─────────────────────────────────────────────────────────────────
# ADMIN MENU ROUTER
# ─────────────────────────────────────────────────────────────────

async def admin_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db_is_admin(user_id): return
    text = update.message.text

    match text:
        case "👑 إدارة الأونرز":
            if db_is_owner(user_id): await show_owners_panel(update, context)
        case "📂 إدارة المحتوى":
            context.user_data.update({"path_stack": [], "current_cat": 0, "current_page": 0, "student_test_mode": False})
            await show_category(update, context, parent_id=0, page=0, edit=False)
        case "📢 إدارة القنوات":
            await show_channels_panel(update, context)
        case "⭐ إدارة VIP":
            await show_vip_panel(update, context)
        case "📊 إحصائيات":
            await show_statistics(update, context)
        case "💾 نسخ احتياطي":
            await send_db_backup(update, context)
        case "👁️ وضع الطالب":
            context.user_data.update({"student_test_mode": True, "path_stack": [], "current_cat": 0, "current_page": 0})
            await update.message.reply_text(
                "👁️ <b>وضع الطالب مفعّل</b>\n\nالآن ترى البوت كما يراه الطالب.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_admin_reply_keyboard_student_mode(),
            )
            await show_main_menu(update, context, edit=False)
        case "🔙 العودة إلى لوحة التحكم":
            context.user_data["student_test_mode"] = False
            await update.message.reply_text("✅ عدت إلى لوحة التحكم.", reply_markup=build_admin_reply_keyboard(user_id))
        case "🚪 خروج من لوحة التحكم":
            await update.message.reply_text("تم الخروج. اضغط /start للبدء.", reply_markup=ReplyKeyboardRemove())


# ─────────────────────────────────────────────────────────────────
# OWNERS PANEL
# ─────────────────────────────────────────────────────────────────

async def show_owners_panel(update, context):
    owners_db = db_get_all_owners()
    text = "👑 <b>إدارة الأونرز</b>\n\n<b>ثابتون:</b>\n"
    for oid in OWNER_IDS: text += f"• <code>{oid}</code> ⭐\n"
    if owners_db:
        text += "\n<b>مضافون من البوت:</b>\n"
        for row in owners_db: text += f"• <code>{row['user_id']}</code> — أضافه <code>{row['added_by']}</code>\n"
    else:
        text += "\n<i>لا يوجد أونرز مضافون.</i>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة أونر",  callback_data="owner_add")],
            [InlineKeyboardButton("🗑️ حذف أونر",   callback_data="owner_remove")],
        ]))

async def owner_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    user_id = update.effective_user.id
    if not db_is_owner(user_id):
        await query.answer("⛔ غير مصرح.", show_alert=True); return ConversationHandler.END
    if query.data == "owner_add":
        await query.edit_message_text("👑 <b>إضافة أونر</b>\n\nأرسل الـ User ID أو /cancel:", parse_mode=ParseMode.HTML)
        return ST_ADD_OWNER_ID
    if query.data == "owner_remove":
        if not db_get_all_owners():
            await query.answer("لا يوجد أونرز لحذفهم.", show_alert=True); return ConversationHandler.END
        await query.edit_message_text("🗑️ <b>حذف أونر</b>\n\nأرسل الـ User ID أو /cancel:", parse_mode=ParseMode.HTML)
        return ST_REMOVE_OWNER_ID
    return ConversationHandler.END

async def receive_new_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id; text = update.message.text.strip()
    if not text.isdigit(): await update.message.reply_text("❌ أرسل رقم ID صحيح."); return ST_ADD_OWNER_ID
    new_owner = int(text)
    if db_is_owner(new_owner): await update.message.reply_text("ℹ️ هذا المستخدم أونر بالفعل.")
    else:
        db_add_owner(new_owner, added_by=user_id)
        await update.message.reply_text(f"✅ تم إضافة <code>{new_owner}</code> كأونر.", parse_mode=ParseMode.HTML)
        try: await context.bot.send_message(chat_id=new_owner, text="🎉 تم منحك صلاحية <b>أونر</b>!\nاضغط /start.", parse_mode=ParseMode.HTML)
        except TelegramError: pass
    context.user_data.clear()
    await update.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(user_id))
    return ConversationHandler.END

async def receive_remove_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id; text = update.message.text.strip()
    if not text.isdigit(): await update.message.reply_text("❌ أرسل رقم ID صحيح."); return ST_REMOVE_OWNER_ID
    target = int(text)
    if target in OWNER_IDS:       await update.message.reply_text("⛔ لا يمكن حذف الأونرز الثابتين.")
    elif not db_is_owner(target): await update.message.reply_text("ℹ️ هذا المستخدم ليس أونر.")
    else:
        db_remove_owner(target)
        await update.message.reply_text(f"✅ تم حذف <code>{target}</code>.", parse_mode=ParseMode.HTML)
    context.user_data.clear()
    await update.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(user_id))
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# VIP PANEL
# ─────────────────────────────────────────────────────────────────

async def show_vip_panel(update, context):
    await update.message.reply_text(
        f"⭐ <b>إدارة VIP</b>\n\nعدد المشتركين: <b>{db_count_vip():,}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة مشترك VIP", callback_data="vip_add")],
            [InlineKeyboardButton("🗑️ إزالة مشترك VIP", callback_data="vip_del")],
        ]),
    )

async def vip_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if not db_is_admin(update.effective_user.id): return ConversationHandler.END
    if query.data == "vip_add":
        await query.edit_message_text("⭐ <b>إضافة VIP</b>\n\nأرسل الـ User ID أو /cancel:", parse_mode=ParseMode.HTML)
        return ST_VIP_ADD
    if query.data == "vip_del":
        await query.edit_message_text("🗑️ <b>إزالة VIP</b>\n\nأرسل الـ User ID أو /cancel:", parse_mode=ParseMode.HTML)
        return ST_VIP_DEL
    return ConversationHandler.END

async def receive_vip_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id; text = update.message.text.strip()
    if not text.isdigit(): await update.message.reply_text("❌ أرسل رقم ID صحيح."); return ST_VIP_ADD
    target = int(text); db_add_vip(target, added_by=user_id)
    await update.message.reply_text(f"✅ تم إضافة <code>{target}</code> كمشترك VIP.", parse_mode=ParseMode.HTML)
    try: await context.bot.send_message(chat_id=target, text="⭐ تهانينا! تم تفعيل اشتراكك في <b>قسم VIP</b>!\nاضغط /start.", parse_mode=ParseMode.HTML)
    except TelegramError: pass
    context.user_data.clear()
    await update.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(user_id))
    return ConversationHandler.END

async def receive_vip_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id; text = update.message.text.strip()
    if not text.isdigit(): await update.message.reply_text("❌ أرسل رقم ID صحيح."); return ST_VIP_DEL
    target = int(text); db_remove_vip(target)
    await update.message.reply_text(f"✅ تم إلغاء VIP للمستخدم <code>{target}</code>.", parse_mode=ParseMode.HTML)
    context.user_data.clear()
    await update.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(user_id))
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# CHANNELS
# ─────────────────────────────────────────────────────────────────

async def show_channels_panel(update, context):
    channels = db_get_channels()
    text = "📢 <b>إدارة قنوات/جروبات الاشتراك الإجباري</b>\n\n"
    if channels:
        for ch in channels: 
            text += f"• <b>{ch['channel_title']}</b> \n🔗 <a href='{ch['invite_link']}'>رابط الاشتراك</a>\n\n"
    else: 
        text += "<i>لا توجد قنوات حالياً.</i>"
        
    # بناء أزرار الحذف
    buttons = [[InlineKeyboardButton(f"🗑️ حذف {ch['channel_title'][:15]}", callback_data=f"a_rch_{ch['channel_username']}")] for ch in channels]
    buttons.append([InlineKeyboardButton("➕ إضافة قناة أو جروب", callback_data="a_ach_0")])
    
    msg_obj = update.message or (update.callback_query.message if update.callback_query else None)
    if update.callback_query:
        try: await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(buttons))
        except BadRequest: await msg_obj.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await msg_obj.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(buttons))


# ─────────────────────────────────────────────────────────────────
# STATISTICS
# ─────────────────────────────────────────────────────────────────

async def show_statistics(update, context):
    conn = get_db()
    cats      = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conts     = conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    grps      = conn.execute("SELECT COUNT(*) FROM content_groups").fetchone()[0]
    grp_items = conn.execute("SELECT COUNT(*) FROM group_items").fetchone()[0]
    channels  = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    conn.close()
    await update.message.reply_text(
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 المستخدمون: <b>{db_count_users():,}</b>\n"
        f"👑 المشرفون: <b>{db_count_admins():,}</b>\n"
        f"⭐ مشتركو VIP: <b>{db_count_vip():,}</b>\n"
        f"📂 الفئات: <b>{cats:,}</b>\n"
        f"📌 المحتويات: <b>{conts:,}</b>\n"
        f"📦 المجموعات: <b>{grps:,}</b>\n"
        f"🗂️ ملفات المجموعات: <b>{grp_items:,}</b>\n"
        f"📢 قنوات الاشتراك: <b>{channels:,}</b>\n\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────────────────────────
# BACKUP
# ─────────────────────────────────────────────────────────────────

async def send_db_backup(update, context):
    await update.message.reply_text("⏳ جاري إعداد النسخة الاحتياطية...")
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="💾 <b>نسخة احتياطية</b>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await update.message.reply_text(f"❌ فشل: {e}")


# ─────────────────────────────────────────────────────────────────
# ADD ADMIN
# ─────────────────────────────────────────────────────────────────

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👤 <b>إضافة مشرف</b>\n\nأرسل الـ User ID أو /cancel:",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove(),
    )
    return ST_ADD_ADMIN_ID

async def receive_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit(): await update.message.reply_text("❌ أرسل رقم صحيح أو /cancel."); return ST_ADD_ADMIN_ID
    new_admin_id = int(text)
    if new_admin_id in OWNER_IDS:    await update.message.reply_text("ℹ️ هذا المستخدم أونر أصلاً.")
    elif db_is_admin(new_admin_id):  await update.message.reply_text("ℹ️ هذا المستخدم مشرف بالفعل.")
    else:
        db_add_admin(new_admin_id)
        await update.message.reply_text(f"✅ تم إضافة <code>{new_admin_id}</code> كمشرف.", parse_mode=ParseMode.HTML)
    context.user_data.clear()
    await update.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(update.effective_user.id))
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# BROADCAST
# ─────────────────────────────────────────────────────────────────

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        f"📣 <b>إرسال جماعي</b>\n\nسيصل إلى <b>{db_count_users():,}</b> مستخدم.\n\nأرسل الرسالة أو /cancel:",
        parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove(),
    )
    return ST_BROADCAST_MSG

async def receive_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    context.user_data.update({"broadcast_from_chat": msg.chat_id, "broadcast_msg_id": msg.message_id})
    preview = f"📝 {msg.text[:80]}" if msg.text else "🖼️ صورة" if msg.photo else "🎥 فيديو" if msg.video else "📄 ملف" if msg.document else "رسالة"
    await msg.reply_text(
        f"<b>معاينة:</b> {preview}\n\nإرسال إلى <b>{db_count_users():,}</b> مستخدم؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ إرسال",  callback_data="bc_confirm"),
            InlineKeyboardButton("❌ إلغاء", callback_data="bc_cancel"),
        ]]),
    )
    return ST_BROADCAST_CONFIRM

async def broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    if query.data == "bc_cancel":
        context.user_data.clear(); await query.edit_message_text("❌ تم الإلغاء.")
        await query.message.reply_text("العودة:", reply_markup=build_admin_reply_keyboard(update.effective_user.id))
        return ConversationHandler.END
    from_chat = context.user_data.get("broadcast_from_chat")
    msg_id    = context.user_data.get("broadcast_msg_id")
    users     = db_get_all_users()
    await query.edit_message_text(f"⏳ جاري الإرسال إلى {len(users):,} مستخدم...")
    success = failed = 0
    for uid in users:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=from_chat, message_id=msg_id)
            success += 1
        except (Forbidden, TelegramError): failed += 1
        await asyncio.sleep(0.05)
    context.user_data.clear()
    await query.message.reply_text(
        f"✅ <b>اكتمل الإرسال</b>\n\n✔️ نجح: <b>{success:,}</b>\n✖️ فشل: <b>{failed:,}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_admin_reply_keyboard(update.effective_user.id),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# ADD CHANNEL
# ─────────────────────────────────────────────────────────────────

async def start_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    await query.edit_message_text(
        "📢 <b>إضافة قناة</b>\n\nأرسل الـ username (بدون @) أو /cancel:\n<i>مثال: mychannel</i>",
        parse_mode=ParseMode.HTML,
    )
    return ST_ADD_CHANNEL

async def receive_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "/cancel": 
        return await cancel_conversation(update, context)

    # استخراج المعرف أو الـ ID
    if text.startswith("http"):
        if "+" in text or "joinchat" in text:
            await update.message.reply_text("⚠️ للجروبات الخاصة، يجب إرسال الـ ID الخاص بالجروب (مثال: -100123456789) وليس الرابط.")
            return ST_ADD_CHANNEL
        username = text.split("/")[-1].lstrip("@")
    else:
        username = text.lstrip("@")

    try:
        # نحدد إذا كان المدخل ID (للجروب الخاص) أو يوزرنيم (للقناة العامة)
        chat_id = int(username) if username.lstrip("-").isdigit() else f"@{username}"
        
        # البوت يجلب بيانات الجروب/القناة (لازم يكون البوت مشرف فيها)
        chat = await context.bot.get_chat(chat_id)
        
        # تجهيز البيانات للحفظ
        save_id = str(chat.id)
        title = chat.title or save_id
        
        # استخراج أو إنشاء رابط الدعوة
        invite_link = chat.invite_link
        if not invite_link:
            if chat.username:
                invite_link = f"https://t.me/{chat.username}"
            else:
                try:
                    # لو الجروب خاص ومفيش رابط، البوت هيعمل رابط جديد ويحفظه
                    invite_link = await context.bot.export_chat_invite_link(chat.id)
                except TelegramError:
                    invite_link = "غير_متوفر"

        # حفظ في الداتا بيز
        db_add_channel(save_id, title, invite_link)
        context.user_data.clear()
        
        await update.message.reply_text(
            f"✅ <b>تمت الإضافة بنجاح!</b>\n\n"
            f"📌 <b>الاسم:</b> {title}\n🔗 <b>الرابط:</b> {invite_link}",
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END

    except Exception as e:
        await update.message.reply_text(
            f"❌ <b>فشل الوصول!</b>\n"
            f"تأكد أن البوت <b>مشرف (Admin)</b> في القناة/الجروب أولاً.\n\n<i>تفاصيل: {e}</i>",
            parse_mode=ParseMode.HTML
        )
        return ST_ADD_CHANNEL

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # لو كان بيضيف ملفات لمجموعة → احفظ ما تم
    if context.user_data.get("awaiting") == "add_group_item":
        group_id = context.user_data.get("adding_to_group")
        if group_id:
            await _finish_group(update, context, group_id)
            return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=build_admin_reply_keyboard(update.effective_user.id))
    return ConversationHandler.END

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/done لإنهاء إضافة الملفات للمجموعة"""
    if context.user_data.get("awaiting") == "add_group_item":
        group_id = context.user_data.get("adding_to_group")
        if group_id:
            await _finish_group(update, context, group_id); return
    await update.message.reply_text("لا توجد عملية جارية.", reply_markup=build_admin_reply_keyboard(update.effective_user.id))


# ─────────────────────────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=context.error)
    tb_str = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))[-2000:]
    for oid in OWNER_IDS:
        try: await context.bot.send_message(chat_id=oid, text=f"⚠️ <b>خطأ</b>\n\n<pre>{tb_str}</pre>", parse_mode=ParseMode.HTML)
        except Exception: pass
    if isinstance(update, Update) and update.effective_message:
        try: await update.effective_message.reply_text("❌ حدث خطأ. تم إشعار المشرف.")
        except Exception: pass


# ─────────────────────────────────────────────────────────────────
# BUILD APPLICATION
# ─────────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    add_admin_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^👤 إضافة مشرف$"), start_add_admin)],
        states={ST_ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_id)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)], allow_reentry=True,
    )
    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📣 إرسال رسالة جماعية$"), start_broadcast)],
        per_message=False,
        states={
            ST_BROADCAST_MSG: [MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
                receive_broadcast_msg,
            )],
            ST_BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm_callback, pattern="^bc_")],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)], allow_reentry=True,
    )
    add_channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_channel, pattern="^a_ach_0$")],
        per_message=False,
        states={ST_ADD_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_username)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)], allow_reentry=True,
    )
    owners_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(owner_panel_callback, pattern="^owner_(add|remove)$")],
        per_message=False,
        states={
            ST_ADD_OWNER_ID:    [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_owner_id)],
            ST_REMOVE_OWNER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_remove_owner_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)], allow_reentry=True,
    )
    vip_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(vip_panel_callback, pattern="^vip_(add|del)$")],
        per_message=False,
        states={
            ST_VIP_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vip_add)],
            ST_VIP_DEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vip_del)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)], allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("done",  done_command))
    app.add_handler(owners_conv)
    app.add_handler(vip_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(add_channel_conv)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(
            "^(📂 إدارة المحتوى|📢 إدارة القنوات|📊 إحصائيات"
            "|💾 نسخ احتياطي|👁️ وضع الطالب|👑 إدارة الأونرز"
            "|⭐ إدارة VIP|📣 إرسال رسالة جماعية|👤 إضافة مشرف"
            "|🔙 العودة إلى لوحة التحكم|🚪 خروج من لوحة التحكم)$"
        ),
        admin_menu_router,
    ))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL) & ~filters.COMMAND,
        handle_awaiting_input,
    ))
    app.add_error_handler(error_handler)
    return app


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    init_db()
    logger.info("🚀 Starting LMS Bot...")
    app = build_application()
    logger.info("✅ Bot is running.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
