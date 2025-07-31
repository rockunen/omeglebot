import os
import asyncio
import psycopg2
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from psycopg2 import pool
db_pool = None

def init_pool():
    global db_pool
    db_pool = psycopg2.pool.SimpleConnectionPool(
        1, 20,  # minconn, maxconn
        dsn=DB_URL,
        sslmode='require'
    )

load_dotenv()

DB_URL = os.getenv("DATABASE_URL")  # You MUST set this in your Railway environment
TOKEN = os.getenv("BOT_TOKEN")  # Store your bot token in .env or Railway variables

def get_conn():
    return db_pool.getconn()

# --- DB Setup ---
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id BIGINT PRIMARY KEY,
            username TEXT,
            is_available BOOLEAN,
            is_paired_with BIGINT,
            reports INTEGER DEFAULT 0,
            blocked BOOLEAN DEFAULT FALSE
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender_id BIGINT,
            sender_username TEXT,
            receiver_id BIGINT,
            receiver_username TEXT,
            content TEXT,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def register_user(user: Update.effective_user):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO users (telegram_id, username, is_available, is_paired_with)
            VALUES (%s, %s, FALSE, NULL)
            ON CONFLICT (telegram_id) DO NOTHING
        ''', (user.id, user.username))
        cur.execute('UPDATE users SET username=%s WHERE telegram_id=%s', (user.username, user.id))
        conn.commit()
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection

def get_user(telegram_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id=%s", (telegram_id,))
        return cur.fetchone()
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection

def find_partner(my_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users WHERE is_available=TRUE AND telegram_id != %s AND blocked=FALSE LIMIT 1", (my_id,))
        result = cur.fetchone()
        if result:
            partner_id = result[0]
            cur.execute("UPDATE users SET is_available=FALSE, is_paired_with=%s WHERE telegram_id=%s", (partner_id, my_id))
            cur.execute("UPDATE users SET is_available=FALSE, is_paired_with=%s WHERE telegram_id=%s", (my_id, partner_id))
            conn.commit()
            return partner_id
        else:
            cur.execute("UPDATE users SET is_available=TRUE, is_paired_with=NULL WHERE telegram_id=%s", (my_id,))
            conn.commit()
            return None
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection

def get_partner(telegram_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=%s", (telegram_id,))
        result = cur.fetchone()
        return result[0] if result else None
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection

def get_username(telegram_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username FROM users WHERE telegram_id=%s", (telegram_id,))
        result = cur.fetchone()
        return result[0] if result else ""
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection
def stop_chat(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=%s", (user_id,))
        result = cur.fetchone()
        if result and result[0]:
            partner_id = result[0]
            cur.execute("UPDATE users SET is_available=FALSE, is_paired_with=NULL WHERE telegram_id IN (%s, %s)", (user_id, partner_id))
            conn.commit()
            return partner_id
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection
    return None

def add_message(sender_id, receiver_id, content):
    sender_username = get_username(sender_id)
    receiver_username = get_username(receiver_id)
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO messages (sender_id, sender_username, receiver_id, receiver_username, content)
            VALUES (%s, %s, %s, %s, %s)
        ''', (sender_id, sender_username, receiver_id, receiver_username, content))
        conn.commit()
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection

def report_user(from_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_paired_with FROM users WHERE telegram_id=%s", (from_id,))
        result = cur.fetchone()
        if result and result[0]:
            partner_id = result[0]
            cur.execute("UPDATE users SET reports = reports + 1 WHERE telegram_id=%s", (partner_id,))
            cur.execute("SELECT reports FROM users WHERE telegram_id=%s", (partner_id,))
            reports = cur.fetchone()[0]
            if reports >= 5:
                cur.execute("UPDATE users SET blocked = TRUE WHERE telegram_id=%s", (partner_id,))
            cur.execute("UPDATE users SET is_available=FALSE, is_paired_with=NULL WHERE telegram_id IN (%s, %s)", (from_id, partner_id))
            conn.commit()
            return partner_id, reports
    finally:
        db_pool.putconn(conn)  # ✅ always release the connection
    return None, 0

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user: register_user(update.effective_user)
    await update.message.reply_text("👋 Welcome to Anonymous Chat!\nUse /find to connect with a stranger.\n We are not responsible for any vulgar chats")

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if get_user(telegram_id)[5]:  # if blocked
        await update.message.reply_text("🚫 You have been blocked due to multiple reports.")
        return
    current_partner = get_partner(telegram_id)
    if current_partner:
        await update.message.reply_text("❌ You're already in a chat. Use /stop first.")
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
        await context.bot.send_message(partner_id, "❌ The stranger has left the chat.")
        await update.message.reply_text("❌ You left the chat.")
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
        await update.message.reply_text("❗ You're not in a chat. Use /find to connect.")

# --- Main ---
if __name__ == "__main__":
    init_pool()
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_message))

    print("Bot is running...")
    asyncio.run(app.run_polling())
