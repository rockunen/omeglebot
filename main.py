import sqlite3
import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

DB_FILE = "omegledb.db"
TOKEN = "8124998861:AAGGUWzHByOxg3loZz0FUjT5M_tc2vUciz0"

# --- DB Setup ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            is_available BOOLEAN,
            is_paired_with INTEGER,
            reports INTEGER DEFAULT 0,
            blocked BOOLEAN DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER,
            sender_username TEXT,
            receiver_id INTEGER,
            receiver_username TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def register_user(user: Update.effective_user):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT OR IGNORE INTO users (telegram_id, username, is_available, is_paired_with)
            VALUES (?, ?, 0, NULL)
        ''', (user.id, user.username))
        cur.execute('''
            UPDATE users SET username=? WHERE telegram_id=?
        ''', (user.username, user.id))  # Keep username updated

        conn.commit()

def get_user(telegram_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return cur.fetchone()

def find_partner(my_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users WHERE is_available=1 AND telegram_id != ? AND blocked=0", (my_id,))
        result = cur.fetchone()
        if result:
            partner_id = result[0]
            cur.execute("UPDATE users SET is_available=0, is_paired_with=? WHERE telegram_id=?", (partner_id, my_id))
            cur.execute("UPDATE users SET is_available=0, is_paired_with=? WHERE telegram_id=?", (my_id, partner_id))
            conn.commit()
            return partner_id
        else:
            cur.execute("UPDATE users SET is_available=1, is_paired_with=NULL WHERE telegram_id=?", (my_id,))
            conn.commit()
            return None

def get_partner(telegram_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=?", (telegram_id,))
        result = cur.fetchone()
        return result[0] if result else None

def get_username(telegram_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE telegram_id=?", (telegram_id,))
        result = cur.fetchone()
        return result[0] if result else ""

def stop_chat(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=?", (user_id,))
        result = cur.fetchone()
        if result and result[0]:
            partner_id = result[0]
            cur.execute("UPDATE users SET is_available=0, is_paired_with=NULL WHERE telegram_id IN (?, ?)", (user_id, partner_id))
            conn.commit()
            return partner_id
    return None

def add_message(sender_id, receiver_id, content):
    sender_username = get_username(sender_id)
    receiver_username = get_username(receiver_id)
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO messages (sender_id, sender_username, receiver_id, receiver_username, content)
            VALUES (?, ?, ?, ?, ?)
        ''', (sender_id, sender_username, receiver_id, receiver_username, content))
        conn.commit()

def report_user(from_id):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=?", (from_id,))
        result = cur.fetchone()
        if result and result[0]:
            partner_id = result[0]
            cur.execute("UPDATE users SET reports = reports + 1 WHERE telegram_id=?", (partner_id,))
            cur.execute("SELECT reports FROM users WHERE telegram_id=?", (partner_id,))
            reports = cur.fetchone()[0]
            if reports >= 5:
                cur.execute("UPDATE users SET blocked = 1 WHERE telegram_id=?", (partner_id,))
            cur.execute("UPDATE users SET is_available=0, is_paired_with=NULL WHERE telegram_id IN (?, ?)", (from_id, partner_id))
            conn.commit()
            return partner_id, reports
    return None, 0

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user: register_user(update.effective_user)

    await update.message.reply_text("👋 Welcome to Anonymous Chat!\nUse /find to connect with a stranger.")

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if get_user(telegram_id)[5]:  # if blocked
        await update.message.reply_text("🚫 You have been blocked due to multiple reports.")
        return
    # ❌ Prevent if user is already chatting
    current_partner = get_partner(telegram_id)
    if current_partner:
        await update.message.reply_text("❌ You are already in a chat. Use /stop to end current chat before finding a new one.")
        return
    partner = find_partner(telegram_id)
    if partner:
        await context.bot.send_message(partner, "🎉 You are now connected with a stranger!")
        await update.message.reply_text("🎉 You are now connected with a stranger!")
    else:
        await update.message.reply_text("⌛ Waiting for a partner...")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    partner_id = stop_chat(user_id)
    if partner_id:
        await context.bot.send_message(partner_id, "❌ The stranger has ended the chat.")
        await update.message.reply_text("❌ You have left the chat.")
    else:
        await update.message.reply_text("You're not in a chat.")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from_id = update.effective_user.id
    partner_id, reports = report_user(from_id)
    if partner_id:
        await context.bot.send_message(partner_id, "⚠️ You have been reported!")
        await update.message.reply_text(f"✅ Reported the user. They now have {reports} report(s).")
        if reports >= 5:
            await context.bot.send_message(partner_id, "🚫 You have been blocked from using the bot.")
    else:
        await update.message.reply_text("⚠️ No one to report!")

async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    partner_id = get_partner(sender_id)
    if partner_id:
        text = update.message.text
        await context.bot.send_message(partner_id, text)
        add_message(sender_id, partner_id, text)
    else:
        await update.message.reply_text("❗ You are not in a chat. Use /find to connect.")

# --- Main ---
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message))

    print("Bot is running...")
    asyncio.run(app.run_polling())

