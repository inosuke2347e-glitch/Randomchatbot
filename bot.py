# bot.py
"""
Anonymous Chat + Media Forwarder Bot
python-telegram-bot v20.3 compatible
Safe config via environment variables only.
"""

import os
import json
import time
import logging
from typing import Dict, List, Optional

import nest_asyncio
nest_asyncio.apply()

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -----------------------------------------------------------------------------
# SAFE CONFIG (no secrets in repo)
# - Provide these as environment variables in Railway/Render/Colab/etc:
#   BOT_TOKEN:   your bot token
#   GROUP_ID:    target group id for forwarded media (e.g. -1001234567890)
#   ADMIN_IDS:   comma separated admin IDs (e.g. 12345,67890)
# -----------------------------------------------------------------------------

def _parse_admins(env: Optional[str]) -> List[int]:
    if not env:
        return []
    parts = [p.strip() for p in env.split(",") if p.strip()]
    out = []
    for p in parts:
        try:
            out.append(int(p))
        except:
            # ignore invalid entries
            continue
    return out

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GROUP_ID_RAW = os.getenv("GROUP_ID", "").strip()
ADMIN_IDS = _parse_admins(os.getenv("ADMIN_IDS", ""))

# Validate
if not BOT_TOKEN:
    logging.warning("BOT_TOKEN not set. The bot will not start until BOT_TOKEN is provided as env var.")

try:
    GROUP_ID = int(GROUP_ID_RAW) if GROUP_ID_RAW else None
except:
    GROUP_ID = None
    logging.warning("GROUP_ID invalid or not set. Group forwarding will be disabled until configured.")

# Runtime constants
STATE_FILE = "anon_state.json"
RATE_LIMIT = 1.3  # seconds per-user minimum

# Logging
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# -----------------------------------------------------------------------------
# In-memory state (persisted)
# -----------------------------------------------------------------------------
queue: List[int] = []
sessions: Dict[int, int] = {}
last_time: Dict[int, float] = {}

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"queue": queue, "sessions": sessions}, f)
    except Exception as e:
        logging.exception("Failed to save state: %s", e)

def load_state():
    global queue, sessions
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            queue = data.get("queue", []) or []
            raw_sessions = data.get("sessions", {}) or {}
            sessions = {int(k): int(v) for k, v in raw_sessions.items()}
            logging.info("Loaded state: %d queued, %d sessions", len(queue), len(sessions)//2)
    except Exception as e:
        logging.exception("Failed to load state: %s", e)
        queue = []
        sessions = {}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

async def notify_admins(app_ctx, text: str):
    # send lightly to admins; ignore failures
    try:
        if not ADMIN_IDS:
            return
        for adm in ADMIN_IDS:
            try:
                await app_ctx.bot.send_message(adm, f"[ADMIN] {text}")
            except Exception:
                pass
    except Exception:
        pass

def rate_limited(uid: int) -> bool:
    now = time.time()
    last = last_time.get(uid)
    if last and (now - last) < RATE_LIMIT:
        return True
    last_time[uid] = now
    return False

def pair(a: int, b: int):
    sessions[a] = b
    sessions[b] = a
    save_state()

def unpair(uid: int) -> Optional[int]:
    other = sessions.pop(uid, None)
    if other:
        sessions.pop(other, None)
    save_state()
    return other

def find_partner(uid: int) -> Optional[int]:
    # if already paired return partner
    if uid in sessions:
        return sessions[uid]
    # remove from queue if present
    if uid in queue:
        try:
            queue.remove(uid)
        except:
            pass
    # if queue not empty, pair
    if queue:
        other = queue.pop(0)
        pair(uid, other)
        return other
    # else enqueue
    queue.append(uid)
    save_state()
    return None

async def send_menu(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    # Always show the same short menu after start/next/stop or connection events
    try:
        txt = (
            "Anonymous Bot Activated\n\n"
            "Commands:\n"
            "/anon_start - Find a partner\n"
            "/anon_next - Next partner\n"
            "/anon_stop - Stop chatting\n"
            "/status - Show status\n"
        )
        await context.bot.send_message(chat_id, txt)
    except Exception:
        pass

# -----------------------------------------------------------------------------
# Command handlers
# -----------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show menu to user (explicit start)
    user_id = update.effective_user.id
    await send_menu(context, user_id)

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))

async def cmd_show_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    cfg = {
        "BOT_TOKEN": "***" if BOT_TOKEN else "(not set)",
        "GROUP_ID": GROUP_ID,
        "ADMIN_IDS": ADMIN_IDS,
        "STATE_FILE": STATE_FILE
    }
    await update.message.reply_text(json.dumps(cfg, indent=2))

async def cmd_clear_state(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Unauthorized.")
        return
    global queue, sessions
    queue = []
    sessions = {}
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except:
        pass
    await update.message.reply_text("State cleared.")
    await send_menu(context, update.effective_user.id)

# Anonymous chat commands
async def cmd_anon_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    partner = find_partner(uid)
    if partner:
        # we just paired
        try:
            await context.bot.send_message(uid, "🎯 Partner connected.")
            await context.bot.send_message(partner, "🎯 Partner connected.")
        except:
            pass
        # always show menu after connect
        await send_menu(context, uid)
        await send_menu(context, partner)
    else:
        try:
            await context.bot.send_message(uid, "⌛ Searching for partner...")
        except:
            pass
        await send_menu(context, uid)

async def cmd_anon_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    old = unpair(uid)
    if old:
        try:
            await context.bot.send_message(old, "⚠ Partner disconnected.")
            await send_menu(context, old)
        except:
            pass
    partner = find_partner(uid)
    if partner:
        try:
            await context.bot.send_message(uid, "🎯 New partner connected.")
            await context.bot.send_message(partner, "🎯 New partner connected.")
        except:
            pass
        await send_menu(context, uid)
        await send_menu(context, partner)
    else:
        try:
            await context.bot.send_message(uid, "⌛ Searching for partner...")
            await send_menu(context, uid)
        except:
            pass

async def cmd_anon_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    partner = unpair(uid)
    if partner:
        try:
            await context.bot.send_message(partner, "⚠ Partner disconnected.")
            await send_menu(context, partner)
        except:
            pass
    # remove from queue if present
    if uid in queue:
        try:
            queue.remove(uid)
            save_state()
        except:
            pass
    try:
        await context.bot.send_message(uid, "❌ You left the chat.")
        await send_menu(context, uid)
    except:
        pass

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in sessions:
        await update.message.reply_text("✔ Connected")
    elif uid in queue:
        await update.message.reply_text("⌛ Waiting")
    else:
        await update.message.reply_text("❌ Not in chat")
    await send_menu(context, uid)

# -----------------------------------------------------------------------------
# Unified message handler (non-commands)
# -----------------------------------------------------------------------------
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    This handler receives everything except commands (see filters when adding).
    It will:
      1) forward attachment messages to GROUP_ID (if configured)
      2) relay message to partner via copy_message to preserve anonymity
      3) prompt user to /anon_start if not in a session
    """
    if update.effective_message is None:
        return

    msg = update.effective_message
    uid = update.effective_user.id

    # safety: ignore any commands (should already be excluded)
    if msg.text and msg.text.startswith("/"):
        return

    # 1) forward attachments to group (if configured)
    has_attachment = (
        bool(msg.photo) or bool(msg.video) or bool(msg.audio) or
        bool(msg.voice) or bool(msg.document) or bool(msg.sticker) or
        bool(msg.animation) or bool(msg.video_note)
    )
    if GROUP_ID and has_attachment:
        try:
            await context.bot.forward_message(chat_id=GROUP_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
        except Exception as e:
            logging.exception("Group forward failed: %s", e)
            await notify_admins(context, f"Group forward failed: {e}")

    # 2) relay to partner if exists (with rate-limiting)
    if uid in sessions:
        if rate_limited(uid):
            # optionally inform user quietly
            try:
                await context.bot.send_chat_action(chat_id=uid, action="typing")
            except:
                pass
            return
        partner = sessions.get(uid)
        if partner:
            try:
                # copy_message preserves anonymity (sender becomes bot)
                await context.bot.copy_message(chat_id=partner, from_chat_id=msg.chat_id, message_id=msg.message_id)
            except Exception as e:
                logging.exception("Relay failed: %s", e)
                await notify_admins(context, f"Relay failed: {e}")
        return

    # 3) if not in session, inform user and show menu
    try:
        await context.bot.send_message(uid, "❌ You are not connected to a partner. Use /anon_start")
        await send_menu(context, uid)
    except:
        pass

# -----------------------------------------------------------------------------
# Build and run
# -----------------------------------------------------------------------------
def build_app():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN not provided. Set env variable BOT_TOKEN before starting the bot.")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register command handlers first
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("show_config", cmd_show_config))
    app.add_handler(CommandHandler("clear_state", cmd_clear_state))
    app.add_handler(CommandHandler("anon_start", cmd_anon_start))
    app.add_handler(CommandHandler("anon_next", cmd_anon_next))
    app.add_handler(CommandHandler("anon_stop", cmd_anon_stop))
    app.add_handler(CommandHandler("status", cmd_status))

    # Register unified message handler (non-commands).
    # Must exclude commands so command handlers run first and not blocked.
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages),
    )

    return app

# -----------------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    load_state()
    try:
        app = build_app()
    except Exception as e:
        logging.exception("Failed to build application: %s", e)
        raise

    logging.info("Bot starting... (press Ctrl+C to stop)")
    # run_polling is safe in Colab when nest_asyncio applied
    app.run_polling()
