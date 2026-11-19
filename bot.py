import os
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
from downloader import MediaDownloader, extract_urls, format_bytes, format_duration
from server import start_web_server
from database import db

# Cache to store media extraction data (maps uuid -> media_info)
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
        # If bot is not admin in channel, don't block users
        return True


def get_social_keyboard() -> InlineKeyboardMarkup:
    """Returns the main social & support keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Updates Channel", url=OWNER_CHANNEL_LINK),
            InlineKeyboardButton("💬 Support Group", url=OWNER_GROUP_LINK),
        ],
        [
            InlineKeyboardButton("👑 Developer", url=f"https://t.me/{OWNER_USERNAME}"),
            InlineKeyboardButton("📸 Instagram", url=OWNER_INSTAGRAM_LINK),
        ],
        [
            InlineKeyboardButton("👻 Snapchat", url=OWNER_SNAPCHAT_LINK),
        ]
    ])


def get_media_sent_keyboard() -> InlineKeyboardMarkup:
    """Keyboard attached below every downloaded media."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Updates Channel", url=OWNER_CHANNEL_LINK),
            InlineKeyboardButton("💬 Support Group", url=OWNER_GROUP_LINK),
        ]
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command with world-class UI."""
    user = update.effective_user
    if not user:
        return

    # Register user in database
    db.add_or_update_user(user.id, user.username, user.first_name)

    if db.is_user_banned(user.id):
        await update.message.reply_text("🚫 *You are banned from using this bot.*", parse_mode=ParseMode.MARKDOWN)
        return

    welcome_text = (
        f"⚡ *Welcome {user.first_name}!* ⚡\n\n"
        "I am **String Gen Media Bot** — the world's most advanced, 24/7 high-speed downloader! 🚀\n\n"
        "✨ *Supported Platforms:*\n"
        "• 📸 **Instagram** — Reels, Posts, Stories, Audio\n"
        "• 🎥 **YouTube** — Videos, Shorts (1080p, 720p, 480p, MP3)\n"
        "• 📌 **Pinterest** — HD Video Pins & Original Photos\n"
        "• 📘 **Facebook** — Reels, Watch Videos & Stories\n"
        "• 🐦 **Twitter / X** — HD Videos & Clips\n"
        "• 🤖 **Reddit** — Videos with clean merged audio\n"
        "• 👻 **Snapchat** — Spotlight & Stories\n"
        "• 🎵 **TikTok, SoundCloud, Vimeo & 1800+ more!**\n\n"
        "👑 **Owner / Developer:** @ex\\_amrit\n"
        "📢 **Updates Channel:** [Private Think](https://t.me/privatethink)\n"
        "💬 **Support Group:** [Amrit Feel](https://t.me/amrit_feel)\n\n"
        "👉 *Just paste any link below to download instantly in full quality!*"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    help_text = (
        "📖 *How to use String Gen Media Bot:*\n\n"
        "1️⃣ Copy any video, reel, post, or short link from any app.\n"
        "2️⃣ Paste and send the link in this chat.\n"
        "3️⃣ Wait 1-3 seconds for instant media analysis.\n"
        "4️⃣ Choose your preferred resolution (1080p, 720p, etc.) or tap *🎵 Audio (MP3)*.\n"
        "5️⃣ Enjoy instant high-speed delivery!\n\n"
        "📌 *Bot Commands:*\n"
        "• `/start` — Start bot & view main menu\n"
        "• `/help` — How to use & tips\n"
        "• `/ping` — Check server latency & 24/7 status\n"
        "• `/stats` — View live download statistics"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ping command to test bot responsiveness."""
    start_t = time.time()
    msg = await update.message.reply_text("⚡ *Pinging servers...*", parse_mode=ParseMode.MARKDOWN)
    latency = int((time.time() - start_t) * 1000)
    await msg.edit_text(
        f"🚀 *Pong!* Latency: `{latency}ms`\n"
        f"🟢 **Status:** 24/7 Live & Operational\n"
        f"👑 **Owner:** @{OWNER_USERNAME}",
        parse_mode=ParseMode.MARKDOWN
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays bot usage statistics."""
    stats = db.get_stats()
    text = (
        "📊 *String Gen Bot Live Analytics:*\n\n"
        f"👥 **Total Registered Users:** `{stats['total_users']}`\n"
        f"📥 **Total Media Downloads:** `{stats['total_downloads']}`\n"
        f"⚡ **Downloads Today:** `{stats['today_downloads']}`\n"
        f"🚫 **Banned Users:** `{stats['banned_users']}`\n\n"
        f"👑 **Developed by:** @{OWNER_USERNAME}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ==========================================
# OWNER & ADMIN COMMANDS
# ==========================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard for owner."""
    if not is_owner(update):
        return

    stats = db.get_stats()
    admin_text = (
        "👑 *Owner Admin Control Panel*\n\n"
        f"👤 **Owner:** @{OWNER_USERNAME}\n"
        f"👥 **Total Users:** `{stats['total_users']}`\n"
        f"📥 **Total Downloads:** `{stats['total_downloads']}`\n"
        f"📅 **Today's Tasks:** `{stats['today_downloads']}`\n\n"
        "⚙️ *Available Admin Commands:*\n"
        "• `/broadcast <text>` — Send broadcast announcement to all users\n"
        "• `/ban <user_id>` — Ban a user\n"
        "• `/unban <user_id>` — Unban a user\n"
        "• `/stats` — Live statistics"
    )
    await update.message.reply_text(admin_text, parse_mode=ParseMode.MARKDOWN)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast announcement to all bot users (Owner only)."""
    if not is_owner(update):
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: `/broadcast <your message here>`", parse_mode=ParseMode.MARKDOWN)
        return

    broadcast_text = " ".join(context.args)
    user_ids = db.get_all_user_ids()
    status_msg = await update.message.reply_text(f"🚀 *Broadcasting to {len(user_ids)} users...*", parse_mode=ParseMode.MARKDOWN)

    success_count = 0
    fail_count = 0

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 *Announcement from @{OWNER_USERNAME}:*\n\n{broadcast_text}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_social_keyboard()
            )
            success_count += 1
            await asyncio.sleep(0.05)  # Telegram rate limit prevention
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📨 Sent: `{success_count}`\n"
        f"❌ Failed / Blocked: `{fail_count}`",
        parse_mode=ParseMode.MARKDOWN
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban user by ID (Owner only)."""
    if not is_owner(update) or not context.args:
        return
    try:
        target_id = int(context.args[0])
        db.set_user_ban(target_id, True)
        await update.message.reply_text(f"✅ User `{target_id}` has been banned.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban user by ID (Owner only)."""
    if not is_owner(update) or not context.args:
        return
    try:
        target_id = int(context.args[0])
        db.set_user_ban(target_id, False)
        await update.message.reply_text(f"✅ User `{target_id}` has been unbanned.", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("Invalid User ID.")


# ==========================================
# MEDIA PROCESSING
# ==========================================

async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detects links, presents quality options, and processes downloads."""
    message = update.effective_message
    user = update.effective_user
    if not message or not message.text or not user:
        return

    # Check ban
    if db.is_user_banned(user.id):
        return

    # Update database
    db.add_or_update_user(user.id, user.username, user.first_name)

    urls = extract_urls(message.text)
    if not urls:
        return

    # Check Force Subscribe
    has_joined = await check_fsub(context.bot, user.id)
    if not has_joined:
        join_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Updates Channel", url=OWNER_CHANNEL_LINK)],
            [InlineKeyboardButton("💬 Join Support Group", url=OWNER_GROUP_LINK)],
        ])
        await message.reply_text(
            "⚠️ *Access Restricted!*\n\n"
            f"Please join our official channel and group to use this bot for free high-speed downloads!\n\n"
            f"👉 [Join Channel]({OWNER_CHANNEL_LINK}) & [Join Group]({OWNER_GROUP_LINK})",
            reply_markup=join_keyboard,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    target_url = urls[0]
    prune_cache()

    status_msg = await message.reply_text(
        "🔍 *Analyzing your link... please wait.*",
        parse_mode=ParseMode.MARKDOWN,
        reply_to_message_id=message.message_id
    )

    try:
        info = await downloader.fetch_media_info(target_url)
    except Exception as e:
        logger.error(f"Failed to fetch media info for {target_url}: {e}")
        error_msg = str(e)
        if "Private video" in error_msg or "login" in error_msg.lower():
            friendly_err = "🔒 This content is from a private account or requires authorization."
        else:
            friendly_err = "⚠️ Could not extract media from this link. Make sure the link is public and accessible."

        await status_msg.edit_text(
            f"❌ *Extraction Failed*\n\n{friendly_err}\n\n💬 Need help? Contact @{OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Handle direct photo posts
    if info.get("is_image") and info.get("image_url"):
        await status_msg.edit_text("⏳ *Downloading photo in original resolution...*", parse_mode=ParseMode.MARKDOWN)
        local_img = None
        try:
            local_img = await downloader.download_direct_image(info["image_url"])
            with open(local_img, "rb") as f:
                caption = (
                    f"📷 *{info['title']}*\n\n"
                    f"🌐 Platform: `{info['extractor']}`\n"
                    f"⚡ Downloaded via: @String_rebot\n"
                    f"👑 Owner: @{OWNER_USERNAME}"
                )
                await message.reply_photo(
                    photo=f,
                    caption=caption,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
            db.increment_download(user.id, info.get("extractor", "Web"), "photo")
            await status_msg.delete()
        except Exception as e:
            logger.error(f"Image download error: {e}")
            await status_msg.edit_text("❌ *Failed to download photo.*", parse_mode=ParseMode.MARKDOWN)
        finally:
            if local_img:
                downloader.cleanup_files(local_img)
        return

    # Store in memory cache
    task_id = os.urandom(4).hex()
    MEDIA_CACHE[task_id] = {
        "url": target_url,
        "title": info["title"],
        "duration": info["duration"],
        "thumbnail": info["thumbnail"],
        "extractor": info["extractor"],
        "uploader": info["uploader"],
        "timestamp": time.time()
    }

    # Build Interactive Quality Selection Grid
    keyboard = []
    qualities = info.get("qualities", ["best"])

    quality_buttons = []
    for q in qualities:
        label = f"🎬 {q}p" if isinstance(q, int) else "🎬 Best Quality"
        callback_data = f"dl:v:{q}:{task_id}"
        quality_buttons.append(InlineKeyboardButton(label, callback_data=callback_data))

    # Arrange 2 per row
    for i in range(0, len(quality_buttons), 2):
        keyboard.append(quality_buttons[i:i+2])

    # Audio extraction button (Dedicated MP3 download)
    keyboard.append([
        InlineKeyboardButton("🎵 Download Audio / Song (MP3)", callback_data=f"dl:a:mp3:{task_id}")
    ])

    # Cancel button
    keyboard.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"dl:c:0:{task_id}")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    caption_text = (
        f"🎬 *{info['title']}*\n\n"
        f"⏱ *Duration:* `{info['duration_str']}`\n"
        f"👤 *Author:* `{info['uploader']}`\n"
        f"🌐 *Platform:* `{info['extractor']}`\n\n"
        "👇 *Select your preferred quality or extract audio:*"
    )

    try:
        await status_msg.edit_text(
            caption_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Failed to edit with markdown: {e}")
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

    action_type = parts[1]  # 'v' (video), 'a' (audio), 'c' (cancel)
    quality = parts[2]
    task_id = parts[3]

    if action_type == "c":
        MEDIA_CACHE.pop(task_id, None)
        await query.edit_message_text("❌ *Download cancelled.*", parse_mode=ParseMode.MARKDOWN)
        return

    media_data = MEDIA_CACHE.get(task_id)
    if not media_data:
        await query.edit_message_text(
            "⚠️ *Session expired or link timed out.* Please send the link again.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    url = media_data["url"]
    title = media_data["title"]
    is_audio = (action_type == "a")

    status_label = "Audio (MP3)" if is_audio else f"{quality}p Video"
    await query.edit_message_text(
        f"⏳ *Downloading {status_label}...*\n\n_{title}_\n\nPlease wait a moment.",
        parse_mode=ParseMode.MARKDOWN
    )

    download_result = None
    file_path = None

    try:
        if is_audio:
            download_result = await downloader.download_media(url, media_type="audio")
        else:
            download_result = await downloader.download_media(url, media_type="video", quality=quality)

        file_path = download_result["file_path"]
        file_size_mb = download_result["file_size_mb"]

        # 50MB Telegram Bot API limit check
        if file_size_mb > MAX_FILE_SIZE_MB:
            await query.edit_message_text(
                f"⚠️ *File is too large for Telegram ({file_size_mb:.1f} MB > 50 MB limit)!*\n\n"
                "💡 *Solution:*\n"
                "• Send the link again and choose a lower resolution (e.g. 480p or 360p)\n"
                "• Or choose *🎵 Download Audio (MP3)*",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Uploading state
        await query.edit_message_text(
            f"📤 *Uploading {status_label} ({file_size_mb:.1f} MB)...*",
            parse_mode=ParseMode.MARKDOWN
        )

        chat_id = query.message.chat_id

        if is_audio:
            with open(file_path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    title=download_result["title"],
                    performer=download_result["uploader"],
                    duration=download_result["duration"],
                    caption=(
                        f"🎵 *{download_result['title']}*\n\n"
                        f"👤 Artist: `{download_result['uploader']}`\n"
                        f"⚡ Downloaded via: @String_rebot\n"
                        f"👑 Owner: @{OWNER_USERNAME}"
                    ),
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "audio")
        else:
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=(
                        f"🎬 *{download_result['title']}*\n\n"
                        f"✨ Quality: `{quality}p` ({file_size_mb:.1f} MB)\n"
                        f"⚡ Downloaded via: @String_rebot\n"
                        f"👑 Owner: @{OWNER_USERNAME}"
                    ),
                    duration=download_result["duration"],
                    supports_streaming=True,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.MARKDOWN
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "video")

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Download/Upload error: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ *Failed to complete download.*\n\n`{str(e)[:150]}`\n\n💬 Report to: @{OWNER_USERNAME}",
            parse_mode=ParseMode.MARKDOWN
        )
    finally:
        if file_path:
            downloader.cleanup_files(file_path)
        MEDIA_CACHE.pop(task_id, None)


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
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))

    # Callbacks and links
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_message))

    # Global error handler
    application.add_error_handler(error_handler)

    logger.info(f"Starting String Gen Bot (@String_rebot) with polling (Owner: @{OWNER_USERNAME})...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
