import os
import time
import html
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
    """Handler for /start command with HTML UI."""
    user = update.effective_user
    if not user:
        return

    db.add_or_update_user(user.id, user.username, user.first_name)

    if db.is_user_banned(user.id):
        await update.message.reply_text("🚫 <b>You are banned from using this bot.</b>", parse_mode=ParseMode.HTML)
        return

    name = html.escape(user.first_name or "Friend")
    welcome_text = (
        f"⚡ <b>Welcome {name}!</b> ⚡\n\n"
        "I am <b>String Gen Media Bot</b> — the world's most advanced, ultra-fast 24/7 downloader! 🚀\n\n"
        "✨ <b>Supported Platforms:</b>\n"
        "• 📸 <b>Instagram</b> — Reels, Posts, Stories, Audio\n"
        "• 🎥 <b>YouTube</b> — Videos, Shorts (1080p, 720p, 480p, MP3)\n"
        "• 📌 <b>Pinterest</b> — HD Video Pins & Original Photos\n"
        "• 📘 <b>Facebook</b> — Reels, Watch Videos & Stories\n"
        "• 🐦 <b>Twitter / X</b> — HD Videos & Clips\n"
        "• 🤖 <b>Reddit</b> — Videos with audio merged\n"
        "• 👻 <b>Snapchat</b> — Spotlight & Stories\n"
        "• 🎵 <b>TikTok, SoundCloud, Vimeo & 1800+ more!</b>\n\n"
        f"👑 <b>Owner:</b> @{html.escape(OWNER_USERNAME)}\n"
        f"📢 <b>Updates:</b> <a href=\"{OWNER_CHANNEL_LINK}\">Private Think</a>\n"
        f"💬 <b>Support:</b> <a href=\"{OWNER_GROUP_LINK}\">Amrit Feel</a>\n\n"
        "👉 <i>Just paste any video or post link below to download instantly!</i>"
    )
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    help_text = (
        "📖 <b>How to use String Gen Media Bot:</b>\n\n"
        "1️⃣ Copy any video, reel, post, or short link from any app.\n"
        "2️⃣ Paste and send the link in this chat.\n"
        "3️⃣ Wait 1-2 seconds for instant media analysis.\n"
        "4️⃣ Choose your preferred resolution (1080p, 720p, etc.) or tap <b>🎵 Audio (MP3)</b>.\n"
        "5️⃣ Enjoy instant high-speed delivery!\n\n"
        "📌 <b>Commands:</b>\n"
        "• /start — Start bot & view main menu\n"
        "• /help — How to use\n"
        "• /ping — Server speed & 24/7 status\n"
        "• /stats — Live download statistics"
    )
    await update.message.reply_text(
        help_text,
        reply_markup=get_social_keyboard(),
        parse_mode=ParseMode.HTML
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ping command to test bot responsiveness."""
    start_t = time.time()
    msg = await update.message.reply_text("⚡ <i>Pinging servers...</i>", parse_mode=ParseMode.HTML)
    latency = int((time.time() - start_t) * 1000)
    await msg.edit_text(
        f"🚀 <b>Pong!</b> Latency: <code>{latency}ms</code>\n"
        "🟢 <b>Status:</b> 24/7 Live & Operational\n"
        f"👑 <b>Owner:</b> @{html.escape(OWNER_USERNAME)}",
        parse_mode=ParseMode.HTML
    )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays bot usage statistics."""
    stats = db.get_stats()
    text = (
        "📊 <b>String Gen Bot Live Analytics:</b>\n\n"
        f"👥 <b>Total Registered Users:</b> <code>{stats['total_users']}</code>\n"
        f"📥 <b>Total Media Downloads:</b> <code>{stats['total_downloads']}</code>\n"
        f"⚡ <b>Downloads Today:</b> <code>{stats['today_downloads']}</code>\n"
        f"🚫 <b>Banned Users:</b> <code>{stats['banned_users']}</code>\n\n"
        f"👑 <b>Developed by:</b> @{html.escape(OWNER_USERNAME)}"
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
    admin_text = (
        "👑 <b>Owner Admin Control Panel</b>\n\n"
        f"👤 <b>Owner:</b> @{html.escape(OWNER_USERNAME)}\n"
        f"👥 <b>Total Users:</b> <code>{stats['total_users']}</code>\n"
        f"📥 <b>Total Downloads:</b> <code>{stats['total_downloads']}</code>\n"
        f"📅 <b>Today's Tasks:</b> <code>{stats['today_downloads']}</code>\n\n"
        "⚙️ <b>Available Admin Commands:</b>\n"
        "• <code>/broadcast &lt;text&gt;</code> — Send broadcast announcement to all users\n"
        "• <code>/ban &lt;user_id&gt;</code> — Ban a user\n"
        "• <code>/unban &lt;user_id&gt;</code> — Unban a user\n"
        "• <code>/stats</code> — Live statistics"
    )
    await update.message.reply_text(admin_text, parse_mode=ParseMode.HTML)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast announcement to all bot users (Owner only)."""
    if not is_owner(update):
        return

    if not context.args:
        await update.message.reply_text("⚠️ Usage: <code>/broadcast &lt;your message here&gt;</code>", parse_mode=ParseMode.HTML)
        return

    broadcast_text = html.escape(" ".join(context.args))
    user_ids = db.get_all_user_ids()
    status_msg = await update.message.reply_text(f"🚀 <i>Broadcasting to {len(user_ids)} users...</i>", parse_mode=ParseMode.HTML)

    success_count = 0
    fail_count = 0

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>Announcement from @{html.escape(OWNER_USERNAME)}:</b>\n\n{broadcast_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_social_keyboard()
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"📨 Sent: <code>{success_count}</code>\n"
        f"❌ Failed / Blocked: <code>{fail_count}</code>",
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
    if not message or not message.text or not user:
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
            [InlineKeyboardButton("📢 Join Updates Channel", url=OWNER_CHANNEL_LINK)],
            [InlineKeyboardButton("💬 Join Support Group", url=OWNER_GROUP_LINK)],
        ])
        await message.reply_text(
            "⚠️ <b>Access Restricted!</b>\n\n"
            "Please join our official channel and group to use this bot for free high-speed downloads!\n\n"
            f"👉 <a href=\"{OWNER_CHANNEL_LINK}\">Join Channel</a> &amp; <a href=\"{OWNER_GROUP_LINK}\">Join Group</a>",
            reply_markup=join_keyboard,
            parse_mode=ParseMode.HTML
        )
        return

    target_url = urls[0]
    prune_cache()

    status_msg = await message.reply_text(
        "⚡ <i>Analyzing link at high speed... please wait.</i>",
        parse_mode=ParseMode.HTML,
        reply_to_message_id=message.message_id
    )

    try:
        info = await downloader.fetch_media_info(target_url)
    except Exception as e:
        logger.error(f"Failed to fetch media info for {target_url}: {e}")
        error_msg = str(e)
        if "Private video" in error_msg or "login" in error_msg.lower() or "forbidden" in error_msg.lower():
            friendly_err = "🔒 This content is from a private account or requires authorization."
        else:
            friendly_err = "⚠️ Could not extract media. Please make sure the link is public and valid."

        try:
            await status_msg.edit_text(
                f"❌ <b>Extraction Failed</b>\n\n{friendly_err}\n\n💬 Need help? Contact @{html.escape(OWNER_USERNAME)}",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        return

    # Handle direct photo posts
    if info.get("is_image") and info.get("image_url"):
        await status_msg.edit_text("⏳ <i>Downloading photo in original resolution...</i>", parse_mode=ParseMode.HTML)
        local_img = None
        try:
            local_img = await downloader.download_direct_image(info["image_url"])
            with open(local_img, "rb") as f:
                caption = (
                    f"📷 <b>{html.escape(info['title'])}</b>\n\n"
                    f"🌐 Platform: <code>{html.escape(info['extractor'])}</code>\n"
                    "⚡ Downloaded via: @String_rebot\n"
                    f"👑 Owner: @{html.escape(OWNER_USERNAME)}"
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
            await status_msg.edit_text("❌ <b>Failed to download photo.</b>", parse_mode=ParseMode.HTML)
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

    # Build Interactive Quality Selection Grid
    keyboard = []
    qualities = info.get("qualities", ["best"])

    quality_buttons = []
    for q in qualities:
        label = f"🎬 {q}p HD" if isinstance(q, int) else "🎬 Best Quality"
        callback_data = f"dl:v:{q}:{task_id}"
        quality_buttons.append(InlineKeyboardButton(label, callback_data=callback_data))

    for i in range(0, len(quality_buttons), 2):
        keyboard.append(quality_buttons[i:i+2])

    keyboard.append([
        InlineKeyboardButton("🎵 Download Audio / Song (MP3)", callback_data=f"dl:a:mp3:{task_id}")
    ])

    keyboard.append([
        InlineKeyboardButton("❌ Cancel", callback_data=f"dl:c:0:{task_id}")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    caption_text = (
        f"🎬 <b>{html.escape(info['title'])}</b>\n\n"
        f"⏱ <b>Duration:</b> <code>{info['duration_str']}</code>\n"
        f"👤 <b>Author:</b> <code>{html.escape(info['uploader'])}</code>\n"
        f"🌐 <b>Platform:</b> <code>{html.escape(info['extractor'])}</code>\n\n"
        "👇 <b>Select your download quality or extract song:</b>"
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
        await query.edit_message_text("❌ <b>Download cancelled.</b>", parse_mode=ParseMode.HTML)
        return

    media_data = MEDIA_CACHE.get(task_id)
    if not media_data:
        await query.edit_message_text(
            "⚠️ <b>Session expired.</b> Please send the link again.",
            parse_mode=ParseMode.HTML
        )
        return

    url = media_data["url"]
    direct_download_url = media_data.get("direct_download_url")
    title = media_data["title"]
    is_audio = (action_type == "a")

    status_label = "Audio (MP3)" if is_audio else f"{quality}p Video"
    await query.edit_message_text(
        f"⏳ <i>Downloading {status_label}...</i>\n\n<b>{html.escape(title)}</b>\n\n⚡ Ultra-high speed transfer in progress...",
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
                f"⚠️ <b>File is too large for Telegram ({file_size_mb:.1f} MB &gt; 50 MB limit)!</b>\n\n"
                "💡 <b>Solution:</b>\n"
                "• Send link again and choose 480p or 360p\n"
                "• Or choose <b>🎵 Download Audio (MP3)</b>",
                parse_mode=ParseMode.HTML
            )
            return

        await query.edit_message_text(
            f"📤 <i>Uploading {status_label} ({file_size_mb:.1f} MB)...</i>",
            parse_mode=ParseMode.HTML
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
                        f"🎵 <b>{html.escape(download_result['title'])}</b>\n\n"
                        f"👤 Artist: <code>{html.escape(download_result['uploader'])}</code>\n"
                        "⚡ Downloaded via: @String_rebot\n"
                        f"👑 Owner: @{html.escape(OWNER_USERNAME)}"
                    ),
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "audio")
        else:
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=(
                        f"🎬 <b>{html.escape(download_result['title'])}</b>\n\n"
                        f"✨ Quality: <code>{quality}p</code> ({file_size_mb:.1f} MB)\n"
                        "⚡ Downloaded via: @String_rebot\n"
                        f"👑 Owner: @{html.escape(OWNER_USERNAME)}"
                    ),
                    duration=download_result["duration"],
                    supports_streaming=True,
                    reply_markup=get_media_sent_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            db.increment_download(query.from_user.id, media_data.get("extractor", "Web"), "video")

        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Download/Upload error: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ <b>Download Error</b>\n\n<code>{html.escape(str(e)[:150])}</code>\n\n💬 Report to: @{html.escape(OWNER_USERNAME)}",
            parse_mode=ParseMode.HTML
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
