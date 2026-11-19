import os
import time
import asyncio
import logging
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
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

from config import BOT_TOKEN, MAX_FILE_SIZE_MB, logger
from downloader import MediaDownloader, extract_urls, format_bytes, format_duration
from server import start_web_server

# Cache to store media extraction data (maps uuid -> media_info)
# Keeps callback data small (< 64 bytes) to comply with Telegram API
MEDIA_CACHE: Dict[str, Dict[str, Any]] = {}
CACHE_EXPIRY_SECONDS = 3600  # 1 hour

downloader = MediaDownloader()


def prune_cache():
    """Prunes expired entries from the in-memory cache."""
    now = time.time()
    expired_keys = [k for k, v in MEDIA_CACHE.items() if now - v.get("timestamp", 0) > CACHE_EXPIRY_SECONDS]
    for k in expired_keys:
        MEDIA_CACHE.pop(k, None)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    user_first_name = update.effective_user.first_name if update.effective_user else "there"
    welcome_text = (
        f"👋 *Hello {user_first_name}!*\n\n"
        "Welcome to the *Universal Media & Audio Downloader Bot*! 🚀\n\n"
        "I can download media from almost any platform in your chosen quality:\n"
        "• 📸 *Instagram* (Reels, Stories, Posts, Audio)\n"
        "• 🎥 *YouTube* (Videos, Shorts, 1080p, 720p, 480p, MP3)\n"
        "• 📌 *Pinterest* (Videos, Photos, Pins)\n"
        "• 📘 *Facebook* (Reels, Videos, Stories)\n"
        "• 🐦 *Twitter / X* (Videos, Clips)\n"
        "• 🤖 *Reddit* (Videos with sound, Gifs)\n"
        "• 👻 *Snapchat* (Spotlight, Stories)\n"
        "• 🎵 *TikTok, Vimeo, Soundcloud & more!*\n\n"
        "✨ *Features:*\n"
        "🔘 *Interactive Quality Selection Buttons* (1080p, 720p, 480p, 360p)\n"
        "🎵 *Extract Audio / Song* directly from any video as an MP3!\n\n"
        "👉 *Just paste any link below to get started!*"
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    help_text = (
        "📖 *How to use this bot:*\n\n"
        "1. Copy any video, reel, post, or short link from your favorite app.\n"
        "2. Send or paste the link here in this chat.\n"
        "3. Wait 2-3 seconds while the bot fetches media details.\n"
        "4. Choose your preferred video resolution or tap *🎵 Audio (MP3)*.\n"
        "5. The bot will download and deliver your media immediately!\n\n"
        "⚙️ *Bot Limitations:*\n"
        "• Maximum file size via standard Telegram API is *50 MB*.\n"
        "• Private accounts require authorization and may not be accessible.\n\n"
        "Commands:\n"
        "/start - Restart or show welcome message\n"
        "/help - How to use\n"
        "/ping - Check bot response speed"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ping command to test bot responsiveness."""
    start_t = time.time()
    msg = await update.message.reply_text("🏓 *Pinging...*", parse_mode=ParseMode.MARKDOWN)
    latency = int((time.time() - start_t) * 1000)
    await msg.edit_text(f"🏓 *Pong!* Latency: `{latency}ms`\nStatus: 🟢 24/7 Live", parse_mode=ParseMode.MARKDOWN)


async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detects links in messages, fetches metadata, and presents interactive quality buttons."""
    message = update.effective_message
    if not message or not message.text:
        return

    urls = extract_urls(message.text)
    if not urls:
        return

    target_url = urls[0]
    prune_cache()

    status_msg = await message.reply_text(
        "🔍 *Analyzing your link... please wait.*",
        parse_mode=ParseMode.MARKDOWN,
        reply_to_message_id=message.message_id
    )

    try:
        # Fetch metadata using yt-dlp
        info = await downloader.fetch_media_info(target_url)
    except Exception as e:
        logger.error(f"Failed to fetch media info for {target_url}: {e}")
        error_msg = str(e)
        if "Private video" in error_msg or "login" in error_msg.lower():
            friendly_err = "🔒 This content is from a private account or requires login."
        else:
            friendly_err = "⚠️ Could not extract media from this link. Please check if the link is valid and public."

        await status_msg.edit_text(
            f"❌ *Extraction Failed*\n\n{friendly_err}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Handle direct photo posts (e.g. Pinterest image, Instagram photo)
    if info.get("is_image") and info.get("image_url"):
        await status_msg.edit_text("⏳ *Downloading photo...*", parse_mode=ParseMode.MARKDOWN)
        local_img = None
        try:
            local_img = await downloader.download_direct_image(info["image_url"])
            with open(local_img, "rb") as f:
                await message.reply_photo(
                    photo=f,
                    caption=f"📷 *{info['title']}*\n👤 Platform: `{info['extractor']}`",
                    parse_mode=ParseMode.MARKDOWN
                )
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

    # Build Interactive Inline Keyboard
    keyboard = []
    qualities = info.get("qualities", ["best"])

    # Video quality buttons
    quality_buttons = []
    for q in qualities:
        label = f"🎬 {q}p" if isinstance(q, int) else "🎬 Best Video"
        callback_data = f"dl:v:{q}:{task_id}"
        quality_buttons.append(InlineKeyboardButton(label, callback_data=callback_data))

    # Split video buttons into rows of 2
    for i in range(0, len(quality_buttons), 2):
        keyboard.append(quality_buttons[i:i+2])

    # Audio extraction button (Always available)
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
        "👇 *Choose download format or audio:*"
    )

    try:
        await status_msg.edit_text(
            caption_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.warning(f"Failed to edit with markdown, falling back to plain text: {e}")
        await status_msg.edit_text(
            f"Media: {info['title']}\nDuration: {info['duration_str']}\n\nChoose an option:",
            reply_markup=reply_markup
        )


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes button clicks for quality selection, audio extraction, or cancellation."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "dl":
        return

    action_type = parts[1]  # 'v' (video), 'a' (audio), 'c' (cancel)
    quality = parts[2]      # '1080', '720', 'mp3', etc.
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

    # Update UI to Downloading
    status_label = "Audio (MP3)" if is_audio else f"{quality}p"
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

        # Check Telegram Bot API 50 MB limit
        if file_size_mb > MAX_FILE_SIZE_MB:
            await query.edit_message_text(
                f"⚠️ *File is too large for Telegram ({file_size_mb:.1f} MB)!*\n\n"
                f"The standard Telegram Bot API has a strict *50 MB* limit.\n\n"
                "💡 *Suggestions:*\n"
                "• Send the link again and choose a lower resolution (e.g. 480p or 360p)\n"
                "• Or choose *🎵 Download Audio (MP3)*",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Update UI to Uploading
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
                    caption=f"🎵 *{download_result['title']}*\n👤 `{download_result['uploader']}`",
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            with open(file_path, "rb") as f:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=f"🎬 *{download_result['title']}*\n✨ Quality: `{quality}p` ({file_size_mb:.1f} MB)",
                    duration=download_result["duration"],
                    supports_streaming=True,
                    parse_mode=ParseMode.MARKDOWN
                )

        # Remove the status message on success
        try:
            await query.message.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Download/Upload error: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ *Failed to process download.*\n\n`{str(e)[:150]}`",
            parse_mode=ParseMode.MARKDOWN
        )
    finally:
        # Guarantee disk cleanup
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
    # Launch keepalive HTTP server for Koyeb / Render
    await start_web_server()
    logger.info("Bot and Web Server are now 100% active and running!")


def main():
    """Main application entry point."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is not configured! Please provide your Telegram Bot Token in .env file.")
        print("ERROR: BOT_TOKEN is missing! Set BOT_TOKEN in .env or environment variables.")
        return

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Register command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))

    # Register callback query handler for buttons
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Register URL/message listener
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_incoming_message))

    # Global error handler
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram Bot with polling...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
