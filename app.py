from flask import Flask, render_template, request, redirect, url_for, session
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = "yasdbasdjahvdavdywguyq"  # Use a strong secret key in production

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "12345"

DATABASE_URL = os.getenv("DATABASE_URL")  # Your Railway PostgreSQL URL

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require", cursor_factory=RealDictCursor)

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("dashboard"))
        return "Invalid credentials!"
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if not session.get("admin"):
        return redirect(url_for("login"))

    conn = get_db()
    cur = conn.cursor()

    # Fetch messages with user info
    cur.execute("""
        SELECT m.id,
               m.content,
               m.timestamp,
               s.telegram_id AS sender_id,
               s.username    AS sender_username,
               r.telegram_id AS receiver_id,
               r.username    AS receiver_username
        FROM messages m
                 LEFT JOIN users s ON m.sender_id = s.telegram_id
                 LEFT JOIN users r ON m.receiver_id = r.telegram_id
        ORDER BY m.timestamp DESC
    """)
    messages = cur.fetchall()

    # Fetch user report and block info
    cur.execute("SELECT telegram_id, reports, blocked FROM users")
    users = cur.fetchall()

    conn.close()
    return render_template("index.html", messages=messages, users=users)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
