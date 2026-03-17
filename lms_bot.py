#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║          نظام إدارة التعلم (LMS) - بوت تيليغرام                ║
║          Senior Python Developer & Cybersecurity Build          ║
║          python-telegram-bot v20+ | SQLite3 | Async             ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import sqlite3
import traceback
from datetime import datetime
from functools import wraps
from typing import Optional

from dotenv import load_dotenv
from telegram import (
    Bot,
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    Message,
    PhotoSize,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    Video,
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

# ─────────────────────────────────────────────────────────────────
# 🔧 CONFIGURATION & LOGGING
# ─────────────────────────────────────────────────────────────────

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
OWNER_IDS = set(
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "123456789").split(",")
    if x.strip().isdigit()
)
DB_PATH = "/app/data/lms_school.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# 📊 CONVERSATION STATES
# ─────────────────────────────────────────────────────────────────

(
    # Category states
    ST_CAT_NAME,
    ST_CAT_EDIT_NAME,
    ST_CAT_REORDER,

    # Content states
    ST_CONTENT_NAME,
    ST_CONTENT_TYPE,
    ST_CONTENT_DATA,
    ST_CONTENT_EDIT_NAME,
    ST_CONTENT_EDIT_DATA,

    # Broadcast states
    ST_BROADCAST_MSG,
    ST_BROADCAST_CONFIRM,

    # Admin management
    ST_ADD_ADMIN_ID,

    # Channel management
    ST_ADD_CHANNEL,
    # Owner management
    ST_ADD_OWNER_ID,
    ST_REMOVE_OWNER_ID,
) = range(14)

# ─────────────────────────────────────────────────────────────────
# 🗄️ DATABASE LAYER
# ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Get a thread-local DB connection with Row factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """Initialize all tables on first run."""
    conn = get_db()
    cur = conn.cursor()

    cur.executescript("""
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

        CREATE TABLE IF NOT EXISTS channels (
            channel_username TEXT PRIMARY KEY,
            channel_title    TEXT,
            invite_link      TEXT
        );

        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_id   INTEGER NOT NULL DEFAULT 0,
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
    """)
    conn.commit()
    conn.close()
    logger.info("✅ Database initialized successfully.")


# ── Users ──────────────────────────────────────────────────────

def db_upsert_user(user_id: int, first_name: str, username: Optional[str]) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, first_name, username) VALUES (?,?,?)",
        (user_id, first_name, username),
    )
    conn.commit()
    conn.close()


def db_get_all_users() -> list:
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def db_count_users() -> int:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count


# ── Admins ─────────────────────────────────────────────────────

def db_is_admin(user_id: int) -> bool:
    if db_is_owner(user_id):
        return True
    conn = get_db()
    row = conn.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None


def db_add_admin(user_id: int) -> None:
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def db_remove_admin(user_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def db_get_all_admins() -> list:
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM admins").fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def db_count_admins() -> int:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0]
    conn.close()
    return count

def db_is_owner(user_id: int) -> bool:
    if user_id in OWNER_IDS:
        return True
    conn = get_db()
    row = conn.execute("SELECT 1 FROM owners WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def db_add_owner(user_id: int, added_by: int) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO owners (user_id, added_by) VALUES (?,?)",
        (user_id, added_by),
    )
    conn.commit()
    conn.close()

def db_remove_owner(user_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM owners WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def db_get_all_owners() -> list:
    conn = get_db()
    rows = conn.execute("SELECT user_id, added_by, added_at FROM owners").fetchall()
    conn.close()
    return rows


# ── Channels ───────────────────────────────────────────────────

def db_get_channels() -> list:
    conn = get_db()
    rows = conn.execute("SELECT * FROM channels").fetchall()
    conn.close()
    return rows


def db_add_channel(username: str, title: str, invite_link: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO channels (channel_username, channel_title, invite_link) VALUES (?,?,?)",
        (username, title, invite_link),
    )
    conn.commit()
    conn.close()


def db_remove_channel(username: str) -> None:
    conn = get_db()
    conn.execute("DELETE FROM channels WHERE channel_username=?", (username,))
    conn.commit()
    conn.close()


# ── Categories ─────────────────────────────────────────────────

def db_get_subcategories(parent_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM categories WHERE parent_id=? ORDER BY sort_order, id",
        (parent_id,),
    ).fetchall()
    conn.close()
    return rows


def db_get_category(cat_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM categories WHERE id=?", (cat_id,)).fetchone()
    conn.close()
    return row


def db_add_category(parent_id: int, name: str) -> int:
    conn = get_db()
    conn.execute(
        "INSERT INTO categories (parent_id, name, sort_order) VALUES (?,?,?)",
        (parent_id, name, 0),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return new_id


def db_edit_category_name(cat_id: int, name: str) -> None:
    conn = get_db()
    conn.execute("UPDATE categories SET name=? WHERE id=?", (name, cat_id))
    conn.commit()
    conn.close()


def db_delete_category(cat_id: int) -> None:
    """Cascading delete handled by FK ON DELETE CASCADE."""
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()


def db_update_category_order(cat_id: int, sort_order: int) -> None:
    conn = get_db()
    conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (sort_order, cat_id))
    conn.commit()
    conn.close()


# ── Contents ───────────────────────────────────────────────────

def db_get_contents(category_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM contents WHERE category_id=? ORDER BY sort_order, id",
        (category_id,),
    ).fetchall()
    conn.close()
    return rows


def db_get_content(content_id: int) -> Optional[sqlite3.Row]:
    conn = get_db()
    row = conn.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    conn.close()
    return row


def db_add_content(
    category_id: int,
    content_type: str,
    content_data: str,
    name: str,
) -> int:
    conn = get_db()
    conn.execute(
        "INSERT INTO contents (category_id, content_type, content_data, name, sort_order) VALUES (?,?,?,?,0)",
        (category_id, content_type, content_data, name),
    )
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return new_id


def db_edit_content_name(content_id: int, name: str) -> None:
    conn = get_db()
    conn.execute("UPDATE contents SET name=? WHERE id=?", (name, content_id))
    conn.commit()
    conn.close()


def db_edit_content_data(content_id: int, content_data: str, content_type: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE contents SET content_data=?, content_type=? WHERE id=?",
        (content_data, content_type, content_id),
    )
    conn.commit()
    conn.close()


def db_delete_content(content_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM contents WHERE id=?", (content_id,))
    conn.commit()
    conn.close()


def db_update_content_order(content_id: int, sort_order: int) -> None:
    conn = get_db()
    conn.execute("UPDATE contents SET sort_order=? WHERE id=?", (sort_order, content_id))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────
# 🔐 HELPERS & DECORATORS
# ─────────────────────────────────────────────────────────────────

CONTENT_EMOJI = {
    "text": "📝",
    "photo": "🖼️",
    "video": "🎥",
    "document": "📄",
    "link": "🔗",
}


def is_admin_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if current session is admin and not in student-test mode."""
    user_id = context._user_id if hasattr(context, "_user_id") else None
    if user_id is None:
        return False
    in_student_test = context.user_data.get("student_test_mode", False)
    return db_is_admin(user_id) and not in_student_test


async def check_subscription(bot: Bot, user_id: int) -> tuple[bool, list]:
    """
    Returns (all_subscribed: bool, missing_channels: list[Row])
    """
    channels = db_get_channels()
    if not channels:
        return True, []

    missing = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(
                chat_id=f"@{ch['channel_username']}", user_id=user_id
            )
            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
            ):
                missing.append(ch)
        except TelegramError:
            missing.append(ch)

    return len(missing) == 0, missing


def subscription_required_keyboard(missing: list) -> InlineKeyboardMarkup:
    """Build keyboard with channel links + check button."""
    buttons = []
    for ch in missing:
        link = ch["invite_link"] or f"https://t.me/{ch['channel_username']}"
        title = ch["channel_title"] or f"@{ch['channel_username']}"
        buttons.append([InlineKeyboardButton(f"📢 {title}", url=link)])
    buttons.append([InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)


def build_admin_reply_keyboard(user_id: int = 0) -> ReplyKeyboardMarkup:
    keyboard = [
        ["📂 إدارة المحتوى", "📢 إدارة القنوات"],
        ["📣 إرسال رسالة جماعية", "👤 إضافة مشرف"],
        ["📊 إحصائيات", "💾 نسخ احتياطي"],
        ["👁️ وضع الطالب", "🚪 خروج من لوحة التحكم"],
    ]
    if db_is_owner(user_id):
        keyboard.insert(2, ["👑 إدارة الأونرز"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def build_admin_reply_keyboard_student_mode() -> ReplyKeyboardMarkup:
    keyboard = [["🔙 العودة إلى لوحة التحكم"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def send_content_to_user(
    bot: Bot,
    chat_id: int,
    content: sqlite3.Row,
    reply_markup=None,
) -> None:
    """Dispatch content to user based on its type."""
    ctype = content["content_type"]
    cdata = content["content_data"]
    cname = content["name"]

    match ctype:
        case "text":
            await bot.send_message(
                chat_id=chat_id,
                text=f"<b>{cname}</b>\n\n{cdata}",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

        case "photo":
            await bot.send_photo(
                chat_id=chat_id,
                photo=cdata,
                caption=f"<b>{cname}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

        case "video":
            await bot.send_video(
                chat_id=chat_id,
                video=cdata,
                caption=f"<b>{cname}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

        case "document":
            await bot.send_document(
                chat_id=chat_id,
                document=cdata,
                caption=f"<b>{cname}</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

        case "link":
            # Attempt to parse t.me/c/CHANNEL_ID/MSG_ID format
            if "t.me/c/" in cdata or "t.me/" in cdata:
                try:
                    parts = cdata.rstrip("/").split("/")
                    msg_id = int(parts[-1])
                    if "t.me/c/" in cdata:
                        channel_id = int(f"-100{parts[-2]}")
                    else:
                        channel_username = parts[-2]
                        channel_id = f"@{channel_username}"
                    await bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=channel_id,
                        message_id=msg_id,
                        reply_markup=reply_markup,
                    )
                    return
                except Exception:
                    pass
            # Fallback: send as clickable link
            await bot.send_message(
                chat_id=chat_id,
                text=f"<b>{cname}</b>\n\n🔗 <a href='{cdata}'>افتح الرابط</a>",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )

        case _:
            await bot.send_message(
                chat_id=chat_id,
                text=f"❓ نوع محتوى غير معروف: {ctype}",
                reply_markup=reply_markup,
            )


# ─────────────────────────────────────────────────────────────────
# 📁 NAVIGATION KEYBOARDS
# ─────────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int = 28) -> str:
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def build_browse_keyboard(
    parent_id: int,
    is_admin: bool,
    path_stack: list,
) -> InlineKeyboardMarkup:
    """
    Build the dynamic navigation keyboard for both admins and students.
    Callback data budget: 64 bytes max.
    Prefixes:
      nav_  → navigate into category (nav_<id>)
      cnt_  → open content item (cnt_<id>)
      back_ → go back (back_<parent_id>)
      a_nc_ → admin: new category (a_nc_<parent_id>)
      a_nx_ → admin: new content (a_nx_<parent_id>)
      a_ec_ → admin: edit category name (a_ec_<cat_id>)
      a_dc_ → admin: delete category (a_dc_<cat_id>)
      a_rc_ → admin: reorder children (a_rc_<parent_id>)
      a_vc_ → admin: view/manage content list (a_vc_<cat_id>)
    """
    buttons = []

    subcats = db_get_subcategories(parent_id)
    contents = db_get_contents(parent_id)

    for cat in subcats:
        label = f"📁 {_truncate(cat['name'])}"
        cb = f"nav_{cat['id']}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    for cont in contents:
        emoji = CONTENT_EMOJI.get(cont["content_type"], "📌")
        label = f"{emoji} {_truncate(cont['name'])}"
        cb = f"cnt_{cont['id']}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    # Admin-only actions row
    if is_admin:
        admin_row1 = [
            InlineKeyboardButton("➕ فئة فرعية", callback_data=f"a_nc_{parent_id}"),
            InlineKeyboardButton("➕ محتوى", callback_data=f"a_nx_{parent_id}"),
        ]
        buttons.append(admin_row1)

        if parent_id != 0:
            admin_row2 = [
                InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"a_ec_{parent_id}"),
                InlineKeyboardButton("🗑️ حذف الفئة", callback_data=f"a_dc_{parent_id}"),
            ]
            buttons.append(admin_row2)
            buttons.append(
                [InlineKeyboardButton("🔃 إعادة الترتيب", callback_data=f"a_rc_{parent_id}")]
            )

    # Back button
    if path_stack:
        prev = path_stack[-1]
        buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"back_{prev}")])
    elif parent_id != 0:
        buttons.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="back_0")])

    return InlineKeyboardMarkup(buttons)


def build_content_admin_keyboard(content_id: int, category_id: int) -> InlineKeyboardMarkup:
    """Admin keyboard shown when viewing a content item."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"a_en_{content_id}"),
            InlineKeyboardButton("🔄 تعديل الملف/البيانات", callback_data=f"a_ed_{content_id}"),
        ],
        [InlineKeyboardButton("🗑️ حذف المحتوى", callback_data=f"a_dl_{content_id}")],
        [InlineKeyboardButton("🔙 رجوع للفئة", callback_data=f"nav_{category_id}")],
    ])


# ─────────────────────────────────────────────────────────────────
# 🚀 /start COMMAND
# ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db_upsert_user(user.id, user.first_name, user.username)
    context.user_data["_user_id_cache"] = user.id

    # Force subscription check
    ok, missing = await check_subscription(context.bot, user.id)
    if not ok:
        await update.message.reply_text(
            "🔒 <b>يجب عليك الاشتراك في القنوات التالية للمتابعة:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=subscription_required_keyboard(missing),
        )
        return

    context.user_data["path_stack"] = []
    context.user_data["current_cat"] = 0

    admin_mode = db_is_admin(user.id) and not context.user_data.get("student_test_mode", False)

    welcome = (
        f"👋 <b>مرحباً {user.first_name}!</b>\n\n"
        "🎓 أهلاً بك في بوت المنهج التعليمي.\n"
        "استخدم القائمة أدناه للتنقل بين المحتويات."
    )

    if admin_mode:
        await update.message.reply_text(
            f"👑 <b>مرحباً مشرف {user.first_name}!</b>\n"
            "لوحة تحكم المشرف جاهزة.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_reply_keyboard(update.effective_user.id),
        )
    else:
        await update.message.reply_text(
            welcome,
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        await show_category(update, context, parent_id=0, edit=False)


async def show_category(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_id: int,
    edit: bool = True,
) -> None:
    """Send or edit the category browsing message."""
    user_id = update.effective_user.id
    admin = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
    path_stack = context.user_data.get("path_stack", [])

    if parent_id == 0:
        title = "🏠 <b>الرئيسية — اختر فئة:</b>"
    else:
        cat = db_get_category(parent_id)
        title = f"📂 <b>{cat['name']}</b>" if cat else "📂 <b>الفئة</b>"

    keyboard = build_browse_keyboard(parent_id, admin, path_stack)

    subcats = db_get_subcategories(parent_id)
    contents = db_get_contents(parent_id)
    if not subcats and not contents:
        title += "\n\n<i>لا يوجد محتوى في هذه الفئة بعد.</i>"

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=title,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except BadRequest:
            pass
    else:
        msg_obj = update.message or (update.callback_query.message if update.callback_query else None)
        if msg_obj:
            await msg_obj.reply_text(
                text=title,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )


# ─────────────────────────────────────────────────────────────────
# 🔄 CALLBACK QUERY — MAIN ROUTER
# ─────────────────────────────────────────────────────────────────

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    context.user_data["_user_id_cache"] = user_id

    # ── Force sub check ────────────────────────────────────────
    if data == "check_sub":
        ok, missing = await check_subscription(context.bot, user_id)
        if ok:
            db_upsert_user(
                user_id,
                update.effective_user.first_name,
                update.effective_user.username,
            )
            context.user_data["path_stack"] = []
            context.user_data["current_cat"] = 0
            await query.edit_message_text("✅ شكراً! تم التحقق من اشتراكك. اضغط /start للبدء.")
        else:
            await query.edit_message_reply_markup(
                reply_markup=subscription_required_keyboard(missing)
            )
        return

    # ── Force sub check on every navigation ───────────────────
    if not data.startswith("a_") and data not in ("check_sub",):
        ok, missing = await check_subscription(context.bot, user_id)
        if not ok:
            await query.edit_message_text(
                "🔒 <b>يجب الاشتراك في القنوات أولاً:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=subscription_required_keyboard(missing),
            )
            return

    match data.split("_")[0]:

        case "nav":
            cat_id = int(data.split("_")[1])
            prev_cat = context.user_data.get("current_cat", 0)
            stack = context.user_data.get("path_stack", [])
            stack.append(prev_cat)
            context.user_data["path_stack"] = stack
            context.user_data["current_cat"] = cat_id
            await show_category(update, context, parent_id=cat_id, edit=True)

        case "back":
            target = int(data.split("_")[1])
            stack = context.user_data.get("path_stack", [])
            # Pop until we reach target
            while stack and stack[-1] != target:
                stack.pop()
            if stack:
                stack.pop()
            context.user_data["path_stack"] = stack
            context.user_data["current_cat"] = target
            await show_category(update, context, parent_id=target, edit=True)

        case "cnt":
            content_id = int(data.split("_")[1])
            content = db_get_content(content_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True)
                return
            admin = db_is_admin(user_id) and not context.user_data.get("student_test_mode", False)
            rm = build_content_admin_keyboard(content_id, content["category_id"]) if admin else None
            await send_content_to_user(context.bot, query.message.chat_id, content, reply_markup=rm)

        # ── Admin callbacks ────────────────────────────────────
        case "a":
            if not db_is_admin(user_id):
                await query.answer("⛔ غير مصرح.", show_alert=True)
                return
            await handle_admin_callback(update, context, data)

        case _:
            pass


async def handle_admin_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Route admin-specific inline callbacks that start with 'a_'."""
    query = update.callback_query
    user_id = update.effective_user.id

    # Parse: a_XX_<id>
    parts = data.split("_")
    # parts[0] = 'a', parts[1] = action_code, parts[2] = id
    action = parts[1]
    item_id = int(parts[2]) if len(parts) > 2 else 0

    match action:

        # ── New sub-category ──────────────────────────────────
        case "nc":
            context.user_data["new_cat_parent"] = item_id
            await query.edit_message_text(
                "📁 <b>إضافة فئة فرعية جديدة</b>\n\nأرسل اسم الفئة الجديدة:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{item_id}")]]
                ),
            )
            context.user_data["awaiting"] = "new_category_name"

        # ── New content ───────────────────────────────────────
        case "nx":
            context.user_data["new_cont_cat"] = item_id
            context.user_data["awaiting"] = "new_content_name"
            await query.edit_message_text(
                "📌 <b>إضافة محتوى جديد — الخطوة 1/3</b>\n\nأرسل <b>اسم</b> المحتوى:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{item_id}")]]
                ),
            )

        # ── Edit category name ────────────────────────────────
        case "ec":
            cat = db_get_category(item_id)
            if not cat:
                await query.answer("❌ الفئة غير موجودة.", show_alert=True)
                return
            context.user_data["edit_cat_id"] = item_id
            context.user_data["awaiting"] = "edit_category_name"
            parent = cat["parent_id"]
            await query.edit_message_text(
                f"✏️ <b>تعديل اسم الفئة</b>\n\nالاسم الحالي: <i>{cat['name']}</i>\n\nأرسل الاسم الجديد:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{parent}")]]
                ),
            )

        # ── Delete category ───────────────────────────────────
        case "dc":
            cat = db_get_category(item_id)
            if not cat:
                await query.answer("❌ الفئة غير موجودة.", show_alert=True)
                return
            parent = cat["parent_id"]
            await query.edit_message_text(
                f"⚠️ <b>تأكيد حذف الفئة</b>\n\n"
                f"سيتم حذف الفئة <b>«{cat['name']}»</b> مع جميع محتوياتها الفرعية بشكل نهائي.\n\n"
                f"هل أنت متأكد؟",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ نعم، احذف", callback_data=f"a_cy_{item_id}"),
                        InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{parent}"),
                    ]
                ]),
            )

        case "cy":
            cat = db_get_category(item_id)
            if cat:
                parent = cat["parent_id"]
                db_delete_category(item_id)
                await query.answer("✅ تم الحذف.", show_alert=True)
                context.user_data["current_cat"] = parent
                stack = context.user_data.get("path_stack", [])
                if stack and stack[-1] == item_id:
                    stack.pop()
                context.user_data["path_stack"] = stack
                await show_category(update, context, parent_id=parent, edit=True)
            else:
                await query.answer("❌ الفئة غير موجودة.", show_alert=True)

        # ── Reorder children ──────────────────────────────────
        case "rc":
            await show_reorder_menu(update, context, item_id)

        # ── Reorder move up ───────────────────────────────────
        case "ru":
            # item_id here = cat_id to move, stored parent in user_data
            parent_id = context.user_data.get("reorder_parent", 0)
            _reorder_item(item_id, "cat", -1)
            await show_reorder_menu(update, context, parent_id)

        case "rd":
            parent_id = context.user_data.get("reorder_parent", 0)
            _reorder_item(item_id, "cat", +1)
            await show_reorder_menu(update, context, parent_id)

        # ── Edit content name ─────────────────────────────────
        case "en":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True)
                return
            context.user_data["edit_cont_id"] = item_id
            context.user_data["awaiting"] = "edit_content_name"
            await query.message.reply_text(
                f"✏️ <b>تعديل اسم المحتوى</b>\n\nالاسم الحالي: <i>{content['name']}</i>\n\nأرسل الاسم الجديد:",
                parse_mode=ParseMode.HTML,
            )

        # ── Edit content data ─────────────────────────────────
        case "ed":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True)
                return
            context.user_data["edit_cont_id"] = item_id
            context.user_data["awaiting"] = "edit_content_data"
            ctype_ar = {
                "text": "نص", "photo": "صورة", "video": "فيديو",
                "document": "ملف", "link": "رابط",
            }.get(content["content_type"], content["content_type"])
            await query.message.reply_text(
                f"🔄 <b>تعديل بيانات المحتوى</b>\n\nالنوع الحالي: <b>{ctype_ar}</b>\n\n"
                f"أرسل المحتوى الجديد (صورة / فيديو / ملف / نص / رابط):",
                parse_mode=ParseMode.HTML,
            )

        # ── Delete content ────────────────────────────────────
        case "dl":
            content = db_get_content(item_id)
            if not content:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True)
                return
            cat_id = content["category_id"]
            await query.message.reply_text(
                f"⚠️ <b>تأكيد الحذف</b>\n\nحذف المحتوى: <b>«{content['name']}»</b>؟",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ نعم، احذف", callback_data=f"a_dy_{item_id}"),
                        InlineKeyboardButton("❌ إلغاء", callback_data=f"nav_{cat_id}"),
                    ]
                ]),
            )

        case "dy":
            content = db_get_content(item_id)
            if content:
                cat_id = content["category_id"]
                db_delete_content(item_id)
                await query.answer("✅ تم حذف المحتوى.", show_alert=True)
                await show_category(update, context, parent_id=cat_id, edit=False)
            else:
                await query.answer("❌ المحتوى غير موجود.", show_alert=True)

        # ── Remove channel confirm ────────────────────────────
        case "rch":
            username = "_".join(parts[2:])  # channel usernames may contain nothing weird
            db_remove_channel(username)
            await query.answer(f"✅ تم حذف @{username}.", show_alert=True)
            await show_channels_panel(update, context)

        case _:
            await query.answer("❓ أمر غير معروف.", show_alert=True)


def _reorder_item(item_id: int, item_type: str, direction: int) -> None:
    """Shift sort_order by direction (+1 down, -1 up)."""
    conn = get_db()
    if item_type == "cat":
        row = conn.execute("SELECT sort_order FROM categories WHERE id=?", (item_id,)).fetchone()
        if row:
            new_order = max(0, row[0] + direction)
            conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (new_order, item_id))
    elif item_type == "cont":
        row = conn.execute("SELECT sort_order FROM contents WHERE id=?", (item_id,)).fetchone()
        if row:
            new_order = max(0, row[0] + direction)
            conn.execute("UPDATE contents SET sort_order=? WHERE id=?", (new_order, item_id))
    conn.commit()
    conn.close()


async def show_reorder_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    parent_id: int,
) -> None:
    query = update.callback_query
    context.user_data["reorder_parent"] = parent_id
    subcats = db_get_subcategories(parent_id)
    contents = db_get_contents(parent_id)

    if not subcats and not contents:
        await query.answer("لا توجد عناصر لإعادة ترتيبها.", show_alert=True)
        return

    buttons = []
    for cat in subcats:
        label = f"📁 {_truncate(cat['name'], 20)}"
        buttons.append([
            InlineKeyboardButton(f"⬆️ {label}", callback_data=f"a_ru_{cat['id']}"),
            InlineKeyboardButton(f"⬇️ {label}", callback_data=f"a_rd_{cat['id']}"),
        ])
    for cont in contents:
        emoji = CONTENT_EMOJI.get(cont["content_type"], "📌")
        label = f"{emoji} {_truncate(cont['name'], 20)}"
        buttons.append([
            InlineKeyboardButton(f"⬆️ {label}", callback_data=f"a_rcu_{cont['id']}"),
            InlineKeyboardButton(f"⬇️ {label}", callback_data=f"a_rcd_{cont['id']}"),
        ])

    buttons.append([InlineKeyboardButton("✅ تم", callback_data=f"nav_{parent_id}")])
    await query.edit_message_text(
        "🔃 <b>إعادة ترتيب العناصر</b>\n\nاضغط ⬆️/⬇️ لتحريك العنصر:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ─────────────────────────────────────────────────────────────────
# 💬 MESSAGE HANDLER — awaiting inputs from inline flows
# ─────────────────────────────────────────────────────────────────

async def handle_awaiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle plain message inputs that are expected because of an 'awaiting' key
    set via inline button interactions (non-ConversationHandler path).
    """
    user_id = update.effective_user.id
    if not db_is_admin(user_id):
        return

    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    msg = update.message
    text = msg.text or ""

    match awaiting:

        case "new_category_name":
            if not text.strip():
                await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً.")
                return
            parent_id = context.user_data.get("new_cat_parent", 0)
            db_add_category(parent_id, text.strip())
            context.user_data.pop("awaiting", None)
            context.user_data.pop("new_cat_parent", None)
            await msg.reply_text(
                f"✅ تم إضافة الفئة <b>«{text.strip()}»</b> بنجاح.",
                parse_mode=ParseMode.HTML,
            )
            await show_category(update, context, parent_id=parent_id, edit=False)

        case "new_content_name":
            if not text.strip():
                await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً.")
                return
            context.user_data["new_cont_name"] = text.strip()
            context.user_data["awaiting"] = "new_content_data"
            await msg.reply_text(
                "📎 <b>الخطوة 2/3 — أرسل المحتوى:</b>\n\n"
                "أرسل صورة 🖼️ أو فيديو 🎥 أو ملف 📄 أو نص 📝 أو رابط 🔗",
                parse_mode=ParseMode.HTML,
            )

        case "new_content_data":
            cat_id = context.user_data.get("new_cont_cat", 0)
            name = context.user_data.get("new_cont_name", "محتوى")
            ctype, cdata = _extract_content_from_message(msg)
            if not ctype:
                await msg.reply_text("❌ لم أتمكن من استخراج المحتوى. أرسل ملف أو صورة أو نص.")
                return
            db_add_content(cat_id, ctype, cdata, name)
            context.user_data.pop("awaiting", None)
            context.user_data.pop("new_cont_cat", None)
            context.user_data.pop("new_cont_name", None)
            ctype_ar = {
                "text": "نص", "photo": "صورة", "video": "فيديو",
                "document": "ملف", "link": "رابط",
            }.get(ctype, ctype)
            await msg.reply_text(
                f"✅ تم إضافة المحتوى <b>«{name}»</b> ({ctype_ar}) بنجاح.",
                parse_mode=ParseMode.HTML,
            )
            await show_category(update, context, parent_id=cat_id, edit=False)

        case "edit_category_name":
            if not text.strip():
                await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً.")
                return
            cat_id = context.user_data.get("edit_cat_id")
            db_edit_category_name(cat_id, text.strip())
            context.user_data.pop("awaiting", None)
            context.user_data.pop("edit_cat_id", None)
            await msg.reply_text(
                f"✅ تم تحديث الاسم إلى <b>«{text.strip()}»</b>.",
                parse_mode=ParseMode.HTML,
            )
            await show_category(update, context, parent_id=cat_id, edit=False)

        case "edit_content_name":
            if not text.strip():
                await msg.reply_text("⚠️ الاسم لا يمكن أن يكون فارغاً.")
                return
            cont_id = context.user_data.get("edit_cont_id")
            db_edit_content_name(cont_id, text.strip())
            context.user_data.pop("awaiting", None)
            context.user_data.pop("edit_cont_id", None)
            await msg.reply_text(
                f"✅ تم تحديث الاسم إلى <b>«{text.strip()}»</b>.",
                parse_mode=ParseMode.HTML,
            )

        case "edit_content_data":
            cont_id = context.user_data.get("edit_cont_id")
            ctype, cdata = _extract_content_from_message(msg)
            if not ctype:
                await msg.reply_text("❌ لم أتمكن من استخراج المحتوى.")
                return
            db_edit_content_data(cont_id, cdata, ctype)
            context.user_data.pop("awaiting", None)
            context.user_data.pop("edit_cont_id", None)
            ctype_ar = {
                "text": "نص", "photo": "صورة", "video": "فيديو",
                "document": "ملف", "link": "رابط",
            }.get(ctype, ctype)
            await msg.reply_text(
                f"✅ تم تحديث بيانات المحتوى ({ctype_ar}) بنجاح.",
                parse_mode=ParseMode.HTML,
            )

        case _:
            pass


def _extract_content_from_message(msg: Message) -> tuple[Optional[str], Optional[str]]:
    """Extract (content_type, content_data) from a Telegram message."""
    if msg.photo:
        return "photo", msg.photo[-1].file_id
    if msg.video:
        return "video", msg.video.file_id
    if msg.document:
        return "document", msg.document.file_id
    if msg.text:
        t = msg.text.strip()
        if t.startswith("http") or t.startswith("t.me") or "t.me/" in t:
            return "link", t
        return "text", t
    return None, None


# ─────────────────────────────────────────────────────────────────
# 🔧 ADMIN REPLY KEYBOARD HANDLERS
# ─────────────────────────────────────────────────────────────────

async def admin_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route admin ReplyKeyboard button presses."""
    user_id = update.effective_user.id
    if not db_is_admin(user_id):
        return

    text = update.message.text
    
    match text:

        case "👑 إدارة الأونرز":
            if db_is_owner(user_id):
                await show_owners_panel(update, context)

        case "📂 إدارة المحتوى":
            context.user_data["path_stack"] = []
            context.user_data["current_cat"] = 0
            # Ensure not in student mode for admin content browsing
            context.user_data["student_test_mode"] = False
            await show_category(update, context, parent_id=0, edit=False)

        case "📢 إدارة القنوات":
            await show_channels_panel(update, context)

        case "📣 إرسال رسالة جماعية":
            await start_broadcast(update, context)

        case "👤 إضافة مشرف":
            await start_add_admin(update, context)

        case "📊 إحصائيات":
            await show_statistics(update, context)

        case "💾 نسخ احتياطي":
            await send_db_backup(update, context)

        case "👁️ وضع الطالب":
            context.user_data["student_test_mode"] = True
            context.user_data["path_stack"] = []
            context.user_data["current_cat"] = 0
            await update.message.reply_text(
                "👁️ <b>تم تفعيل وضع الطالب</b>\n\n"
                "الآن ترى البوت كما يراه الطالب.\n"
                "اضغط على الزر أدناه للعودة.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_admin_reply_keyboard_student_mode(),
            )
            await show_category(update, context, parent_id=0, edit=False)

        case "🔙 العودة إلى لوحة التحكم":
            context.user_data["student_test_mode"] = False
            await update.message.reply_text(
                "✅ عدت إلى لوحة التحكم.",
                reply_markup=build_admin_reply_keyboard(update.effective_user.id),
            )

        case "🚪 خروج من لوحة التحكم":
            await update.message.reply_text(
                "تم الخروج. اضغط /start للبدء من جديد.",
                reply_markup=ReplyKeyboardRemove(),
            )

async def show_owners_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not db_is_owner(user_id):
        await update.message.reply_text("⛔ هذا الأمر للأونرز فقط.")
        return

    owners_db = db_get_all_owners()

    text = "👑 <b>إدارة الأونرز</b>\n\n"
    text += "<b>أونرز ثابتون (من الإعدادات):</b>\n"
    for oid in OWNER_IDS:
        text += f"• <code>{oid}</code> ⭐\n"

    if owners_db:
        text += "\n<b>أونرز مضافون من البوت:</b>\n"
        for row in owners_db:
            text += (
                f"• <code>{row['user_id']}</code> "
                f"— أضافه: <code>{row['added_by']}</code> "
                f"— {row['added_at']}\n"
            )
    else:
        text += "\n<i>لا يوجد أونرز مضافون من البوت بعد.</i>\n"

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ إضافة أونر", callback_data="owner_add")],
            [InlineKeyboardButton("🗑️ حذف أونر", callback_data="owner_remove")],
        ]),
    )


async def owner_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    if not db_is_owner(user_id):
        await query.answer("⛔ غير مصرح.", show_alert=True)
        return ConversationHandler.END

    if query.data == "owner_add":
        await query.edit_message_text(
            "👑 <b>إضافة أونر جديد</b>\n\n"
            "أرسل الـ <b>User ID</b> الرقمي:\n\n"
            "أو /cancel للإلغاء.",
            parse_mode=ParseMode.HTML,
        )
        return ST_ADD_OWNER_ID

    if query.data == "owner_remove":
        owners_db = db_get_all_owners()
        if not owners_db:
            await query.answer("لا يوجد أونرز لحذفهم.", show_alert=True)
            return ConversationHandler.END
        await query.edit_message_text(
            "🗑️ <b>حذف أونر</b>\n\n"
            "أرسل الـ <b>User ID</b> للأونر المراد حذفه:\n\n"
            "أو /cancel للإلغاء.",
            parse_mode=ParseMode.HTML,
        )
        return ST_REMOVE_OWNER_ID

    return ConversationHandler.END


async def receive_new_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("❌ أرسل رقم ID صحيح فقط.")
        return ST_ADD_OWNER_ID

    new_owner = int(text)

    if db_is_owner(new_owner):
        await update.message.reply_text("ℹ️ هذا المستخدم أونر بالفعل.")
    else:
        db_add_owner(new_owner, added_by=user_id)
        await update.message.reply_text(
            f"✅ تم إضافة <code>{new_owner}</code> كأونر بنجاح.",
            parse_mode=ParseMode.HTML,
        )
        # إشعار الأونر الجديد
        try:
            await context.bot.send_message(
                chat_id=new_owner,
                text="🎉 تم منحك صلاحية <b>أونر</b> في البوت!\nاضغط /start للبدء.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass

    context.user_data.clear()
    await update.message.reply_text(
        "العودة:", reply_markup=build_admin_reply_keyboard(user_id)
    )
    return ConversationHandler.END


async def receive_remove_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("❌ أرسل رقم ID صحيح فقط.")
        return ST_REMOVE_OWNER_ID

    target = int(text)

    if target in OWNER_IDS:
        await update.message.reply_text(
            "⛔ لا يمكن حذف الأونرز الثابتين من الإعدادات."
        )
    elif not db_is_owner(target):
        await update.message.reply_text("ℹ️ هذا المستخدم ليس أونر أصلاً.")
    else:
        db_remove_owner(target)
        await update.message.reply_text(
            f"✅ تم حذف <code>{target}</code> من الأونرز.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data.clear()
    await update.message.reply_text(
        "العودة:", reply_markup=build_admin_reply_keyboard(user_id)
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# 📢 CHANNELS MANAGEMENT
# ─────────────────────────────────────────────────────────────────

async def show_channels_panel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    channels = db_get_channels()
    text = "📢 <b>إدارة قنوات الاشتراك الإجباري</b>\n\n"

    if channels:
        text += "القنوات المضافة حالياً:\n"
        for ch in channels:
            text += f"• @{ch['channel_username']} — {ch['channel_title'] or 'بلا عنوان'}\n"
    else:
        text += "<i>لا توجد قنوات مضافة.</i>\n"

    buttons = []
    for ch in channels:
        uname = ch["channel_username"]
        buttons.append([
            InlineKeyboardButton(
                f"🗑️ حذف @{uname}",
                callback_data=f"a_rch_{uname}",
            )
        ])
    buttons.append([InlineKeyboardButton("➕ إضافة قناة", callback_data="a_ach_0")])

    msg_obj = update.message or (update.callback_query.message if update.callback_query else None)
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except BadRequest:
            await msg_obj.reply_text(text, parse_mode=ParseMode.HTML,
                                     reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await msg_obj.reply_text(text, parse_mode=ParseMode.HTML,
                                 reply_markup=InlineKeyboardMarkup(buttons))


# We handle "a_ach_0" inside handle_admin_callback:
# Let's patch it in there (already handled below in the extended match)

# ─────────────────────────────────────────────────────────────────
# 📊 STATISTICS
# ─────────────────────────────────────────────────────────────────

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = db_count_users()
    admins = db_count_admins()
    conn = get_db()
    cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conts = conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
    conn.close()

    text = (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 إجمالي المستخدمين: <b>{users:,}</b>\n"
        f"👑 عدد المشرفين: <b>{admins:,}</b>\n"
        f"📂 الفئات: <b>{cats:,}</b>\n"
        f"📌 المحتويات: <b>{conts:,}</b>\n"
        f"📢 قنوات الاشتراك: <b>{channels:,}</b>\n\n"
        f"⏰ آخر تحديث: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────────────────────────
# 💾 DATABASE BACKUP
# ─────────────────────────────────────────────────────────────────

async def send_db_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ جاري إعداد النسخة الاحتياطية...")
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"lms_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="💾 <b>نسخة احتياطية من قاعدة البيانات</b>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")


# ─────────────────────────────────────────────────────────────────
# 👤 ADD ADMIN — ConversationHandler
# ─────────────────────────────────────────────────────────────────

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👤 <b>إضافة مشرف جديد</b>\n\n"
        "أرسل <b>معرف المستخدم (User ID)</b> الرقمي للمشرف الجديد:\n\n"
        "أو أرسل /cancel للإلغاء.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ST_ADD_ADMIN_ID


async def receive_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text(
            "❌ يجب أن يكون المعرف رقماً صحيحاً. أعد المحاولة أو أرسل /cancel."
        )
        return ST_ADD_ADMIN_ID

    new_admin_id = int(text)
    if new_admin_id in OWNER_IDS:
        await update.message.reply_text("ℹ️ هذا المستخدم هو المالك أصلاً.")
    elif db_is_admin(new_admin_id):
        await update.message.reply_text("ℹ️ هذا المستخدم مشرف بالفعل.")
    else:
        db_add_admin(new_admin_id)
        await update.message.reply_text(
            f"✅ تم إضافة المستخدم <code>{new_admin_id}</code> كمشرف بنجاح.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data.clear()
    await update.message.reply_text(
        "العودة إلى لوحة التحكم:", reply_markup=build_admin_reply_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# 📣 BROADCAST — ConversationHandler
# ─────────────────────────────────────────────────────────────────

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    count = db_count_users()
    await update.message.reply_text(
        f"📣 <b>إرسال رسالة جماعية</b>\n\n"
        f"سيتم إرسال الرسالة إلى <b>{count:,}</b> مستخدم.\n\n"
        f"أرسل الرسالة (نص / صورة / فيديو / ملف) أو /cancel للإلغاء:",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ST_BROADCAST_MSG


async def receive_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    context.user_data["broadcast_from_chat"] = msg.chat_id
    context.user_data["broadcast_msg_id"] = msg.message_id

    preview_text = ""
    if msg.text:
        preview_text = f"📝 نص: {msg.text[:100]}..."
    elif msg.photo:
        preview_text = f"🖼️ صورة مع تعليق: {msg.caption or '(بلا تعليق)'}"
    elif msg.video:
        preview_text = f"🎥 فيديو مع تعليق: {msg.caption or '(بلا تعليق)'}"
    elif msg.document:
        preview_text = f"📄 ملف: {msg.document.file_name or 'ملف'}"
    else:
        preview_text = "رسالة"

    count = db_count_users()
    await msg.reply_text(
        f"<b>معاينة الرسالة:</b>\n{preview_text}\n\n"
        f"هل تريد إرسالها إلى <b>{count:,}</b> مستخدم؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ إرسال", callback_data="bc_confirm"),
                InlineKeyboardButton("❌ إلغاء", callback_data="bc_cancel"),
            ]
        ]),
    )
    return ST_BROADCAST_CONFIRM


async def broadcast_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "bc_cancel":
        context.user_data.clear()
        await query.edit_message_text("❌ تم إلغاء الإرسال الجماعي.")
        await query.message.reply_text(
            "العودة:", reply_markup=build_admin_reply_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END

    # Execute broadcast
    from_chat = context.user_data.get("broadcast_from_chat")
    msg_id = context.user_data.get("broadcast_msg_id")
    users = db_get_all_users()

    await query.edit_message_text(
        f"⏳ جاري الإرسال إلى {len(users):,} مستخدم..."
    )

    success = 0
    failed = 0

    for uid in users:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=from_chat,
                message_id=msg_id,
            )
            success += 1
        except Forbidden:
            failed += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)

    context.user_data.clear()
    await query.message.reply_text(
        f"✅ <b>اكتمل الإرسال الجماعي</b>\n\n"
        f"✔️ نجح: <b>{success:,}</b>\n"
        f"✖️ فشل: <b>{failed:,}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_admin_reply_keyboard(update.effective_user.id),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# ➕ ADD CHANNEL — ConversationHandler
# ─────────────────────────────────────────────────────────────────

async def start_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Triggered by inline button a_ach_0 via callback_router."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📢 <b>إضافة قناة اشتراك إجباري</b>\n\n"
        "أرسل <b>يوزر نيم</b> القناة (بدون @) أو /cancel للإلغاء:\n\n"
        "<i>مثال: mychannel</i>",
        parse_mode=ParseMode.HTML,
    )
    return ST_ADD_CHANNEL


async def receive_channel_username(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    text = update.message.text.strip().lstrip("@")
    if not text:
        await update.message.reply_text("⚠️ أرسل اليوزر نيم أو /cancel.")
        return ST_ADD_CHANNEL

    # Try to get channel info
    try:
        chat = await context.bot.get_chat(f"@{text}")
        title = chat.title or text
        invite_link = chat.invite_link or f"https://t.me/{text}"
    except TelegramError:
        title = text
        invite_link = f"https://t.me/{text}"

    db_add_channel(text, title, invite_link)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ تم إضافة القناة <b>@{text}</b> ({title}) بنجاح.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_admin_reply_keyboard(update.effective_user.id),
    )
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ تم إلغاء العملية.",
        reply_markup=build_admin_reply_keyboard(update.effective_user.id),
    )
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────
# 🚨 GLOBAL ERROR HANDLER
# ─────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update:", exc_info=context.error)
    tb = traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    )
    tb_str = "".join(tb)[-2000:]

    for oid in OWNER_IDS:
        try:
            await context.bot.send_message(
                chat_id=oid,
                text=(
                    "⚠️ <b>خطأ في البوت</b>\n\n"
                    f"<pre>{tb_str}</pre>"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ حدث خطأ غير متوقع. تم إشعار المشرف."
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────
# We need to handle `a_ach_0` in the callback router above.
# Patch handle_admin_callback to include it:
# ─────────────────────────────────────────────────────────────────

# The start_add_channel function returns a ConversationHandler state.
# We'll register it as part of the channel ConversationHandler.
# But it starts from an inline button. We handle it via a special
# ConversationHandler with entry_point = CallbackQueryHandler("a_ach_0").


# ─────────────────────────────────────────────────────────────────
# 🤖 APPLICATION SETUP & MAIN
# ─────────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # ── ConversationHandler: Add Admin ────────────────────────
    add_admin_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^👤 إضافة مشرف$") & filters.TEXT,
                start_add_admin,
            )
        ],
        states={
            ST_ADD_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_id)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )

    # ── ConversationHandler: Broadcast ───────────────────────
    broadcast_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^📣 إرسال رسالة جماعية$") & filters.TEXT,
                start_broadcast,
            )
        ],
        states={
            ST_BROADCAST_MSG: [
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL)
                    & ~filters.COMMAND,
                    receive_broadcast_msg,
                )
            ],
            ST_BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm_callback, pattern="^bc_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )

    # ── ConversationHandler: Add Channel ─────────────────────
    add_channel_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_channel, pattern="^a_ach_0$"),
        ],
        states={
            ST_ADD_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_username)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )

    owners_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(owner_panel_callback, pattern="^owner_(add|remove)$")
        ],
        states={
            ST_ADD_OWNER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_owner_id)
            ],
            ST_REMOVE_OWNER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_remove_owner_id)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        allow_reentry=True,
    )
    app.add_handler(owners_conv)

    # ── Register handlers ─────────────────────────────────────
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_admin_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(add_channel_conv)

    # Admin menu router (ReplyKeyboard)
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.Regex(
                "^(📂 إدارة المحتوى|📢 إدارة القنوات|📊 إحصائيات"
                "|💾 نسخ احتياطي|👁️ وضع الطالب|👑 إدارة الأونرز"
                "|📣 إرسال رسالة جماعية|👤 إضافة مشرف"
                "|🔙 العودة إلى لوحة التحكم|🚪 خروج من لوحة التحكم)$"
            ),
            admin_menu_router,
        )
    )

    # Callback query router
    app.add_handler(CallbackQueryHandler(callback_router))

    # Awaiting input handler (inline-driven multi-step)
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL)
            & ~filters.COMMAND,
            handle_awaiting_input,
        )
    )

    # Error handler
    app.add_error_handler(error_handler)

    return app


def main() -> None:
    init_db()
    logger.info("🚀 Starting LMS Bot...")
    app = build_application()
    logger.info("✅ Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
