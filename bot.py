# ============================================================
# FINAL MASTER VERSION — OPTION A
# Anonymous Chat + Media Forwarder Bot (Full Working Code)
# python-telegram-bot v20.3
# Single Cell — No Patching Needed
# ============================================================

import nest_asyncio
nest_asyncio.apply()

import os
import json
import time
import logging
from typing import Dict, List

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIG — FALLBACK VALUES YOU GAVE
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "BOT_TOKEN"
GROUP_ID = int(os.getenv("GROUP_ID") or "GROUP_ID")
ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS") or "admi_id").split(",")]

STATE_FILE = "anon_state.json"
RATE_LIMIT = 1.3

logging.getLogger("telegram").setLevel(logging.WARNING)

# ============================================================
# STATE
# ============================================================
queue: List[int] = []
sessions: Dict[int, int] = {}
last_time: Dict[int, float] = {}

# ============================================================
# PERSISTENCE
# ============================================================
def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"queue": queue, "sessions": sessions}, f)
    except:
        pass

def load_state():
    global queue, sessions
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
            queue = data.get("queue", [])
            sessions = {int(k): int(v) for k, v in data.get("sessions", {}).items()}
    except:
        queue = []
        sessions = {}

# ============================================================
# HELPERS
# ============================================================
def is_admin(uid): return uid in ADMIN_IDS

async def notify_admins(app, text):
    for adm in ADMIN_IDS:
        try:
            await app.bot.send_message(adm, f"[ADMIN]\n{text}")
        except:
            pass

def rate_limited(uid):
    now = time.time()
    if uid in last_time and now - last_time[uid] < RATE_LIMIT:
        return True
    last_time[uid] = now
    return False

def pair(a,b):
    sessions[a] = b
    sessions[b] = a
    save_state()

def unpair(uid):
    p = sessions.pop(uid, None)
    if p:
        sessions.pop(p, None)
    save_state()
    return p

def find_partner(uid):
    if uid in sessions:
        return sessions[uid]
    if uid in queue:
        try: queue.remove(uid)
        except: pass
    if queue:
        other = queue.pop(0)
        pair(uid, other)
        return other
    queue.append(uid)
    save_state()
    return None

async def send_menu(ctx, chat_id):
    try:
        await ctx.bot.send_message(
            chat_id,
            "Anonymous Bot Activated\n"
            "/anon_start – Find partner\n"
            "/anon_next – Next partner\n"
            "/anon_stop – Stop chat\n"
            "/status – Chat status"
        )
    except:
        pass

# ============================================================
# COMMANDS
# ============================================================
async def start(update, ctx):
    await send_menu(ctx, update.effective_user.id)

async def myid(update, ctx):
    await update.message.reply_text(str(update.effective_user.id))

async def show_config(update, ctx):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("Unauthorized.")
    await update.message.reply_text(str({
        "BOT_TOKEN": "***",
        "GROUP_ID": GROUP_ID,
        "ADMIN_IDS": ADMIN_IDS
    }))

async def clear_state(update, ctx):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("Unauthorized.")

    global queue, sessions
    queue = []
    sessions = {}
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    await update.message.reply_text("State cleared.")
    await send_menu(ctx, update.effective_user.id)

# ============================================================
# ANONYMOUS CHAT COMMANDS
# ============================================================
async def anon_start(update, ctx):
    uid = update.effective_user.id
    p = find_partner(uid)

    if p:
        await ctx.bot.send_message(uid, "🎯 Partner connected.")
        await ctx.bot.send_message(p, "🎯 Partner connected.")
        await send_menu(ctx, uid)
        await send_menu(ctx, p)
    else:
        await ctx.bot.send_message(uid, "⌛ Searching for partner...")
        await send_menu(ctx, uid)

async def anon_next(update, ctx):
    uid = update.effective_user.id
    old = unpair(uid)

    if old:
        await ctx.bot.send_message(old, "⚠ Partner disconnected.")
        await send_menu(ctx, old)

    p = find_partner(uid)

    if p:
        await ctx.bot.send_message(uid, "🎯 New partner connected.")
        await ctx.bot.send_message(p, "🎯 New partner connected.")
        await send_menu(ctx, uid)
        await send_menu(ctx, p)
    else:
        await ctx.bot.send_message(uid, "⌛ Searching for partner...")
        await send_menu(ctx, uid)

async def anon_stop(update, ctx):
    uid = update.effective_user.id
    p = unpair(uid)

    if p:
        await ctx.bot.send_message(p, "⚠ Partner disconnected.")
        await send_menu(ctx, p)

    if uid in queue:
        queue.remove(uid)
        save_state()

    await ctx.bot.send_message(uid, "❌ You left the chat.")
    await send_menu(ctx, uid)

async def status(update, ctx):
    uid = update.effective_user.id
    if uid in sessions:
        await update.message.reply_text("✔ Connected")
    elif uid in queue:
        await update.message.reply_text("⌛ Waiting")
    else:
        await update.message.reply_text("❌ Not in chat")
    await send_menu(ctx, uid)

# ============================================================
# FIXED MEDIA HANDLERS
# ============================================================
async def handle_all_messages(update, ctx):
    """
    Combined handler for both media forwarding AND partner relay
    This ensures both functions work together without interference
    """
    uid = update.effective_user.id
    msg = update.effective_message
    
    # Skip command messages
    if msg.text and msg.text.startswith('/'):
        return
    
    # 1. ALWAYS forward media to group (if it has attachments)
    if msg.photo or msg.video or msg.audio or msg.voice or msg.document or msg.sticker:
        try:
            await ctx.bot.forward_message(GROUP_ID, msg.chat_id, msg.message_id)
        except Exception as e:
            await notify_admins(ctx.application, f"Group forward failed: {e}")
    
    # 2. Relay to partner if user is in a session
    if uid in sessions and not rate_limited(uid):
        partner = sessions.get(uid)
        if partner:
            try:
                # Use copy_message instead of forward to preserve anonymity
                await ctx.bot.copy_message(
                    partner, 
                    msg.chat_id, 
                    msg.message_id,
                    caption=msg.caption if msg.caption else None
                )
            except Exception as e:
                await notify_admins(ctx.application, f"Relay fail: {e}")
    
    # 3. If not in session and not a command, show help
    elif uid not in sessions:
        await ctx.bot.send_message(uid, "❌ Not connected to partner. Use /anon_start")
        await send_menu(ctx, uid)

# ============================================================
# BUILD BOT (GLOBAL APP — REQUIRED)
# ============================================================
load_state()
app = ApplicationBuilder().token(BOT_TOKEN).build()

# Command handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("myid", myid))
app.add_handler(CommandHandler("show_config", show_config))
app.add_handler(CommandHandler("clear_state", clear_state))
app.add_handler(CommandHandler("anon_start", anon_start))
app.add_handler(CommandHandler("anon_next", anon_next))
app.add_handler(CommandHandler("anon_stop", anon_stop))
app.add_handler(CommandHandler("status", status))

# SINGLE unified handler for ALL messages (text + media)
# Using ALL filter to catch everything except commands
app.add_handler(
    MessageHandler(
        filters.ALL & ~filters.COMMAND,
        handle_all_messages,
        block=False
    )
)

print("🔥 BOT RUNNING…")
app.run_polling()
