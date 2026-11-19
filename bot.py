import os
import re
import time
import html
import asyncio
import logging
import urllib.request
from typing import Dict, Any, Optional
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedAudio,
    InputTextMessageContent,
)
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError, TimedOut, NetworkError, RetryAfter

from config import (
    BOT_TOKEN,
    MAX_FILE_SIZE_MB,
    OWNER_USERNAME,
    OWNER_CHANNEL_LINK,
    OWNER_GROUP_LINK,
    OWNER_INSTAGRAM_LINK,
    OWNER_SNAPCHAT_LINK,
    ENABLE_FSUB,
    REQUIRED_CHANNEL,
    logger,
)
from downloader import MediaDownloader, extract_urls, format_bytes, format_duration, sanitize_url, COOKIES_PATH
from server import start_web_server
from database import db
from fonts import to_stylish

# In-memory cache for media extraction info
MEDIA_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

downloader = MediaDownloader()


def is_owner(update: Update) -> bool:
    """Checks if the user sending the command is the bot owner."""
    user = update.effective_user
    if not user:
        return False
    user_handle = (user.username or "").lower().replace("@", "")
    return user_handle == OWNER_USERNAME.lower()


def prune_cache():
    """Prunes expired entries from the in-memory cache."""
    now = time.time()
    expired_keys = [k for k, v in MEDIA_CACHE.items() if now - v.get("timestamp", 0) > CACHE_EXPIRY_SECONDS]
    for k in expired_keys:
        MEDIA_CACHE.pop(k, None)


async def check_fsub(bot, user_id: int) -> bool:
    """Checks if user has joined the required channel (if enabled)."""
    if not ENABLE_FSUB or not REQUIRED_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(chat_id=f"@{REQUIRED_CHANNEL}", user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning(f"Could not verify chat member in @{REQUIRED_CHANNEL}: {e}")
        return True


def get_social_keyboard() -> InlineKeyboardMarkup:
    """Returns the main social & support keyboard in stylish aesthetic font."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(to_stylish("📢 ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ"), url=OWNER_CHANNEL_LINK),
            InlineKeyboardButton(to_stylish("💬 sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ"), url=OWNER_GROUP_LINK),
        ],
        [
            InlineKeyboardButton(to_stylish("👑 ᴅᴇᴠᴇʟᴏᴘᴇʀ"), url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton(to_stylish("📸 ɪɴsᴛᴀɢʀᴀᴍ"), url=OWNER_INSTAGRAM_LINK),
        ],
        [
            InlineKeyboardButton(to_stylish("👻 sɴᴀᴘᴄʜᴀᴛ"), url=OWNER_SNAPCHAT_LINK),
        ]
    ])


def get_media_sent_keyboard() -> InlineKeyboardMarkup:
    """Keyboard attached below every downloaded media in stylish aesthetic font."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(to_stylish("📢 ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ"), url=OWNER_CHANNEL_LINK),
            InlineKeyboardButton(to_stylish("💬 sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ"), url=OWNER_GROUP_LINK),
        ]
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command with stylish aesthetic font UI."""
    user = update.effective_user
    if not user:
        return

    db.add_or_update_user(user.id, user.username, user.first_name)

    if db.is_user_banned(user.id):
        await update.message.reply_text(
            f"🚫 <b>{to_stylish('ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ғʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.')}</b>",
            parse_mode=ParseMode.HTML
        )
        return

    name = html.escape(user.first_name or "Friend")
    welcome_text = (
        f"⚡ <b>{to_stylish('ᴡᴇʟᴄᴏᴍᴇ')} {name}!</b> ⚡\n\n"
        f"<b>{to_stylish('ɪ ᴀᴍ sᴛʀɪɴɢ ɢᴇɴ ᴍᴇᴅɪᴀ ʙᴏᴛ — ᴛʜᴇ ᴡᴏʀʟᴅ\'s ᴍᴏsᴛ ᴀᴅᴠᴀɴᴄᴇᴅ, ᴜʟᴛʀᴀ-ғᴀsᴛ 24/7 ᴅᴏᴡɴʟᴏᴀᴅᴇʀ!')} 🚀</b>\n\n"
        f"✨ <b>{to_stylish('sᴜᴘᴘᴏʀᴛᴇᴅ ᴘʟᴀᴛғᴏʀᴍs:')}</b>\n"
        f"• 📸 <b>{to_stylish('ɪɴsᴛᴀɢʀᴀᴍ')}</b> — {to_stylish('ʀᴇᴇʟs, ᴘᴏsᴛs, sᴛᴏʀɪᴇs, ᴀᴜᴅɪᴏ')}\n"
        f"• 🎥 <b>{to_stylish('ʏᴏᴜᴛᴜʙᴇ')}</b> — {to_stylish('ᴠɪᴅᴇᴏs, sʜᴏʀᴛs (1080ᴘ, 720ᴘ, 480ᴘ, ᴍᴘ3)')}\n"
        f"• 📌 <b>{to_stylish('ᴘɪɴᴛᴇʀᴇsᴛ')}</b> — {to_stylish('ʜᴅ ᴠɪᴅᴇᴏ ᴘɪɴs & ᴏʀɪɢɪɴᴀʟ ᴘʜᴏᴛᴏs')}\n"
        f"• 📘 <b>{to_stylish('ғᴀᴄᴇʙᴏᴏᴋ')}</b> — {to_stylish('ʀᴇᴇʟs, ᴡᴀᴛᴄʜ ᴠɪᴅᴇᴏs & sᴛᴏʀɪᴇs')}\n"
        f"• 🐦 <b>{to_stylish('ᴛᴡɪᴛᴛᴇʀ / x')}</b> — {to_stylish('ʜᴅ ᴠɪᴅᴇᴏs & ᴄʟɪᴘs')}\n"
        f"• 🤖 <b>{to_stylish('ʀᴇᴅᴅɪᴛ')}</b> — {to_stylish('ᴠɪᴅᴇᴏs ᴡɪᴛʜ ᴀᴜᴅɪᴏ ᴍᴇʀɢᴇᴅ')}\n"
        f"• 👻 <b>{to_stylish('sɴᴀᴘᴄʜᴀᴛ')}</b> — {to_stylish('sᴘᴏᴛʟɪɢʜᴛ & sᴛᴏʀɪᴇs')}\n"
        f"• 🎵 <b>{to_stylish('ᴛɪᴋᴛᴏᴋ, sᴏᴜɴᴅᴄʟᴏᴜᴅ, ᴠɪᴍᴇᴏ & 1800+ ᴍᴏʀᴇ!')}</b>\n\n"
        f"👑 <b>{to_stylish('ᴏᴡɴᴇʀ:')}</b> @{html.escape(OWNER_USERNAME)}\n"
        f"📢 <b>{to_stylish('ᴜᴘᴅᴀᴛᴇs:')}</b> <a href=\"{OWNER_CHANNEL_LINK}\">{to_stylish('ᴘʀɪᴠᴀᴛᴇ ᴛʜɪɴᴋ')}</a>\n"
        f"💬 <b>{to_stylish('sᴜᴘᴘᴏʀᴛ:')}</b> <a href=\"{OWNER_GROUP_LINK}\">{to_stylish('ᴀᴍʀɪᴛ ғᴇᴇʟ')}</a>\n\n"
        f"👉 <i>{to_stylish('ᴊᴜsᴛ ᴘᴀsᴛᴇ ᴀɴʏ ᴠɪᴅᴇᴏ ᴏʀ ᴘᴏsᴛ ʟɪɴᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ɪɴsᴛᴀɴᴛʟʏ!')}</i>"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command in stylish aesthetic font."""
    help_text = (
        f"📖 <b>{to_stylish('ʜᴏᴡ ᴛᴏ ᴜsᴇ sᴛʀɪɴɢ ɢᴇɴ ᴍᴇᴅɪᴀ ʙᴏᴛ:')}</b>\n\n"
        f"1️⃣ {to_stylish('ᴄᴏᴘʏ ᴀɴʏ ᴠɪᴅᴇᴏ, ʀᴇᴇʟ, ᴘᴏsᴛ, ᴏʀ sʜᴏʀᴛ ʟɪɴᴋ.')}\n"
        f"2️⃣ {to_stylish('ᴘᴀsᴛᴇ ᴀɴᴅ sᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ ɪɴ ᴛʜɪs ᴄʜᴀᴛ.')}\n"
        f"3️⃣ {to_stylish('ᴡᴀɪᴛ 1-2 sᴇᴄᴏɴᴅs ғᴏʀ ɪɴsᴛᴀɴᴛ ᴍᴇᴅɪᴀ ᴀɴᴀʟʏsɪs.')}\n"
        f"4️⃣ {to_stylish('ᴄʜᴏᴏsᴇ ʏᴏᴜʀ ᴘʀᴇғᴇʀʀᴇᴅ ʀᴇsᴏʟᴜᴛɪᴏɴ ᴏʀ ᴛᴀᴘ ᴀᴜᴅɪᴏ.')}\n"
        f"5️⃣ {to_stylish('ᴇɴᴊᴏʏ ɪɴsᴛᴀɴᴛ ʜɪɢʜ-sᴘᴇᴇᴅ ᴅᴇʟɪᴠᴇʀʏ!')}\n\n"
        f"📌 <b>{to_stylish('ᴄᴏᴍᴍᴀɴᴅs:')}</b>\n"
        f"• /start — {to_stylish('sᴛᴀʀᴛ ʙᴏᴛ & ᴠɪᴇᴡ ᴍᴀɪɴ ᴍᴇɴᴜ')}\n"
        f"• /help — {to_stylish('ʜᴏᴡ ᴛᴏ ᴜsᴇ')}\n"
        f"• /ping — {to_stylish('sᴇʀᴠᴇʀ sᴘᴇᴇᴅ & 24/7 sᴛᴀᴛᴜs')}\n"
        f"• /stats — {to_stylish('ʟɪᴠᴇ ᴅᴏᴡɴʟᴏᴀᴅ sᴛᴀᴛɪsᴛɪᴄs')}"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.HTML
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ping command to test bot responsiveness."""
    start_t = time.time()
    msg = await update.message.reply_text(f"⚡ <i>{to_stylish('ᴘɪɴɢɪɴɢ sᴇʀᴠᴇʀs...')}</i>", parse_mode=ParseMode.HTML)
    latency = int((time.time() - start_t) * 1000)
    await msg.edit_text(
        f"🚀 <b>{to_stylish('ᴘᴏɴɢ!')}</b> {to_stylish('ʟᴀᴛᴇɴᴄʏ:')} <code>{latency}ms</code>\n"
        f"🟢 <b>{to_stylish('sᴛᴀᴛᴜs:')}</b> {to_stylish('24/7 ʟɪᴠᴇ & ᴏᴘᴇʀᴀᴛɪᴏɴᴀʟ')}\n"
        f"👑 <b>{to_stylish('ᴏᴡɴᴇʀ:')}</b> @{html.escape(OWNER_USERNAME)}",
        parse_mode=ParseMode.HTML
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays bot usage statistics in stylish aesthetic font."""
    stats = db.get_stats()
    text = (
        f"📊 <b>{to_stylish('sᴛʀɪɴɢ ɢᴇɴ ʙᴏᴛ ʟɪᴠᴇ ᴀɴᴀʟʏᴛɪᴄs:')}</b>\n\n"
        f"👥 <b>{to_stylish('ᴛᴏᴛᴀʟ ʀᴇɢɪsᴛᴇʀᴇᴅ ᴜsᴇʀs:')}</b> <code>{stats['total_users']}</code>\n"
        f"📥 <b>{to_stylish('ᴛᴏᴛᴀʟ ᴍᴇᴅɪᴀ ᴅᴏᴡɴʟᴏᴀᴅs:')}</b> <code>{stats['total_downloads']}</code>\n"
        f"⚡ <b>{to_stylish('ᴅᴏᴡɴʟᴏᴀᴅs ᴛᴏᴅᴀʏ:')}</b> <code>{stats['today_downloads']}</code>\n"
        f"💾 <b>{to_stylish('ɪɴsᴛᴀɴᴛ ᴄᴀᴄʜᴇᴅ ᴍᴇᴅɪᴀ:')}</b> <code>{stats.get('total_cached', 0)}</code>\n"
        f"🚫 <b>{to_stylish('ʙᴀɴɴᴇᴅ ᴜsᴇʀs:')}</b> <code>{stats['banned_users']}</code>\n\n"
        f"👑 <b>{to_stylish('ᴅᴇᴠᴇʟᴏᴘᴇᴅ ʙʏ:')}</b> @{html.escape(OWNER_USERNAME)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ==========================================
# OWNER & ADMIN COMMANDS
# ==========================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard for owner."""
    if not is_owner(update):
        return

    stats = db.get_stats()
    has_cookies = os.path.exists(COOKIES_PATH)
    cookie_status = "✅ ᴀᴄᴛɪᴠᴇ" if has_cookies else "❌ ɴᴏɴᴇ"

    admin_text = (
        f"👑 <b>{to_stylish('ᴏᴡɴᴇʀ ᴀᴅᴍɪɴ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ')}</b>\n\n"
        f"👤 <b>{to_stylish('ᴏᴡɴᴇʀ:')}</b> @{html.escape(OWNER_USERNAME)}\n"
        f"👥 <b>{to_stylish('ᴛᴏᴛᴀʟ ᴜsᴇʀs:')}</b> <code>{stats['total_users']}</code>\n"
        f"📥 <b>{to_stylish('ᴛᴏᴛᴀʟ ᴅᴏᴡɴʟᴏᴀᴅs:')}</b> <code>{stats['total_downloads']}</code>\n"
        f"📅 <b>{to_stylish('ᴛᴏᴅᴀʏ\'s ᴛᴀsᴋs:')}</b> <code>{stats['today_downloads']}</code>\n"
        f"💾 <b>{to_stylish('ᴄᴀᴄʜᴇᴅ ᴠɪᴅᴇᴏs/ᴀᴜᴅɪᴏ:')}</b> <code>{stats.get('total_cached', 0)}</code>\n"
        f"🍪 <b>{to_stylish('ᴄᴏᴏᴋɪᴇs sᴛᴀᴛᴜs:')}</b> {cookie_status}\n\n"
        f"⚙️ <b>{to_stylish('ᴀᴠᴀɪʟᴀʙʟᴇ ᴀᴅᴍɪɴ ᴄᴏᴍᴍᴀɴᴅs:')}</b>\n"
        f"• <code>/setcookies</code> — {to_stylish('ᴜᴘʟᴏᴀᴅ cookies.txt ғɪʟᴇ ᴛᴏ ᴜɴʟᴏᴄᴋ ᴀʟʟ ʀᴇsᴛʀɪᴄᴛɪᴏɴs')}\n"
        f"• <code>/delcookies</code> — {to_stylish('ʀᴇᴍᴏᴠᴇ sᴀᴠᴇᴅ cookies.txt')}\n"
        f"• <code>/broadcast &lt;text&gt;</code> — {to_stylish('sᴇɴᴅ ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ ᴛᴏ ᴀʟʟ ᴜsᴇʀs')}\n"
        f"• <code>/ban &lt;user_id&gt;</code> — {to_stylish('ʙᴀɴ ᴀ ᴜsᴇʀ')}\n"
        f"• <code>/unban &lt;user_id&gt;</code> — {to_stylish('ᴜɴʙᴀɴ ᴀ ᴜsᴇʀ')}\n"
        f"• <code>/stats</code> — {to_stylish('ʟɪᴠᴇ sᴛᴀᴛɪsᴛɪᴄs')}"
    )
    await update.message.reply_text(admin_text, parse_mode=ParseMode.HTML)


async def setcookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allows the owner to upload or set cookies.txt directly in chat."""
    if not is_owner(update):
        return

    message = update.effective_message
    if not message:
        return

    # Check if a document was sent
    if message.document:
        doc = message.document
        if not doc.file_name.endswith(('.txt', '.cookie')):
            await message.reply_text(f"⚠️ {to_stylish('ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴀ valid .txt cookies ғɪʟᴇ.')}", parse_mode=ParseMode.HTML)
            return

        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(str(COOKIES_PATH))
        await message.reply_text(
            f"✅ <b>{to_stylish('ᴄᴏᴏᴋɪᴇs sᴜᴄᴄᴇssғᴜʟʟʏ sᴀᴠᴇᴅ & ᴀᴄᴛɪᴠᴀᴛᴇᴅ!')}</b>\n\n"
            f"🎉 {to_stylish('ʏᴏᴜᴛᴜʙᴇ & ɪɴsᴛᴀɢʀᴀᴍ ᴀʀᴇ ɴᴏᴡ 100% ᴜɴʟᴏᴄᴋᴇᴅ ᴡɪᴛʜ ɴᴏ ʙᴏᴛ ʟɪᴍɪᴛs.')}",
            parse_mode=ParseMode.HTML
        )
        return

    # Check if text was passed
    if context.args:
        cookie_data = " ".join(context.args)
        with open(COOKIES_PATH, "w") as f:
            f.write(cookie_data)
        await message.reply_text(
            f"✅ <b>{to_stylish('ᴄᴏᴏᴋɪᴇs sᴜᴄᴄᴇssғᴜʟʟʏ sᴀᴠᴇᴅ!')}</b>",
            parse_mode=ParseMode.HTML
        )
        return

    instructions = (
        f"🍪 <b>{to_stylish('ʜᴏᴡ ᴛᴏ sᴇᴛ ᴄᴏᴏᴋɪᴇs:')}</b>\n\n"
        f"1️⃣ {to_stylish('ᴏᴘᴇɴ ʏᴏᴜʀ ʙʀᴏᴡsᴇʀ (ᴄʜʀᴏᴍᴇ/ғɪʀᴇғᴏx) ᴀɴᴅ ʟᴏɢ ɪɴᴛᴏ ɪɴsᴛᴀɢʀᴀᴍ & ʏᴏᴜᴛᴜʙᴇ.')}\n"
        f"2️⃣ {to_stylish('ɪɴsᴛᴀʟʟ ᴛʜᴇ ғʀᴇᴇ ᴇxᴛᴇɴsɪᴏɴ:')} <i>Get cookies.txt LOCALLY</i>\n"
        f"3️⃣ {to_stylish('ᴄʟɪᴄᴋ export ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ cookies.txt')}\n"
        f"4️⃣ {to_stylish('sɪᴍᴘʟʏ sᴇɴᴅ/ᴜᴘʟᴏᴀᴅ ᴛʜᴀᴛ cookies.txt ғɪʟᴇ ʜᴇʀᴇ ᴡɪᴛʜ ᴄᴀᴘᴛɪᴏɴ')} <code>/setcookies</code>\n\n"
        f"✨ {to_stylish('ᴛʜɪs ᴜɴʟᴏᴄᴋs ᴇᴠᴇʀʏ sɪɴɢʟᴇ ʀᴇᴇʟ, sᴛᴏʀʏ, ᴀɴᴅ 4ᴋ ʏᴏᴜᴛᴜʙᴇ ᴠɪᴅᴇᴏ ᴡɪᴛʜ 0 ʙʟᴏᴄᴋs!')}"
    )
    await message.reply_text(instructions, parse_mode=ParseMode.HTML)


async def delcookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes saved cookies.txt."""
    if not is_owner(update):
        return

    if os.path.exists(COOKIES_PATH):
        try:
            os.remove(COOKIES_PATH)
            await update.message.reply_text(f"🗑 <b>{to_stylish('ᴄᴏᴏᴋɪᴇs ғɪʟᴇ ʀᴇᴍᴏᴠᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ.')}</b>", parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(f"Error removing cookies: {e}")
    else:
        await update.message.reply_text(f"ℹ️ {to_stylish('ɴᴏ cookies.txt ғɪʟᴇ ᴡᴀs ғᴏᴜɴᴅ.')}", parse_mode=ParseMode.HTML)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast announcement to all bot users (Owner only)."""
    if not is_owner(update):
        return

    if not context.args:
        await update.message.reply_text(
            f"⚠️ {to_stylish('ᴜsᴀɢᴇ:')} <code>/broadcast &lt;your message here&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    broadcast_text = html.escape(" ".join(context.args))
    user_ids = db.get_all_user_ids()
    status_msg = await update.message.reply_text(
        f"🚀 <i>{to_stylish('ʙʀᴏᴀᴅᴄᴀsᴛɪɴɢ ᴛᴏ')} {len(user_ids)} {to_stylish('ᴜsᴇʀs...')}</i>",
        parse_mode=ParseMode.HTML
    )

    success_count = 0
    fail_count = 0

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>{to_stylish('ᴀɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ ғʀᴏᴍ')} @{html.escape(OWNER_USERNAME)}:</b>\n\n{broadcast_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_social_keyboard()
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"✅ <b>{to_stylish('ʙʀᴏᴀᴅᴄᴀsᴛ ᴄᴏᴍᴘʟᴇᴛᴇ!')}</b>\n\n"
        f"📨 {to_stylish('sᴇɴᴛ:')} <code>{success_count}</code>\n"
        f"❌ {to_stylish('ғᴀɪʟᴇᴅ / ʙʟᴏᴄᴋᴇᴅ:')} <code>{fail_count}</code>",
        parse_mode=ParseMode.HTML
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban user by ID (Owner only)."""
    if not is_owner(update) or not context.args:
        return
    try:
        target_id = int(context.args[0])
        db.set_user_ban(target_id, True)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> has been banned.", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban user by ID (Owner only)."""
    if not is_owner(update) or not context.args:
        return
    try:
        target_id = int(context.args[0])
        db.set_user_ban(target_id, False)
        await update.message.reply_text(f"✅ User <code>{target_id}</code> has been unbanned.", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


# ==========================================
# MEDIA PROCESSING
# ==========================================

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detects links, presents quality options, and processes downloads."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    # Check document upload for cookies
    if message.document and is_owner(update):
        if message.caption and "/setcookies" in message.caption:
            await setcookies_command(update, context)
            return

    if not message.text:
        return

    if db.is_user_banned(user.id):
        return

    db.add_or_update_user(user.id, user.username, user.first_name)

    urls = extract_urls(message.text)
    if not urls:
        return

    has_joined = await check_fsub(context.bot, user.id)
    if not has_joined:
        join_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(to_stylish("📢 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ"), url=OWNER_CHANNEL_LINK)],
            [InlineKeyboardButton(to_stylish("💬 ᴊᴏɪɴ sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ"), url=OWNER_GROUP_LINK)],
        ])
        await message.reply_text(
            f"⚠️ <b>{to_stylish('ᴀᴄᴄᴇss ʀᴇsᴛʀɪᴄᴛᴇᴅ!')}</b>\n\n"
            f"{to_stylish('ᴘʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴏғғɪᴄɪᴀʟ ᴄʜᴀɴɴᴇʟ ᴀɴᴅ ɢʀᴏᴜᴘ ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ғᴏʀ ғʀᴇᴇ ʜɪɢʜ-sᴘᴇᴇᴅ ᴅᴏᴡɴʟᴏᴀᴅs!')}\n\n"
            f"👉 <a href=\"{OWNER_CHANNEL_LINK}\">{to_stylish('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ')}</a> &amp; <a href=\"{OWNER_GROUP_LINK}\">{to_stylish('ᴊᴏɪɴ ɢʀᴏᴜᴘ')}</a>",
            reply_markup=join_keyboard,
            parse_mode=ParseMode.HTML
        )
        return

    target_url = sanitize_url(urls[0])
    prune_cache()

    # Feature 3: Instant Zero-CPU & Zero-Bandwidth Cache Delivery (0.1s)
    cached = db.get_cached_media(target_url)
    if cached:
        logger.info(f"Instant SQLite cache hit for URL: {target_url}")
        file_id = cached["file_id"]
        media_type = cached.get("media_type", "video")
        title = cached.get("title", "Media File")
        duration = cached.get("duration", 0)

        caption = (
            f"⚡ <b>{to_stylish('ɪɴsᴛᴀɴᴛ ᴄᴀᴄʜᴇ ᴅᴇʟɪᴠᴇʀʏ (0.1s)')}</b> ⚡\n\n"
            f"🎬 <b>{html.escape(title)}</b>\n\n"
            f"⚡ {to_stylish('ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ᴠɪᴀ:')} @String_rebot\n"
            f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
        )
        try:
            if media_type == "audio":
                await message.reply_audio(
                    audio=file_id,
                    caption=caption,
                    duration=duration,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.reply_video(
                    video=file_id,
                    caption=caption,
                    duration=duration,
                    supports_streaming=True,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            db.increment_download(user.id, "Instant-Cache", media_type)
            return
        except Exception as cache_err:
            logger.warning(f"Cached file_id invalid ({cache_err}), falling back to fresh extraction.")

    # Telegram Story Link Handler (e.g. t.me/username/s/123)
    tg_story_match = re.search(r'(?:https?://)?(?:www\.)?t\.me/([a-zA-Z0-9_]+)/s/(\d+)', target_url)
    if tg_story_match:
        story_user = tg_story_match.group(1)
        story_id = tg_story_match.group(2)
        try:
            status_msg = await message.reply_text(
                f"⚡ <i>{to_stylish('ғᴇᴛᴄʜɪɴɢ ᴛᴇʟᴇɢʀᴀᴍ ꜱᴛᴏʀʏ...')}</i>",
                parse_mode=ParseMode.HTML
            )
            req_story = urllib.request.Request(target_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req_story, timeout=8) as r_s:
                s_html = r_s.read().decode('utf-8', errors='ignore')
            cover_m = re.search(r'<img class=\"tgme_page_photo_image\" src=\"([^\"]+)\">', s_html)
            if cover_m:
                cover_url = cover_m.group(1)
                caption = (
                    f"👻 <b>{to_stylish('ᴛᴇʟᴇɢʀᴀᴍ ꜱᴛᴏʀʏ ᴄᴏᴠᴇʀ')}</b>\n\n"
                    f"👤 {to_stylish('ᴜꜱᴇʀ:')} @{html.escape(story_user)}\n"
                    f"🆔 {to_stylish('ꜱᴛᴏʀʏ ɪᴅ:')} <code>#{story_id}</code>\n\n"
                    f"💡 <i>{to_stylish('ᴛᴇʟᴇɢʀᴀᴍ ꜱᴛᴏʀɪᴇꜱ ᴀʀᴇ ᴘʀɪᴠᴀᴛᴇ ɪɴꜱɪᴅᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴀᴘᴘ. ᴄʜᴀɴɴᴇʟ ᴘᴏꜱᴛꜱ (t.me/channel/123) ᴀʀᴇ 100% ꜱᴜᴘᴘᴏʀᴛᴇᴅ!')}</i>\n\n"
                    f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
                )
                await message.reply_photo(
                    photo=cover_url,
                    caption=caption,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
                await status_msg.delete()
                db.increment_download(user.id, "Telegram", "story_preview")
                return
        except Exception as e:
            logger.warning(f"Telegram story scrape failed: {e}")

    # Telegram Public Message Link Handler
    tg_match = re.search(r'(?:https?://)?(?:www\.)?t\.me/(?:c/)?([a-zA-Z0-9_]+)/(\d+)', target_url)
    if tg_match and not ("t.me/+" in target_url or "joinchat" in target_url):
        channel_id = tg_match.group(1)
        post_id = int(tg_match.group(2))
        try:
            status_msg = await message.reply_text(
                f"⚡ <i>{to_stylish('ғᴇᴛᴄʜɪɴɢ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇᴅɪᴀ...')}</i>",
                parse_mode=ParseMode.HTML
            )
            await context.bot.copy_message(
                chat_id=message.chat_id,
                from_chat_id=f"@{channel_id}",
                message_id=post_id,
                reply_markup=get_media_sent_keyboard()
            )
            await status_msg.delete()
            db.increment_download(user.id, "Telegram", "direct")
            return
        except Exception as tg_err:
            logger.warning(f"Telegram copy_message failed: {tg_err}")
            # Fall back to standard extractor if copy fails

    status_msg = await message.reply_text(
        f"⚡ <i>{to_stylish('ᴀɴᴀʟʏᴢɪɴɢ ʏᴏᴜʀ ʟɪɴᴋ... ᴘʟᴇᴀsᴇ ᴡᴀɪᴛ.')}</i>",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message.message_id
    )

    try:
        info = await downloader.fetch_media_info(target_url)
    except Exception as e:
        logger.error(f"Failed to fetch media info for {target_url}: {e}")
        error_msg = str(e)
        if any(k in error_msg.lower() for k in ["sign in", "not a bot", "private video", "login", "forbidden", "rate-limit"]):
            friendly_err = (
                f"🔒 <b>{to_stylish('ᴛʜɪs ᴄᴏɴᴛᴇɴᴛ ʀᴇǫᴜɪʀᴇs ʟᴏɢɪɴ ᴏʀ ɪs ᴘʀɪᴠᴀᴛᴇ.')}</b>\n\n"
                f"🍪 <i>{to_stylish('ᴏᴡɴᴇʀ ᴄᴀɴ ᴜᴘʟᴏᴀᴅ cookies.txt ᴠɪᴀ /setcookies ᴛᴏ ᴜɴʟᴏᴄᴋ ᴀʟʟ ʀᴇsᴛʀɪᴄᴛɪᴏɴs 100%!')}</i>"
            )
        else:
            friendly_err = f"⚠️ <b>{to_stylish('ᴄᴏᴜʟᴅ ɴᴏᴛ ᴇxᴛʀᴀᴄᴛ ᴍᴇᴅɪᴀ. ᴍᴀᴋᴇ sᴜʀᴇ ᴛʜᴇ ʟɪɴᴋ ɪs ᴘᴜʙʟɪᴄ ᴀɴᴅ ᴠᴀʟɪᴅ.')}</b>"

        try:
            await status_msg.edit_text(
                f"❌ <b>{to_stylish('ᴇxᴛʀᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ')}</b>\n\n"
                f"{friendly_err}\n\n"
                f"💬 {to_stylish('ɴᴇᴇᴅ ʜᴇʟᴘ? ᴄᴏɴᴛᴀᴄᴛ')} @{html.escape(OWNER_USERNAME)}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    # Handle direct photo posts
    if info.get("is_image") and info.get("image_url"):
        await status_msg.edit_text(f"⏳ <i>{to_stylish('ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ ᴘʜᴏᴛᴏ ɪɴ ᴏʀɪɢɪɴᴀʟ ʀᴇsᴏʟᴜᴛɪᴏɴ...')}</i>", parse_mode=ParseMode.HTML)
        local_img = None
        try:
            local_img = await downloader.download_direct_image(info["image_url"])
            with open(local_img, "rb") as f:
                caption = (
                    f"📷 <b>{html.escape(info['title'])}</b>\n\n"
                    f"🌐 {to_stylish('ᴘʟᴀᴛғᴏʀᴍ:')} <code>{html.escape(info['extractor'])}</code>\n"
                    f"⚡ {to_stylish('ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ᴠɪᴀ:')} @String_rebot\n"
                    f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
                )
                await message.reply_photo(
                    photo=f,
                    caption=caption,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            db.increment_download(user.id, info.get("extractor", "Web"), "photo")
            await status_msg.delete()
        except Exception as e:
            logger.error(f"Image download error: {e}")
            await status_msg.edit_text(f"❌ <b>{to_stylish('ғᴀɪʟᴇᴅ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ᴘʜᴏᴛᴏ.')}</b>", parse_mode=ParseMode.HTML)
        finally:
            if local_img:
                downloader.cleanup_files(local_img)
        return

    # Store in memory cache
    task_id = os.urandom(4).hex()
    MEDIA_CACHE[task_id] = {
        "url": target_url,
        "direct_download_url": info.get("direct_download_url"),
        "title": info["title"],
        "duration": info["duration"],
        "thumbnail": info["thumbnail"],
        "extractor": info["extractor"],
        "uploader": info["uploader"],
        "timestamp": time.time()
    }

    # Build Interactive Quality Selection Grid in Stylish Aesthetic Font
    keyboard = []
    qualities = info.get("qualities", ["best"])

    quality_buttons = []
    for q in qualities:
        label = f"🎬 {q}ᴘ ʜᴅ" if isinstance(q, int) else to_stylish("🎬 ʙᴇsᴛ ǫᴜᴀʟɪᴛʏ")
        callback_data = f"dl:v:{q}:{task_id}"
        quality_buttons.append(InlineKeyboardButton(label, callback_data=callback_data))

    for i in range(0, len(quality_buttons), 2):
        keyboard.append(quality_buttons[i:i+2])

    keyboard.append([
        InlineKeyboardButton(to_stylish("🎵 ᴅᴏᴡɴʟᴏᴀᴅ ᴀᴜᴅɪᴏ / sᴏɴɢ (ᴍᴘ3)"), callback_data=f"dl:a:mp3:{task_id}")
    ])

    keyboard.append([
        InlineKeyboardButton(to_stylish("❌ ᴄᴀɴᴄᴇʟ"), callback_data=f"dl:c:0:{task_id}")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    caption_text = (
        f"🎬 <b>{html.escape(info['title'])}</b>\n\n"
        f"⏱ <b>{to_stylish('ᴅᴜʀᴀᴛɪᴏɴ:')}</b> <code>{info['duration_str']}</code>\n"
        f"👤 <b>{to_stylish('ᴀᴜᴛʜᴏʀ:')}</b> <code>{html.escape(info['uploader'])}</code>\n"
        f"🌐 <b>{to_stylish('ᴘʟᴀᴛғᴏʀᴍ:')}</b> <code>{html.escape(info['extractor'])}</code>\n\n"
        f"👇 <b>{to_stylish('sᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴅᴏᴡɴʟᴏᴀᴅ ǫᴜᴀʟɪᴛʏ ᴏʀ ᴇxᴛʀᴀᴄᴛ sᴏɴɢ:')}</b>"
    )

    try:
        await status_msg.edit_text(
            caption_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Failed to edit with HTML: {e}")
        await status_msg.edit_text(
            f"🎬 {info['title']}\n⏱ Duration: {info['duration_str']}\n\nSelect an option below:",
            reply_markup=reply_markup
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes button clicks for quality selection or audio extraction."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "dl":
        return

    action_type = parts[1]
    quality = parts[2]
    task_id = parts[3]

    if action_type == "c":
        MEDIA_CACHE.pop(task_id, None)
        await query.edit_message_text(f"❌ <b>{to_stylish('ᴅᴏᴡɴʟᴏᴀᴅ ᴄᴀɴᴄᴇʟʟᴇᴅ.')}</b>", parse_mode=ParseMode.HTML)
        return

    media_data = MEDIA_CACHE.get(task_id)
    if not media_data:
        await query.edit_message_text(
            f"⚠️ <b>{to_stylish('sᴇssɪᴏɴ ᴇxᴘɪʀᴇᴅ. ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴛʜᴇ ʟɪɴᴋ ᴀɢᴀɪɴ.')}</b>",
            parse_mode=ParseMode.HTML
        )
        return

    url = media_data["url"]
    direct_download_url = media_data.get("direct_download_url")
    title = media_data["title"]
    is_audio = (action_type == "a")

    status_label = to_stylish("ᴀᴜᴅɪᴏ (ᴍᴘ3)") if is_audio else f"{quality}ᴘ {to_stylish('ᴠɪᴅᴇᴏ')}"
    await query.edit_message_text(
        f"⏳ <i>{to_stylish('ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ')} {status_label}...</i>\n\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"⚡ <i>{to_stylish('ᴜʟᴛʀᴀ-ʜɪɢʜ sᴘᴇᴇᴅ ᴛʀᴀɴsғᴇʀ ɪɴ ᴘʀᴏɢʀᴇss...')}</i>",
        parse_mode=ParseMode.HTML
    )

    download_result = None
    file_path = None

    try:
        if is_audio:
            download_result = await downloader.download_media(url, media_type="audio")
        else:
            download_result = await downloader.download_media(
                url,
                media_type="video",
                quality=quality,
                direct_download_url=direct_download_url
            )

        file_path = download_result["file_path"]
        file_size_mb = download_result["file_size_mb"]

        if file_size_mb > MAX_FILE_SIZE_MB:
            await query.edit_message_text(
                f"⚠️ <b>{to_stylish('ғɪʟᴇ ɪs ᴛᴏᴏ ʟᴀʀɢᴇ ғᴏʀ ᴛᴇʟᴇɢʀᴀᴍ')} ({file_size_mb:.1f} MB &gt; 50 MB)!</b>\n\n"
                f"💡 <b>{to_stylish('sᴏʟᴜᴛɪᴏɴ:')}</b>\n"
                f"• {to_stylish('sᴇɴᴅ ʟɪɴᴋ ᴀɢᴀɪɴ ᴀɴᴅ ᴄʜᴏᴏsᴇ 480ᴘ ᴏʀ 360ᴘ')}\n"
                f"• {to_stylish('ᴏʀ ᴄʜᴏᴏsᴇ ᴅᴏᴡɴʟᴏᴀᴅ ᴀᴜᴅɪᴏ (ᴍᴘ3)')}",
                parse_mode=ParseMode.HTML
            )
            return

        await query.edit_message_text(
            f"📤 <i>{to_stylish('ᴜᴘʟᴏᴀᴅɪɴɢ')} {status_label} ({file_size_mb:.1f} MB)...</i>",
            parse_mode=ParseMode.HTML
        )

        chat_id = query.message.chat_id

        if is_audio:
            with open(file_path, "rb") as f:
                sent_msg = await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    title=download_result["title"],
                    performer=download_result["uploader"],
                    duration=download_result["duration"],
                    caption=(
                        f"🎵 <b>{html.escape(download_result['title'])}</b>\n\n"
                        f"👤 {to_stylish('ᴀʀᴛɪsᴛ:')} <code>{html.escape(download_result['uploader'])}</code>\n"
                        f"⚡ {to_stylish('ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ᴠɪᴀ:')} @String_rebot\n"
                        f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
                    ),
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            if sent_msg and sent_msg.audio:
                db.set_cached_media(
                    url,
                    sent_msg.audio.file_id,
                    "audio",
                    download_result["title"],
                    download_result["duration"]
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "audio")
        else:
            with open(file_path, "rb") as f:
                sent_msg = await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=(
                        f"🎬 <b>{html.escape(download_result['title'])}</b>\n\n"
                        f"✨ {to_stylish('ǫᴜᴀʟɪᴛʏ:')} <code>{quality}p</code> ({file_size_mb:.1f} MB)\n"
                        f"⚡ {to_stylish('ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ᴠɪᴀ:')} @String_rebot\n"
                        f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
                    ),
                    duration=download_result["duration"],
                    supports_streaming=True,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            if sent_msg and sent_msg.video:
                db.set_cached_media(
                    url,
                    sent_msg.video.file_id,
                    "video",
                    download_result["title"],
                    download_result["duration"]
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "video")

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Download/Upload error: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ <b>{to_stylish('ᴅᴏᴡɴʟᴏᴀᴅ ᴇʀʀᴏʀ')}</b>\n\n"
            f"<code>{html.escape(str(e)[:150])}</code>\n\n"
            f"💬 {to_stylish('ʀᴇᴘᴏʀᴛ ᴛᴏ:')} @{html.escape(OWNER_USERNAME)}",
            parse_mode=ParseMode.HTML
        )
    finally:
        if file_path:
            downloader.cleanup_files(file_path)
        MEDIA_CACHE.pop(task_id, None)


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Feature 2: Inline Mode Anywhere.
    Allows users to download or share media directly in any chat/group using @String_rebot <link>!
    """
    inline_query = update.inline_query
    if not inline_query:
        return

    query_text = (inline_query.query or "").strip()
    results = []

    if not query_text:
        # User opened inline mode without query: show informative guide card
        results.append(
            InlineQueryResultArticle(
                id="inline_guide",
                title=to_stylish("⚡ ᴜʟᴛʀᴀ-ғᴀsᴛ ᴍᴇᴅɪᴀ ᴅᴏᴡɴʟᴏᴀᴅᴇʀ"),
                description=to_stylish("ᴘᴀsᴛᴇ ᴀɴʏ ᴠɪᴅᴇᴏ ᴏʀ ᴀᴜᴅɪᴏ ᴜʀʟ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ɪɴsᴛᴀɴᴛʟʏ!"),
                input_message_content=InputTextMessageContent(
                    f"🚀 <b>{to_stylish('ᴜsᴇ @String_rebot ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ᴠɪᴅᴇᴏs ᴀɴʏᴡʜᴇʀᴇ!')}</b>\n\n"
                    f"✨ {to_stylish('sᴜᴘᴘᴏʀᴛs: ɪɴsᴛᴀɢʀᴀᴍ, ʏᴏᴜᴛᴜʙᴇ, ᴘɪɴᴛᴇʀᴇsᴛ, ғᴀᴄᴇʙᴏᴏᴋ, x, sɴᴀᴘᴄʜᴀᴛ, ᴛᴇʟᴇɢʀᴀᴍ & ᴍᴏʀᴇ!')}\n\n"
                    f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}",
                    parse_mode=ParseMode.HTML
                ),
                reply_markup=get_social_keyboard()
            )
        )
        await inline_query.answer(results, cache_time=10)
        return

    urls = extract_urls(query_text)
    if not urls:
        results.append(
            InlineQueryResultArticle(
                id="no_link",
                title=to_stylish("⚠️ ɴᴏ ᴠᴀʟɪᴅ ʟɪɴᴋ ғᴏᴜɴᴅ"),
                description=to_stylish("ᴘʟᴇᴀsᴇ ᴘᴀsᴛᴇ ᴀ ᴠᴀʟɪᴅ ᴍᴇᴅɪᴀ ᴜʀʟ (ɪɴsᴛᴀɢʀᴀᴍ, ʏᴏᴜᴛᴜʙᴇ, ᴘɪɴᴛᴇʀᴇsᴛ, ᴇᴛᴄ.)"),
                input_message_content=InputTextMessageContent(
                    f"⚠️ {to_stylish('ᴘʟᴇᴀsᴇ ᴘʀᴏᴠɪᴅᴇ ᴀ ᴠᴀʟɪᴅ ᴜʀʟ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ᴍᴇᴅɪᴀ.')}",
                    parse_mode=ParseMode.HTML
                )
            )
        )
        await inline_query.answer(results, cache_time=10)
        return

    clean_url = sanitize_url(urls[0])
    cached = db.get_cached_media(clean_url)

    if cached:
        # INSTANT ZERO-LATENCY INLINE CACHE DELIVERY!
        file_id = cached["file_id"]
        media_type = cached.get("media_type", "video")
        title = cached.get("title", "Cached Media")

        caption = (
            f"⚡ <b>{to_stylish('ɪɴsᴛᴀɴᴛ ᴄᴀᴄʜᴇ ᴅᴇʟɪᴠᴇʀʏ (0.1s)')}</b> ⚡\n\n"
            f"🎬 <b>{html.escape(title)}</b>\n\n"
            f"⚡ {to_stylish('ᴅᴏᴡɴʟᴏᴀᴅᴇᴅ ᴠɪᴀ:')} @String_rebot\n"
            f"👑 {to_stylish('ᴏᴡɴᴇʀ:')} @{html.escape(OWNER_USERNAME)}"
        )

        if media_type == "audio":
            results.append(
                InlineQueryResultCachedAudio(
                    id=f"ca_{int(time.time())}",
                    audio_file_id=file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_media_sent_keyboard()
                )
            )
        else:
            results.append(
                InlineQueryResultCachedVideo(
                    id=f"cv_{int(time.time())}",
                    video_file_id=file_id,
                    title=f"⚡ {title}",
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_media_sent_keyboard()
                )
            )
    else:
        # Direct deep-link to download via bot
        results.append(
            InlineQueryResultArticle(
                id=f"dl_{int(time.time())}",
                title=to_stylish("⚡ ᴅᴏᴡɴʟᴏᴀᴅ ᴠɪᴀ @String_rebot"),
                description=f"{clean_url[:50]}...",
                input_message_content=InputTextMessageContent(
                    f"🎬 <b>{to_stylish('ᴍᴇᴅɪᴀ ᴅᴏᴡɴʟᴏᴀᴅ ʀᴇǫᴜᴇsᴛ')}</b>\n\n"
                    f"🔗 <code>{html.escape(clean_url)}</code>\n\n"
                    f"⚡ {to_stylish('ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ ɪɴ ʜᴅ ᴏʀ ᴇxᴛʀᴀᴄᴛ ᴍᴘ3:')}",
                    parse_mode=ParseMode.HTML
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(to_stylish("📥 ᴏᴘᴇɴ ʙᴏᴛ ᴛᴏ ᴅᴏᴡɴʟᴏᴀᴅ"), url=f"https://t.me/String_rebot?start=help")],
                    [InlineKeyboardButton(to_stylish("📢 ᴜᴘᴅᴀᴛᴇs ᴄʜᴀɴɴᴇʟ"), url=OWNER_CHANNEL_LINK)]
                ])
            )
        )

    await inline_query.answer(results, cache_time=10)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler for unexpected bot exceptions."""
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if isinstance(context.error, RetryAfter):
        logger.warning(f"Flood control exceeded. Sleeping for {context.error.retry_after}s")
        await asyncio.sleep(context.error.retry_after)
    elif isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("Telegram network timeout occurred. Auto-reconnecting.")


async def post_init(application: Application):
    """Runs on bot startup inside the async event loop."""
    await start_web_server()
    logger.info("Bot and Web Server are now 100% active and running!")


def main():
    """Main application entry point."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not configured! Please provide your Telegram Bot Token.")
        return

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # User commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("stats", stats_command))

    # Owner / Admin commands
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("panel", admin_command))
    application.add_handler(CommandHandler("setcookies", setcookies_command))
    application.add_handler(CommandHandler("delcookies", delcookies_command))
    application.add_handler(CommandHandler("cookies", setcookies_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))

    # Inline mode query handler (Feature 2)
    application.add_handler(InlineQueryHandler(handle_inline_query))

    # Document handler (for cookies upload)
    application.add_handler(MessageHandler(filters.Document.ALL, handle_incoming_message))

    # Callbacks and links
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_message))

    # Global error handler
    application.add_error_handler(error_handler)

    logger.info(f"Starting String Gen Bot (@String_rebot) with polling (Owner: @{OWNER_USERNAME})...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
