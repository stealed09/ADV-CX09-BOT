#!/usr/bin/env python3
"""
Advanced Livegram-Style Telegram Support Bot
Fully dynamic configuration, button-based admin panel,
auto-delete system, log channel, force-join, and more.
"""

import os
import time
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ForceReply, Message
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.error import TelegramError, BadRequest
from telegram.constants import ParseMode

import storage

# ─────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
_ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not BOT_TOKEN or not _ADMIN_ID_RAW:
    raise ValueError("BOT_TOKEN and ADMIN_ID must be set in environment variables.")

try:
    ADMIN_ID = int(_ADMIN_ID_RAW)
except ValueError:
    raise ValueError("ADMIN_ID must be a valid integer (Telegram user id).")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# State tracking for conversations
ADMIN_STATES = {}
# {admin_id: {"action": "...", "target_user_id": int, ...}}

SPAM_TRACKER = {}
# {user_id: last_message_timestamp}

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def user_mention(user: dict, uid: str) -> str:
    name = user.get("name", "Unknown")
    username = user.get("username", "")
    if username:
        return f"{name} (@{username})"
    return f"{name}"

def parse_time_string(text: str) -> int:
    """Parse '30s', '5m', '2h' → seconds"""
    text = text.strip().lower()
    try:
        if text.endswith("s"):
            return int(text[:-1])
        elif text.endswith("m"):
            return int(text[:-1]) * 60
        elif text.endswith("h"):
            return int(text[:-1]) * 3600
        else:
            return int(text)
    except ValueError:
        return 0

def format_seconds(seconds: int) -> str:
    if seconds == 0:
        return "Off"
    elif seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    else:
        return f"{seconds // 3600}h"

async def safe_send(bot, chat_id, text, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    except TelegramError as e:
        logger.error(f"safe_send error to {chat_id}: {e}")
        return None

async def safe_edit(bot, chat_id, msg_id, text, **kwargs):
    try:
        return await bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id, text=text, **kwargs
        )
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.error(f"safe_edit error: {e}")
    except TelegramError as e:
        logger.error(f"safe_edit error: {e}")
    return None

async def safe_delete(bot, chat_id, msg_id):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        return True
    except TelegramError:
        return False

async def check_membership(bot, user_id: int, channel: str) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        return member.status not in ("left", "kicked", "banned")
    except TelegramError:
        return True  # If we can't check, allow

# ─────────────────────────────────────────────
# KEYBOARDS
# ─────────────────────────────────────────────

def admin_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")
        ],
        [
            InlineKeyboardButton("👥 Users", callback_data="admin_users"),
            InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")
        ]
    ])

def user_main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📩 Send Message", callback_data="user_send"),
            InlineKeyboardButton("ℹ️ Info", callback_data="user_info")
        ]
    ])

def message_action_keyboard(user_id: int, msg_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{user_id}_{msg_id}"),
            InlineKeyboardButton("🚫 Block", callback_data=f"block_{user_id}")
        ],
        [
            InlineKeyboardButton("🗑 Delete Chat", callback_data=f"deletechat_{user_id}"),
            InlineKeyboardButton("📋 View Log", callback_data=f"viewlog_{user_id}")
        ]
    ])

def settings_keyboard():
    config = storage.load_config()
    log_ch = config.get("log_channel_id")
    fj_ch = config.get("force_join_channel")
    ad_time = config.get("auto_delete_time", 0)
    d_mode = config.get("delete_mode", "off")

    log_label = f"📡 Log: {'✅ Set' if log_ch else '❌ Not Set'}"
    fj_label = f"🔒 Force Join: {'✅ Set' if fj_ch else '❌ Not Set'}"
    ad_label = f"🗑 Auto Delete: {format_seconds(ad_time)}"
    dm_label = f"🔄 Delete Mode: {d_mode.title()}"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(log_label, callback_data="set_log_channel")],
        [InlineKeyboardButton(fj_label, callback_data="set_force_join")],
        [InlineKeyboardButton(ad_label, callback_data="set_auto_delete")],
        [InlineKeyboardButton(dm_label, callback_data="set_delete_mode")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ])

def auto_delete_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ OFF", callback_data="ad_time_0")],
        [
            InlineKeyboardButton("10s", callback_data="ad_time_10"),
            InlineKeyboardButton("30s", callback_data="ad_time_30")
        ],
        [
            InlineKeyboardButton("1m", callback_data="ad_time_60"),
            InlineKeyboardButton("5m", callback_data="ad_time_300")
        ],
        [InlineKeyboardButton("✏️ Custom", callback_data="ad_time_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]
    ])

def delete_mode_keyboard():
    config = storage.load_config()
    mode = config.get("delete_mode", "off")

    def check(m): return "✅ " if mode == m else ""

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{check('full')}🗑 Full Delete", callback_data="dm_full")],
        [InlineKeyboardButton(f"{check('hide')}👁 Hide Mode", callback_data="dm_hide")],
        [InlineKeyboardButton(f"{check('admin')}👑 Admin Only", callback_data="dm_admin")],
        [InlineKeyboardButton(f"{check('off')}❌ Off", callback_data="dm_off")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]
    ])

def users_list_keyboard(page: int = 0):
    users = storage.get_all_users()
    items = list(users.items())
    per_page = 8
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]

    buttons = []
    for uid, udata in page_items:
        name = udata.get("name", "Unknown")
        blocked = "🚫" if udata.get("blocked") else "✅"
        buttons.append([
            InlineKeyboardButton(
                f"{blocked} {name} ({uid})",
                callback_data=f"user_detail_{uid}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)

def user_detail_keyboard(uid: str):
    user = storage.get_user(int(uid))
    blocked = user.get("blocked", False) if user else False
    ad_time = user.get("auto_delete_time") if user else None
    ad_label = f"⏱ User AutoDel: {format_seconds(ad_time or 0)}"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{uid}_0"),
            InlineKeyboardButton(
                "✅ Unblock" if blocked else "🚫 Block",
                callback_data=f"{'unblock' if blocked else 'block'}_{uid}"
            )
        ],
        [InlineKeyboardButton(ad_label, callback_data=f"user_ad_{uid}")],
        [InlineKeyboardButton("🗑 Clear Messages", callback_data=f"deletechat_{uid}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_users")]
    ])

# ─────────────────────────────────────────────
# LOG CHANNEL SYSTEM
# ─────────────────────────────────────────────

def build_log_text(user_data: dict, uid: str, messages: list) -> str:
    name = user_data.get("name", "Unknown")
    username = user_data.get("username", "")
    mention = f"@{username}" if username else "No username"
    blocked = "🚫 Blocked" if user_data.get("blocked") else "✅ Active"

    header = (
        f"👤 USER: {name} ({mention})\n"
        f"🆔 ID: {uid}\n"
        f"📊 Status: {blocked}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    lines = []
    for m in messages[-20:]:  # last 20 messages
        role = "📩 User" if m["role"] == "user" else "🤖 Admin"
        deleted = " ❌" if m.get("deleted") else ""
        ts = datetime.fromtimestamp(m.get("ts", 0)).strftime("%H:%M %d/%m")
        content = m.get("text", f"[{m.get('type', 'media')}]")
        if len(content) > 100:
            content = content[:97] + "..."
        lines.append(f"{role} [{ts}]{deleted}: {content}")

    body = "\n".join(lines) if lines else "(No messages yet)"

    # Split if too long
    full = header + body
    if len(full) > 4000:
        full = header + body[-(4000 - len(header)):]

    return full

async def update_log_channel(bot, uid: str, user_data: dict, app=None):
    log_channel = storage.get_config_value("log_channel_id")
    if not log_channel:
        return

    text = build_log_text(user_data, uid, user_data.get("messages", []))

    log_msg_id = user_data.get("log_msg_id")
    if log_msg_id:
        edited = await safe_edit(bot, log_channel, log_msg_id, text)
        if edited:
            return
    # Create new log message
    msg = await safe_send(bot, log_channel, text)
    if msg:
        users = storage.load_users()
        if uid in users:
            users[uid]["log_msg_id"] = msg.message_id
            storage.save_users(users)

# ─────────────────────────────────────────────
# AUTO-DELETE BACKGROUND TASK
# ─────────────────────────────────────────────

async def auto_delete_loop(app):
    """Background task: checks and deletes messages per schedule"""
    while True:
        try:
            await asyncio.sleep(8)
            tracked = storage.load_tracked()
            if not tracked:
                continue

            now = time.time()
            config = storage.load_config()
            global_mode = config.get("delete_mode", "off")

            updated = []
            changed = False

            for entry in tracked:
                if entry.get("deleted"):
                    updated.append(entry)
                    continue

                elapsed = now - entry["timestamp"]
                if elapsed < entry["delete_after"]:
                    updated.append(entry)
                    continue

                # Time to delete
                mode = global_mode
                uid = str(entry.get("user_id", ""))
                if uid:
                    user = storage.get_user(int(uid))
                    if user and user.get("delete_mode"):
                        mode = user["delete_mode"]

                chat_id = entry["chat_id"]
                msg_id = entry["msg_id"]

                if mode == "full":
                    await safe_delete(app.bot, chat_id, msg_id)
                elif mode == "hide":
                    try:
                        await app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg_id,
                            text="⚠️ Message auto-deleted"
                        )
                    except TelegramError:
                        await safe_delete(app.bot, chat_id, msg_id)
                elif mode == "admin":
                    if chat_id == ADMIN_ID:
                        await safe_delete(app.bot, chat_id, msg_id)

                entry["deleted"] = True
                changed = True

                # Update log
                if uid:
                    user_data = storage.get_user(int(uid))
                    if user_data:
                        msgs = user_data.get("messages", [])
                        for m in msgs:
                            if m.get("msg_id") == msg_id:
                                m["deleted"] = True
                        users = storage.load_users()
                        if uid in users:
                            users[uid]["messages"] = msgs
                            storage.save_users(users)
                        await update_log_channel(app.bot, uid, user_data)

                updated.append(entry)

            if changed:
                storage.save_tracked(updated)

        except Exception as e:
            logger.error(f"auto_delete_loop error: {e}")

def schedule_auto_delete(msg_id: int, chat_id: int,
                         user_id: int, delete_after: int):
    if delete_after and delete_after > 0:
        storage.add_tracked_message(
            msg_id=msg_id,
            chat_id=chat_id,
            user_id=user_id,
            timestamp=time.time(),
            delete_after=delete_after
        )

# ─────────────────────────────────────────────
# MEDIA FORWARDING
# ─────────────────────────────────────────────

async def forward_media_to_admin(bot, message: Message, user_id: int):
    """Forward user media to admin chat with action buttons"""
    uid = str(user_id)
    caption = f"📩 Message from user {uid}\n"
    if message.caption:
        caption += f"\n{message.caption}"

    keyboard = message_action_keyboard(user_id, message.message_id)

    sent = None
    try:
        if message.text:
            sent = await bot.send_message(
                ADMIN_ID,
                f"📩 *New message from* `{uid}`:\n\n{message.text}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        elif message.photo:
            sent = await bot.send_photo(
                ADMIN_ID,
                message.photo[-1].file_id,
                caption=caption,
                reply_markup=keyboard
            )
        elif message.video:
            sent = await bot.send_video(
                ADMIN_ID,
                message.video.file_id,
                caption=caption,
                reply_markup=keyboard
            )
        elif message.voice:
            sent = await bot.send_voice(
                ADMIN_ID,
                message.voice.file_id,
                caption=caption,
                reply_markup=keyboard
            )
        elif message.document:
            sent = await bot.send_document(
                ADMIN_ID,
                message.document.file_id,
                caption=caption,
                reply_markup=keyboard
            )
        elif message.sticker:
            sticker_msg = await bot.send_sticker(ADMIN_ID, message.sticker.file_id)
            sent = await bot.send_message(
                ADMIN_ID,
                f"📩 Sticker from `{uid}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
            # Fallback so caller can track at least one id
            if not sent:
                sent = sticker_msg
        elif message.audio:
            sent = await bot.send_audio(
                ADMIN_ID,
                message.audio.file_id,
                caption=caption,
                reply_markup=keyboard
            )
    except TelegramError as e:
        logger.error(f"Error forwarding media: {e}")

    return sent

async def forward_reply_to_user(bot, message: Message, target_user_id: int):
    """Forward admin reply to user"""
    sent = None
    try:
        if message.text:
            sent = await bot.send_message(
                target_user_id,
                f"🤖 *Support Reply:*\n\n{message.text}",
                parse_mode=ParseMode.MARKDOWN
            )
        elif message.photo:
            sent = await bot.send_photo(
                target_user_id,
                message.photo[-1].file_id,
                caption=message.caption or "🤖 Support Reply"
            )
        elif message.video:
            sent = await bot.send_video(
                target_user_id,
                message.video.file_id,
                caption=message.caption or "🤖 Support Reply"
            )
        elif message.voice:
            sent = await bot.send_voice(
                target_user_id,
                message.voice.file_id
            )
        elif message.document:
            sent = await bot.send_document(
                target_user_id,
                message.document.file_id,
                caption=message.caption or "🤖 Support Reply"
            )
        elif message.sticker:
            sent = await bot.send_sticker(target_user_id, message.sticker.file_id)
    except TelegramError as e:
        logger.error(f"Error forwarding reply: {e}")

    return sent

# ─────────────────────────────────────────────
# /START COMMAND
# ─────────────────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if is_admin(uid):
        await update.message.reply_text(
            "👑 *Welcome, Admin!*\n\nChoose an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_main_keyboard()
        )
        return

    # Register user
    user_data = storage.get_or_create_user(
        uid, user.full_name, user.username or ""
    )

    # Force join check
    force_ch = storage.get_config_value("force_join_channel")
    if force_ch:
        is_member = await check_membership(context.bot, uid, force_ch)
        if not is_member:
            await update.message.reply_text(
                f"🔒 *You must join our channel first!*\n\n"
                f"Join: {force_ch}\n\n"
                f"Then press /start again.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    if user_data.get("blocked"):
        await update.message.reply_text(
            "🚫 You have been blocked from using this bot."
        )
        return

    await update.message.reply_text(
        f"👋 *Hello, {user.first_name}!*\n\n"
        f"Welcome to our support bot.\n"
        f"Use the buttons below to get started.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=user_main_keyboard()
    )

# ─────────────────────────────────────────────
# MESSAGE HANDLER (Users → Admin)
# ─────────────────────────────────────────────

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    message = update.message

    if not message:
        return

    # Admin message handling (replies etc.)
    if is_admin(uid):
        await handle_admin_message(update, context)
        return

    # Get/create user
    user_data = storage.get_or_create_user(
        uid, user.full_name, user.username or ""
    )

    # Blocked check
    if user_data.get("blocked"):
        await message.reply_text("🚫 You are blocked from using this bot.")
        return

    # Force join
    force_ch = storage.get_config_value("force_join_channel")
    if force_ch:
        is_member = await check_membership(context.bot, uid, force_ch)
        if not is_member:
            await message.reply_text(
                f"🔒 Please join {force_ch} first, then send your message."
            )
            return

    # Anti-spam: 1 message per 5 sec
    now = time.time()
    last = SPAM_TRACKER.get(uid, 0)
    if now - last < 5:
        await message.reply_text(
            "⏳ Please wait a moment before sending another message."
        )
        return
    SPAM_TRACKER[uid] = now

    # Get message content for logging
    msg_text = message.text or message.caption or ""
    msg_type = "text"
    if message.photo:
        msg_type = "photo"
    elif message.video:
        msg_type = "video"
    elif message.voice:
        msg_type = "voice"
    elif message.document:
        msg_type = "document"
    elif message.sticker:
        msg_type = "sticker"
    elif message.audio:
        msg_type = "audio"

    # Forward to admin
    sent = await forward_media_to_admin(context.bot, message, uid)

    # Store message in user history
    users = storage.load_users()
    uid_str = str(uid)
    if uid_str in users:
        msg_record = {
            "role": "user",
            "text": msg_text or f"[{msg_type}]",
            "type": msg_type,
            "msg_id": message.message_id,
            "admin_msg_id": sent.message_id if sent else None,
            "ts": now,
            "deleted": False
        }
        users[uid_str]["messages"].append(msg_record)
        users[uid_str]["last_msg_time"] = now
        storage.save_users(users)

    # Auto-delete scheduling
    user_ad = user_data.get("auto_delete_time")
    global_ad = storage.get_config_value("auto_delete_time") or 0
    delete_after = user_ad if user_ad else global_ad

    if delete_after and delete_after > 0:
        schedule_auto_delete(message.message_id, uid, uid, delete_after)
        if sent:
            schedule_auto_delete(sent.message_id, ADMIN_ID, uid, delete_after)

    # Update log channel
    user_data = storage.get_user(uid)
    if user_data:
        await update_log_channel(context.bot, uid_str, user_data)

    # Confirm to user
    await message.reply_text("✅ Your message has been sent to support!")

async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin messages (replies, settings input)"""
    message = update.message
    state = ADMIN_STATES.get(ADMIN_ID, {})
    action = state.get("action")

    if not action:
        return

    # ── REPLY TO USER ──
    if action == "reply":
        target_uid = state.get("target_user_id")
        if not target_uid:
            await message.reply_text("❌ No target user selected.")
            ADMIN_STATES.pop(ADMIN_ID, None)
            return

        sent = await forward_reply_to_user(context.bot, message, target_uid)

        if sent:
            await message.reply_text(
                f"✅ Reply sent to user `{target_uid}`",
                parse_mode=ParseMode.MARKDOWN
            )
            # Store in user history
            uid_str = str(target_uid)
            users = storage.load_users()
            if uid_str in users:
                msg_text = message.text or message.caption or "[media]"
                users[uid_str]["messages"].append({
                    "role": "admin",
                    "text": msg_text,
                    "type": "text",
                    "msg_id": sent.message_id,
                    "ts": time.time(),
                    "deleted": False
                })
                storage.save_users(users)

            # Update log
            user_data = storage.get_user(target_uid)
            if user_data:
                await update_log_channel(context.bot, uid_str, user_data)

            # Auto-delete
            global_ad = storage.get_config_value("auto_delete_time") or 0
            if global_ad > 0 and sent:
                schedule_auto_delete(sent.message_id, target_uid,
                                     target_uid, global_ad)
        else:
            await message.reply_text("❌ Failed to send reply. User may have blocked the bot.")

        ADMIN_STATES.pop(ADMIN_ID, None)

    # ── SET LOG CHANNEL (via forward) ──
    elif action == "set_log_channel":
        channel_id = None
        if message.forward_from_chat:
            channel_id = message.forward_from_chat.id
        elif message.text and message.text.startswith("-100"):
            try:
                channel_id = int(message.text.strip())
            except ValueError:
                pass
        elif message.text and message.text.startswith("@"):
            channel_id = message.text.strip()

        if channel_id:
            storage.set_config_value("log_channel_id", channel_id)
            await message.reply_text(
                f"✅ Log channel set to: `{channel_id}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=settings_keyboard()
            )
        else:
            await message.reply_text(
                "❌ Could not extract channel ID.\n"
                "Forward a message from the channel or send the channel ID."
            )
        ADMIN_STATES.pop(ADMIN_ID, None)

    # ── SET FORCE JOIN ──
    elif action == "set_force_join":
        channel_id = None
        if message.forward_from_chat:
            channel_id = message.forward_from_chat.username or message.forward_from_chat.id
        elif message.text:
            txt = message.text.strip()
            if txt.startswith("@") or txt.startswith("-100"):
                channel_id = txt

        if channel_id:
            storage.set_config_value("force_join_channel", channel_id)
            await message.reply_text(
                f"✅ Force join channel set to: `{channel_id}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=settings_keyboard()
            )
        else:
            await message.reply_text(
                "❌ Send channel username (@channel) or forward a message."
            )
        ADMIN_STATES.pop(ADMIN_ID, None)

    # ── CUSTOM AUTO DELETE TIME ──
    elif action == "custom_ad_time":
        seconds = parse_time_string(message.text or "")
        if seconds > 0:
            # Check if it's for a user or global
            target_uid = state.get("target_user_id")
            if target_uid:
                users = storage.load_users()
                uid_str = str(target_uid)
                if uid_str in users:
                    users[uid_str]["auto_delete_time"] = seconds
                    storage.save_users(users)
                await message.reply_text(
                    f"✅ User `{target_uid}` auto-delete set to "
                    f"`{format_seconds(seconds)}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                storage.set_config_value("auto_delete_time", seconds)
                await message.reply_text(
                    f"✅ Global auto-delete set to `{format_seconds(seconds)}`",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=settings_keyboard()
                )
        else:
            await message.reply_text(
                "❌ Invalid format. Use: `30s`, `5m`, `2h`",
                parse_mode=ParseMode.MARKDOWN
            )
        ADMIN_STATES.pop(ADMIN_ID, None)

    # ── BROADCAST MESSAGE ──
    elif action == "broadcast":
        users = storage.get_all_users()
        total = len(users)
        success = 0
        failed = 0

        await message.reply_text(f"📢 Broadcasting to {total} users...")

        for uid_str, udata in users.items():
            if udata.get("blocked"):
                failed += 1
                continue
            try:
                uid_int = int(uid_str)
                if uid_int == ADMIN_ID:
                    continue

                sent_ok = False
                if message.text:
                    await context.bot.send_message(uid_int, message.text)
                    sent_ok = True
                elif message.photo:
                    await context.bot.send_photo(
                        uid_int, message.photo[-1].file_id,
                        caption=message.caption or ""
                    )
                    sent_ok = True
                elif message.video:
                    await context.bot.send_video(
                        uid_int, message.video.file_id,
                        caption=message.caption or ""
                    )
                    sent_ok = True
                elif message.document:
                    await context.bot.send_document(
                        uid_int, message.document.file_id,
                        caption=message.caption or ""
                    )
                    sent_ok = True

                if sent_ok:
                    success += 1
                else:
                    failed += 1
                await asyncio.sleep(0.05)  # Rate limit
            except TelegramError:
                failed += 1

        await message.reply_text(
            f"📢 *Broadcast Complete!*\n\n"
            f"✅ Sent: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_main_keyboard()
        )
        ADMIN_STATES.pop(ADMIN_ID, None)


# ─────────────────────────────────────────────
# CALLBACK QUERY HANDLER
# ─────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # ── USER CALLBACKS ──
    if data == "user_send" and not is_admin(user_id):
        await query.message.reply_text(
            "📩 *Send your message now.*\n"
            "Text, photo, video, voice, document — all supported!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "user_info" and not is_admin(user_id):
        user_data = storage.get_user(user_id)
        if user_data:
            msg_count = len(user_data.get("messages", []))
            ad = user_data.get("auto_delete_time")
            await query.message.reply_text(
                f"ℹ️ *Your Info*\n\n"
                f"👤 Name: {user_data.get('name')}\n"
                f"🆔 ID: `{user_id}`\n"
                f"📩 Messages sent: {msg_count}\n"
                f"⏱ Auto-delete: {format_seconds(ad or 0)}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=user_main_keyboard()
            )
        return

    # ── ADMIN ONLY BELOW ──
    if not is_admin(user_id):
        await query.answer("❌ Admin only!", show_alert=True)
        return

    # ── MAIN ADMIN MENU ──
    if data == "admin_back":
        await query.edit_message_text(
            "👑 *Admin Panel*\n\nChoose an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_main_keyboard()
        )

    elif data == "admin_stats":
        total = storage.count_users()
        blocked = storage.count_blocked()
        config = storage.load_config()
        ad = config.get("auto_delete_time", 0)
        mode = config.get("delete_mode", "off")
        log_ch = config.get("log_channel_id", "Not set")
        fj = config.get("force_join_channel", "Not set")

        text = (
            f"📊 *Bot Statistics*\n\n"
            f"👥 Total Users: `{total}`\n"
            f"🚫 Blocked: `{blocked}`\n"
            f"✅ Active: `{total - blocked}`\n\n"
            f"⚙️ *Config*\n"
            f"📡 Log Channel: `{log_ch}`\n"
            f"🔒 Force Join: `{fj}`\n"
            f"🗑 Auto Delete: `{format_seconds(ad)}`\n"
            f"🔄 Delete Mode: `{mode}`"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
            ])
        )

    elif data == "admin_broadcast":
        ADMIN_STATES[ADMIN_ID] = {"action": "broadcast"}
        await query.edit_message_text(
            "📢 *Broadcast Mode*\n\n"
            "Send the message you want to broadcast to ALL users.\n"
            "Supports: text, photo, video, document",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]
            ])
        )

    elif data == "admin_users":
        await query.edit_message_text(
            "👥 *User List*\n\nClick a user to manage:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=users_list_keyboard(0)
        )

    elif data == "admin_settings":
        await query.edit_message_text(
            "⚙️ *Settings*\n\nConfigure your bot:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=settings_keyboard()
        )

    # ── SETTINGS ──
    elif data == "set_log_channel":
        ADMIN_STATES[ADMIN_ID] = {"action": "set_log_channel"}
        await query.edit_message_text(
            "📡 *Set Log Channel*\n\n"
            "Forward any message from your log channel,\n"
            "OR send the channel ID (e.g. `-1001234567890`)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_settings")]
            ])
        )

    elif data == "set_force_join":
        ADMIN_STATES[ADMIN_ID] = {"action": "set_force_join"}
        await query.edit_message_text(
            "🔒 *Set Force Join Channel*\n\n"
            "Forward a message from your channel,\n"
            "OR send the channel username (@channel)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_settings")],
                [InlineKeyboardButton("🗑 Remove Force Join",
                                      callback_data="remove_force_join")]
            ])
        )

    elif data == "remove_force_join":
        storage.set_config_value("force_join_channel", None)
        await query.edit_message_text(
            "✅ Force join disabled.",
            reply_markup=settings_keyboard()
        )

    elif data == "set_auto_delete":
        await query.edit_message_text(
            "🗑 *Auto Delete Settings*\n\nSelect delete time:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=auto_delete_keyboard()
        )

    elif data == "set_delete_mode":
        await query.edit_message_text(
            "🔄 *Delete Mode*\n\nSelect how messages are deleted:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=delete_mode_keyboard()
        )

    # ── AUTO DELETE TIME BUTTONS ──
    elif data.startswith("ad_time_"):
        val = data.replace("ad_time_", "")
        if val == "custom":
            ADMIN_STATES[ADMIN_ID] = {"action": "custom_ad_time"}
            await query.edit_message_text(
                "✏️ *Custom Auto Delete Time*\n\n"
                "Send the time in format:\n"
                "`30s` = 30 seconds\n"
                "`5m` = 5 minutes\n"
                "`2h` = 2 hours",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="set_auto_delete")]
                ])
            )
        else:
            seconds = int(val)
            storage.set_config_value("auto_delete_time", seconds)
            await query.edit_message_text(
                f"✅ Auto-delete set to `{format_seconds(seconds)}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=settings_keyboard()
            )

    # ── DELETE MODE BUTTONS ──
    elif data.startswith("dm_"):
        mode = data.replace("dm_", "")
        storage.set_config_value("delete_mode", mode)
        await query.edit_message_text(
            f"✅ Delete mode set to `{mode}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=settings_keyboard()
        )

    # ── USER LIST PAGING ──
    elif data.startswith("users_page_"):
        page = int(data.replace("users_page_", ""))
        await query.edit_message_text(
            "👥 *User List*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=users_list_keyboard(page)
        )

    # ── USER DETAIL ──
    elif data.startswith("user_detail_"):
        uid_str = data.replace("user_detail_", "")
        user_data = storage.get_user(int(uid_str))
        if not user_data:
            await query.answer("User not found", show_alert=True)
            return
        name = user_data.get("name", "Unknown")
        username = user_data.get("username", "")
        mention = f"@{username}" if username else "No username"
        blocked = "🚫 Yes" if user_data.get("blocked") else "✅ No"
        msg_count = len(user_data.get("messages", []))
        ad = user_data.get("auto_delete_time")

        text = (
            f"👤 *User Details*\n\n"
            f"Name: {name}\n"
            f"Username: {mention}\n"
            f"ID: `{uid_str}`\n"
            f"Blocked: {blocked}\n"
            f"Messages: {msg_count}\n"
            f"Auto-delete: {format_seconds(ad or 0)}"
        )
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=user_detail_keyboard(uid_str)
        )

    # ── REPLY BUTTON ──
    elif data.startswith("reply_"):
        parts = data.split("_")
        target_uid = int(parts[1])
        ADMIN_STATES[ADMIN_ID] = {
            "action": "reply",
            "target_user_id": target_uid
        }
        user_data = storage.get_user(target_uid)
        name = user_data.get("name", "Unknown") if user_data else "Unknown"
        await query.message.reply_text(
            f"↩️ *Reply to {name}* (`{target_uid}`)\n\n"
            f"Send your reply message now:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]
            ])
        )

    # ── BLOCK/UNBLOCK ──
    elif data.startswith("block_"):
        uid_str = data.replace("block_", "")
        storage.block_user(int(uid_str))
        await query.answer(f"🚫 User {uid_str} blocked!", show_alert=True)
        user_data = storage.get_user(int(uid_str))
        if user_data:
            await update_log_channel(context.bot, uid_str, user_data)
        try:
            await query.edit_message_reply_markup(
                reply_markup=user_detail_keyboard(uid_str)
            )
        except TelegramError:
            pass

    elif data.startswith("unblock_"):
        uid_str = data.replace("unblock_", "")
        storage.unblock_user(int(uid_str))
        await query.answer(f"✅ User {uid_str} unblocked!", show_alert=True)
        user_data = storage.get_user(int(uid_str))
        if user_data:
            await update_log_channel(context.bot, uid_str, user_data)
        try:
            await query.edit_message_reply_markup(
                reply_markup=user_detail_keyboard(uid_str)
            )
        except TelegramError:
            pass

    # ── DELETE CHAT ──
    elif data.startswith("deletechat_"):
        uid_str = data.replace("deletechat_", "")
        users = storage.load_users()
        if uid_str in users:
            users[uid_str]["messages"] = []
            users[uid_str]["log_msg_id"] = None
            storage.save_users(users)

        # Clear log
        log_ch = storage.get_config_value("log_channel_id")
        if log_ch:
            user_data = storage.get_user(int(uid_str))
            if user_data:
                await update_log_channel(context.bot, uid_str, user_data)

        await query.answer("🗑 Chat history cleared!", show_alert=True)

    # ── VIEW LOG ──
    elif data.startswith("viewlog_"):
        uid_str = data.replace("viewlog_", "")
        user_data = storage.get_user(int(uid_str))
        if not user_data:
            await query.answer("No data found", show_alert=True)
            return

        log_text = build_log_text(user_data, uid_str,
                                  user_data.get("messages", []))
        await context.bot.send_message(
            ADMIN_ID,
            f"📋 *Log for user {uid_str}*\n\n{log_text}",
            parse_mode=ParseMode.MARKDOWN
        )

    # ── USER AUTO DELETE ──
    elif data.startswith("user_ad_"):
        uid_str = data.replace("user_ad_", "")
        ADMIN_STATES[ADMIN_ID] = {
            "action": "custom_ad_time",
            "target_user_id": int(uid_str)
        }
        await query.message.reply_text(
            f"⏱ *Set Auto-Delete for user* `{uid_str}`\n\n"
            f"Send time: `30s`, `5m`, `2h`\n"
            f"Send `0` to disable for this user.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data=f"user_detail_{uid_str}")]
            ])
        )

# ─────────────────────────────────────────────
# CANCEL COMMAND
# ─────────────────────────────────────────────

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ADMIN_STATES.pop(ADMIN_ID, None)
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Action cancelled.",
            reply_markup=admin_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=user_main_keyboard()
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "👑 *Admin Commands*\n\n"
            "/start — Open admin panel\n"
            "/cancel — Cancel current action\n"
            "/stats — Quick stats\n"
            "/broadcast — Broadcast mode\n"
            "/help — This message",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_main_keyboard()
        )
    else:
        await update.message.reply_text(
            "ℹ️ *How to use this bot*\n\n"
            "Just send any message and our support team will reply shortly.\n\n"
            "Supported: text, photo, video, voice, documents",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=user_main_keyboard()
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    total = storage.count_users()
    blocked = storage.count_blocked()
    await update.message.reply_text(
        f"📊 *Quick Stats*\n\n"
        f"👥 Total Users: `{total}`\n"
        f"🚫 Blocked: `{blocked}`\n"
        f"✅ Active: `{total - blocked}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=admin_main_keyboard()
    )

# ─────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ An error occurred. Please try again."
            )
        except TelegramError:
            pass

# ─────────────────────────────────────────────
# POST INIT (Start background tasks)
# ─────────────────────────────────────────────

async def post_init(app):
    """Start background auto-delete loop after bot starts"""
    asyncio.create_task(auto_delete_loop(app))
    logger.info("✅ Auto-delete background task started")

    # Notify admin
    try:
        await app.bot.send_message(
            ADMIN_ID,
            "🤖 *Bot Started Successfully!*\n\n"
            "Use the panel below to manage your support bot.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_main_keyboard()
        )
    except TelegramError as e:
        logger.error(f"Could not notify admin: {e}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    # Initialize storage files
    storage.load_config()
    storage.load_users()

    # Build application
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))

    # Callback queries
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Messages (all types)
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_user_message
    ))

    # Error handler
    app.add_error_handler(error_handler)

    logger.info("🚀 Bot is starting...")
    app.run_polling(
        poll_interval=1,
        timeout=30,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
