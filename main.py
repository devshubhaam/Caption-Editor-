"""
╔══════════════════════════════════════════════════════════╗
║         BULK MEDIA CAPTION EDITOR BOT                   ║
║         Built with python-telegram-bot v20+             ║
║         Supports: Photo, Video, Document                ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import asyncio
import logging
from datetime import datetime
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, RetryAfter, TimedOut

# ─────────────────────────────────────────────
# 🔧 LOGGING SETUP
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# ⚙️ ENVIRONMENT CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_IDS = list(
    map(int, os.environ.get("ADMIN_IDS", "123456789").split(","))
)  # Comma-separated admin user IDs

# ─────────────────────────────────────────────
# 💾 IN-MEMORY STORAGE (MongoDB fallback)
# ─────────────────────────────────────────────
# Structure:
#   caption_store[user_id] = {
#       "caption": str,
#       "mode": "append" | "replace"
#   }
caption_store: dict[int, dict] = {}

# Stats tracker per user
stats_store: dict[int, dict] = {}

# ─────────────────────────────────────────────
# 🔌 OPTIONAL: MONGODB INTEGRATION
# Uncomment and install pymongo if you want DB persistence
# pip install pymongo
# ─────────────────────────────────────────────
# from pymongo import MongoClient
# MONGO_URI = os.environ.get("MONGO_URI", "")
# db = MongoClient(MONGO_URI)["caption_bot"] if MONGO_URI else None

# def db_get_caption(user_id: int) -> dict | None:
#     if db:
#         return db.captions.find_one({"user_id": user_id})
#     return caption_store.get(user_id)

# def db_set_caption(user_id: int, caption: str, mode: str):
#     data = {"user_id": user_id, "caption": caption, "mode": mode}
#     if db:
#         db.captions.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
#     else:
#         caption_store[user_id] = {"caption": caption, "mode": mode}

# ─────────────────────────────────────────────
# 🛡️ HELPER: Admin Check
# ─────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ─────────────────────────────────────────────
# 📦 HELPER: Get/Set Caption from Store
# ─────────────────────────────────────────────
def get_caption_data(user_id: int) -> dict:
    """Returns caption config for a user. Admins share a global config."""
    # Admins manage a "global" caption (stored under user_id 0)
    # Non-admins use their own caption
    key = 0 if is_admin(user_id) else user_id
    return caption_store.get(key, {"caption": None, "mode": "append"})


def set_caption_data(user_id: int, caption: str, mode: str = None):
    key = 0 if is_admin(user_id) else user_id
    existing = caption_store.get(key, {"caption": None, "mode": "append"})
    caption_store[key] = {
        "caption": caption,
        "mode": mode if mode else existing["mode"],
    }


def reset_caption_data(user_id: int):
    key = 0 if is_admin(user_id) else user_id
    caption_store[key] = {"caption": None, "mode": "append"}


def set_mode_data(user_id: int, mode: str):
    key = 0 if is_admin(user_id) else user_id
    existing = caption_store.get(key, {"caption": None, "mode": "append"})
    caption_store[key] = {"caption": existing.get("caption"), "mode": mode}


# ─────────────────────────────────────────────
# 📊 HELPER: Stats
# ─────────────────────────────────────────────
def init_stats(user_id: int):
    if user_id not in stats_store:
        stats_store[user_id] = {"processed": 0, "failed": 0, "session_start": datetime.now()}


def increment_stat(user_id: int, key: str):
    init_stats(user_id)
    stats_store[user_id][key] = stats_store[user_id].get(key, 0) + 1


# ─────────────────────────────────────────────
# 🎨 HELPER: Build Final Caption
# ─────────────────────────────────────────────
def build_caption(original_caption: str | None, custom_caption: str, mode: str) -> str:
    """
    mode = "append"  → old_caption + \n\n + custom_caption
    mode = "replace" → only custom_caption
    """
    if mode == "replace":
        return custom_caption

    # Append mode
    if original_caption and original_caption.strip():
        return f"{original_caption.strip()}\n\n{custom_caption}"
    return custom_caption


# ─────────────────────────────────────────────
# 🔁 HELPER: Safe Send with Retry
# ─────────────────────────────────────────────
async def safe_send(coro, retries: int = 3) -> bool:
    """Wraps a send coroutine with retry logic for flood limits."""
    for attempt in range(retries):
        try:
            await coro
            return True
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"Rate limited. Waiting {wait}s...")
            await asyncio.sleep(wait)
        except TimedOut:
            logger.warning(f"Timed out. Retry {attempt + 1}/{retries}")
            await asyncio.sleep(2)
        except TelegramError as e:
            logger.error(f"TelegramError: {e}")
            return False
    return False


# ─────────────────────────────────────────────
# 🚀 COMMAND: /start
# ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    admin_badge = "👑 Admin" if is_admin(user.id) else "👤 User"

    text = (
        f"👋 <b>Welcome to Bulk Caption Editor Bot!</b>\n\n"
        f"<b>Your Role:</b> {admin_badge}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>How to use:</b>\n\n"
        f"1️⃣ Set your caption with <code>/setcaption your text here</code>\n"
        f"2️⃣ Forward or send media files (photos/videos/documents)\n"
        f"3️⃣ Bot will send them back with your caption added!\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Commands:</b>\n"
        f"/setcaption — Set custom caption\n"
        f"/viewcaption — View current caption\n"
        f"/resetcaption — Remove caption\n"
        f"/mode — Switch append/replace mode\n"
        f"/stats — View processing stats\n"
        f"/help — Show this message\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Caption Modes:</b>\n"
        f"📎 <b>Append:</b> Old caption + your caption\n"
        f"✏️ <b>Replace:</b> Only your caption\n\n"
        f"Send up to <b>50 files</b> at once! 🚀"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ─────────────────────────────────────────────
# ✍️ COMMAND: /setcaption
# ─────────────────────────────────────────────
async def cmd_setcaption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Only admins OR each user can set their own caption
    # (Change to `if not is_admin(user_id):` to restrict to admins only)
    caption_text = " ".join(context.args).strip() if context.args else ""

    if not caption_text:
        await update.message.reply_text(
            "❌ <b>Usage:</b> <code>/setcaption Your caption text here</code>\n\n"
            "You can use multiple lines too!\n"
            "Example: <code>/setcaption 🔥 Join @yourchannel for more!</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    set_caption_data(user_id, caption_text)
    data = get_caption_data(user_id)

    await update.message.reply_text(
        f"✅ <b>Caption saved!</b>\n\n"
        f"<b>Caption:</b>\n<code>{caption_text}</code>\n\n"
        f"<b>Mode:</b> {'📎 Append' if data['mode'] == 'append' else '✏️ Replace'}\n\n"
        f"Now forward/send your media files! 📁",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 👁️ COMMAND: /viewcaption
# ─────────────────────────────────────────────
async def cmd_viewcaption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_caption_data(user_id)

    if not data["caption"]:
        await update.message.reply_text(
            "⚠️ <b>No caption set yet.</b>\n\nUse <code>/setcaption your text</code> to set one.",
            parse_mode=ParseMode.HTML,
        )
        return

    mode_text = "📎 Append (adds to existing caption)" if data["mode"] == "append" else "✏️ Replace (replaces existing caption)"

    await update.message.reply_text(
        f"📋 <b>Current Caption:</b>\n\n"
        f"<code>{data['caption']}</code>\n\n"
        f"<b>Mode:</b> {mode_text}",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 🗑️ COMMAND: /resetcaption
# ─────────────────────────────────────────────
async def cmd_resetcaption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_caption_data(user_id)
    await update.message.reply_text(
        "🗑️ <b>Caption reset!</b>\n\nNo custom caption is active.",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 🔄 COMMAND: /mode
# ─────────────────────────────────────────────
async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = get_caption_data(user_id)
    current_mode = data.get("mode", "append")

    # Toggle mode
    new_mode = "replace" if current_mode == "append" else "append"
    set_mode_data(user_id, new_mode)

    mode_desc = {
        "append": "📎 <b>Append Mode</b>\nYour caption will be added AFTER the original caption.",
        "replace": "✏️ <b>Replace Mode</b>\nYour caption will REPLACE the original caption.",
    }

    await update.message.reply_text(
        f"✅ Mode switched to:\n\n{mode_desc[new_mode]}\n\n"
        f"Use /mode again to switch back.",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 📊 COMMAND: /stats
# ─────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    init_stats(user_id)
    s = stats_store[user_id]

    session_time = datetime.now() - s["session_start"]
    minutes = int(session_time.total_seconds() // 60)

    await update.message.reply_text(
        f"📊 <b>Your Stats</b>\n\n"
        f"✅ Processed: <b>{s.get('processed', 0)}</b> files\n"
        f"❌ Failed: <b>{s.get('failed', 0)}</b> files\n"
        f"⏱️ Session: <b>{minutes} min</b>",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 📁 CORE: Media Handler
# ─────────────────────────────────────────────
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main handler for all incoming media messages.
    Detects type → builds caption → resends with new caption.
    """
    message: Message = update.message
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Get caption config
    data = get_caption_data(user_id)
    custom_caption = data.get("caption")

    if not custom_caption:
        await message.reply_text(
            "⚠️ <b>No caption set!</b>\n\nUse <code>/setcaption your text</code> first.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Detect media type ──
    media_type = None
    file_id = None
    original_caption = message.caption or ""

    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id  # Highest resolution

    elif message.video:
        media_type = "video"
        file_id = message.video.file_id

    elif message.document:
        media_type = "document"
        file_id = message.document.file_id

    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id

    elif message.audio:
        media_type = "audio"
        file_id = message.audio.file_id

    elif message.voice:
        media_type = "voice"
        file_id = message.voice.file_id

    else:
        # Silently ignore non-media (or send tip)
        await message.reply_text(
            "📭 <b>Unsupported file type.</b>\nPlease send: Photo, Video, Document, GIF, Audio.",
            parse_mode=ParseMode.HTML,
        )
        return

    # ── Build final caption ──
    final_caption = build_caption(original_caption, custom_caption, data["mode"])

    # Telegram caption limit is 1024 chars
    if len(final_caption) > 1024:
        final_caption = final_caption[:1021] + "..."
        logger.warning(f"Caption truncated for user {user_id}")

    # ── Send back the media ──
    logger.info(f"Processing {media_type} from user {user_id}")
    success = False

    try:
        if media_type == "photo":
            success = await safe_send(
                context.bot.send_photo(
                    chat_id=chat_id,
                    photo=file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif media_type == "video":
            success = await safe_send(
                context.bot.send_video(
                    chat_id=chat_id,
                    video=file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif media_type == "document":
            success = await safe_send(
                context.bot.send_document(
                    chat_id=chat_id,
                    document=file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif media_type == "animation":
            success = await safe_send(
                context.bot.send_animation(
                    chat_id=chat_id,
                    animation=file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif media_type == "audio":
            success = await safe_send(
                context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_id,
                    caption=final_caption,
                    parse_mode=ParseMode.HTML,
                )
            )
        elif media_type == "voice":
            success = await safe_send(
                context.bot.send_voice(
                    chat_id=chat_id,
                    voice=file_id,
                    caption=final_caption,
                )
            )

    except Exception as e:
        logger.error(f"Unexpected error for {media_type}: {e}")
        await message.reply_text(f"❌ Error processing file: <code>{e}</code>", parse_mode=ParseMode.HTML)
        increment_stat(user_id, "failed")
        return

    if success:
        increment_stat(user_id, "processed")
        logger.info(f"✅ Sent {media_type} to {user_id}")
    else:
        increment_stat(user_id, "failed")
        await message.reply_text("❌ Failed to send this file. Please try again.", parse_mode=ParseMode.HTML)

    # ── Delay to avoid flood limits on bulk sends ──
    await asyncio.sleep(0.4)


# ─────────────────────────────────────────────
# 🏓 COMMAND: /help (alias for /start)
# ─────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ─────────────────────────────────────────────
# ❓ Handler: Unknown Commands
# ─────────────────────────────────────────────
async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Unknown command. Use /help to see all commands.",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 🏗️ BOT SETUP & RUN
# ─────────────────────────────────────────────
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise ValueError("❌ BOT_TOKEN not set! Check your environment variables.")

    logger.info("🚀 Starting Bulk Caption Editor Bot...")
    logger.info(f"👑 Admin IDs: {ADMIN_IDS}")

    # Build application
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Register Handlers ──

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setcaption", cmd_setcaption))
    app.add_handler(CommandHandler("viewcaption", cmd_viewcaption))
    app.add_handler(CommandHandler("resetcaption", cmd_resetcaption))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Media messages (all types)
    media_filter = (
        filters.PHOTO
        | filters.VIDEO
        | filters.Document.ALL
        | filters.ANIMATION
        | filters.AUDIO
        | filters.VOICE
    )
    app.add_handler(MessageHandler(media_filter, handle_media))

    # Unknown commands
    app.add_handler(MessageHandler(filters.COMMAND, handle_unknown))

    logger.info("✅ All handlers registered. Bot is polling...")

    # Start polling
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
