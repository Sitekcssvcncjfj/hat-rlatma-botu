import os
import sqlite3
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DB_NAME = "/data/reminders.db"
TZ = ZoneInfo("Europe/Istanbul")

if not TOKEN:
    raise ValueError("BOT_TOKEN bulunamadı.")
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID bulunamadı.")

CHOOSING_TYPE, CHOOSING_CATEGORY, ASK_TIME, ASK_DATE, ASK_DAY, ASK_MESSAGE = range(6)

WEEKDAY_MAP = {
    "pazartesi": 0,
    "salı": 1,
    "sali": 1,
    "çarşamba": 2,
    "carsamba": 2,
    "perşembe": 3,
    "persembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "pazar": 6,
}

WEEKDAY_NAMES = {
    0: "Pazartesi",
    1: "Salı",
    2: "Çarşamba",
    3: "Perşembe",
    4: "Cuma",
    5: "Cumartesi",
    6: "Pazar",
}

CATEGORIES = ["ilaç", "iş", "fatura", "kişisel", "doğum günü", "diğer"]


# ---------------------------
# DB
# ---------------------------

def get_conn():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            username TEXT,
            full_name TEXT,
            chat_type TEXT,
            first_seen TEXT,
            last_seen TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            chat_type TEXT,
            type TEXT NOT NULL,
            category TEXT,
            remind_time TEXT,
            remind_date TEXT,
            message TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            repeat_count INTEGER DEFAULT 0,
            pending INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()

def save_user_or_chat(update: Update):
    user = update.effective_user
    chat = update.effective_chat
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT id FROM users
        WHERE user_id = ? AND chat_id = ?
    """, (user.id, chat.id))
    row = c.fetchone()

    if row:
        c.execute("""
            UPDATE users
            SET username = ?, full_name = ?, chat_type = ?, last_seen = ?
            WHERE user_id = ? AND chat_id = ?
        """, (
            user.username or "",
            user.full_name or "",
            chat.type,
            now,
            user.id,
            chat.id
        ))
    else:
        c.execute("""
            INSERT INTO users (user_id, chat_id, username, full_name, chat_type, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user.id,
            chat.id,
            user.username or "",
            user.full_name or "",
            chat.type,
            now,
            now
        ))

    conn.commit()
    conn.close()

def add_reminder(chat_id, user_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO reminders (
            chat_id, user_id, username, full_name, chat_type,
            type, category, remind_time, remind_date, message,
            active, repeat_count, pending
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0)
    """, (
        chat_id, user_id, username, full_name, chat_type,
        r_type, category, remind_time, remind_date, message
    ))
    reminder_id = c.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def get_all_active_reminders():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, chat_id, type, category, remind_time, remind_date, message
        FROM reminders
        WHERE active = 1
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_reminders(chat_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, type, category, remind_time, remind_date, message, active
        FROM reminders
        WHERE chat_id = ?
        ORDER BY id DESC
    """, (chat_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_reminder(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, chat_id, user_id, username, full_name, chat_type, type, category,
               remind_time, remind_date, message, active, repeat_count, pending
        FROM reminders
        WHERE id = ?
    """, (reminder_id,))
    row = c.fetchone()
    conn.close()
    return row

def delete_reminder(reminder_id, chat_id=None):
    conn = get_conn()
    c = conn.cursor()
    if chat_id is not None:
        c.execute("DELETE FROM reminders WHERE id = ? AND chat_id = ?", (reminder_id, chat_id))
    else:
        c.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    affected = c.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def deactivate_reminder(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE reminders
        SET active = 0, pending = 0
        WHERE id = ?
    """, (reminder_id,))
    conn.commit()
    conn.close()

def set_pending(reminder_id, pending=1):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET pending = ? WHERE id = ?", (pending, reminder_id))
    conn.commit()
    conn.close()

def reset_repeat_count(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET repeat_count = 0 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def increment_repeat_count(reminder_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE reminders SET repeat_count = repeat_count + 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def get_admin_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT user_id, chat_id, username, full_name, chat_type, first_seen, last_seen
        FROM users
        ORDER BY last_seen DESC
        LIMIT 200
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def get_admin_reminders():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, user_id, chat_id, username, full_name, chat_type, type, category,
               remind_time, remind_date, message, active
        FROM reminders
        ORDER BY id DESC
        LIMIT 200
    """)
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_reminders_by_user_id(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, user_id, chat_id, username, full_name, chat_type, type, category,
               remind_time, remind_date, message, active
        FROM reminders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 100
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT user_id) FROM users")
    unique_people = c.fetchone()[0]

    c.execute("SELECT COUNT(DISTINCT chat_id) FROM users WHERE chat_type IN ('group', 'supergroup')")
    total_groups = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM reminders")
    total_reminders = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM reminders WHERE active = 1")
    active_reminders = c.fetchone()[0]

    conn.close()
    return total_users, unique_people, total_groups, total_reminders, active_reminders

def update_reminder_message(reminder_id, chat_id, new_message):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE reminders
        SET message = ?
        WHERE id = ? AND chat_id = ?
    """, (new_message, reminder_id, chat_id))
    ok = c.rowcount
    conn.commit()
    conn.close()
    return ok > 0

def update_reminder_time(reminder_id, chat_id, new_time):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE reminders
        SET remind_time = ?
        WHERE id = ? AND chat_id = ?
    """, (new_time, reminder_id, chat_id))
    ok = c.rowcount
    conn.commit()
    conn.close()
    return ok > 0

def update_reminder_category(reminder_id, chat_id, category):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        UPDATE reminders
        SET category = ?
        WHERE id = ? AND chat_id = ?
    """, (category, reminder_id, chat_id))
    ok = c.rowcount
    conn.commit()
    conn.close()
    return ok > 0


# ---------------------------
# HELPERS
# ---------------------------

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_CHAT_ID)

def support_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📞 Destek", url="https://t.me/KGBotomasyon")]
    ])

def main_menu_keyboard(admin=False):
    rows = [
        ["➕ Hatırlatma Ekle", "📋 Hatırlatmalarım"],
        ["ℹ️ Yardım", "📞 Destek"]
    ]
    if admin:
        rows.append(["🛠 Admin Panel"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def reminder_action_keyboard(reminder_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tamamlandı", callback_data=f"done:{reminder_id}"),
            InlineKeyboardButton("🗑 Sil", callback_data=f"delete:{reminder_id}")
        ],
        [
            InlineKeyboardButton("⏰ 10 dk ertele", callback_data=f"snooze10:{reminder_id}"),
            InlineKeyboardButton("⏰ 1 saat ertele", callback_data=f"snooze60:{reminder_id}")
        ]
    ])

def list_delete_keyboard(reminder_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Sil", callback_data=f"userdelete:{reminder_id}")]
    ])

def schedule_all_types(app, reminder_id, chat_id, r_type, remind_date, remind_time):
    if r_type == "daily":
        schedule_daily_job(app, reminder_id, remind_time)
    elif r_type == "once":
        schedule_once_job(app, reminder_id, remind_date, remind_time)
    elif r_type == "monthly":
        schedule_monthly_job(app, reminder_id, remind_date, remind_time)
    elif r_type == "weekly":
        schedule_weekly_job(app, reminder_id, remind_date, remind_time)


# ---------------------------
# JOBS
# ---------------------------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    reminder_id = context.job.data["id"]
    row = get_reminder(reminder_id)

    if not row:
        return

    _, chat_id, user_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message, active, repeat_count, pending = row

    if active != 1:
        return

    set_pending(reminder_id, 1)

    text = (
        f"⏰ Hatırlatma\n\n"
        f"🗂 Kategori: {category or 'diğer'}\n"
        f"📝 Mesaj: {message}"
    )

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reminder_action_keyboard(reminder_id)
    )

    context.job_queue.run_once(
        check_unanswered_reminder,
        when=60,
        data={"id": reminder_id},
        name=f"recheck_{reminder_id}_{datetime.now(TZ).timestamp()}"
    )

async def check_unanswered_reminder(context: ContextTypes.DEFAULT_TYPE):
    reminder_id = context.job.data["id"]
    row = get_reminder(reminder_id)

    if not row:
        return

    _, chat_id, user_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message, active, repeat_count, pending = row

    if active != 1 or pending != 1:
        return

    if repeat_count >= 3:
        return

    increment_repeat_count(reminder_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🔔 Hatırlatma tekrar\n\n"
            f"🗂 Kategori: {category or 'diğer'}\n"
            f"📝 Mesaj: {message}\n\n"
            f"Lütfen bir işlem seç."
        ),
        reply_markup=reminder_action_keyboard(reminder_id)
    )

    context.job_queue.run_once(
        check_unanswered_reminder,
        when=60,
        data={"id": reminder_id},
        name=f"recheck_{reminder_id}_{datetime.now(TZ).timestamp()}"
    )

def schedule_daily_job(app, reminder_id, remind_time):
    t = datetime.strptime(remind_time, "%H:%M").time().replace(tzinfo=TZ)
    app.job_queue.run_daily(send_reminder, time=t, data={"id": reminder_id}, name=f"daily_{reminder_id}")

def schedule_monthly_job(app, reminder_id, day, remind_time):
    t = datetime.strptime(remind_time, "%H:%M").time().replace(tzinfo=TZ)
    app.job_queue.run_monthly(send_reminder, when=t, day=int(day), data={"id": reminder_id}, name=f"monthly_{reminder_id}")

def schedule_weekly_job(app, reminder_id, weekday, remind_time):
    t = datetime.strptime(remind_time, "%H:%M").time().replace(tzinfo=TZ)
    app.job_queue.run_daily(send_reminder, time=t, days=(int(weekday),), data={"id": reminder_id}, name=f"weekly_{reminder_id}")

def schedule_once_job(app, reminder_id, remind_date, remind_time):
    target_dt = datetime.strptime(f"{remind_date} {remind_time}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    now = datetime.now(TZ)
    if target_dt <= now:
        return
    delay = (target_dt - now).total_seconds()
    app.job_queue.run_once(send_reminder, when=delay, data={"id": reminder_id}, name=f"once_{reminder_id}")

def remove_jobs_for_reminder(app, reminder_id):
    for job in app.job_queue.jobs():
        if job.name.startswith(f"daily_{reminder_id}") or \
           job.name.startswith(f"monthly_{reminder_id}") or \
           job.name.startswith(f"weekly_{reminder_id}") or \
           job.name.startswith(f"once_{reminder_id}") or \
           job.name.startswith(f"recheck_{reminder_id}") or \
           job.name.startswith(f"snooze_{reminder_id}"):
            job.schedule_removal()

def load_jobs(app):
    reminders = get_all_active_reminders()
    for reminder_id, chat_id, r_type, category, remind_time, remind_date, message in reminders:
        try:
            schedule_all_types(app, reminder_id, chat_id, r_type, remind_date, remind_time)
        except Exception as e:
            logging.error(f"Job yüklenemedi {reminder_id}: {e}")


# ---------------------------
# START / MENU
# ---------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    text = "Hoş geldin. Menüden işlem seçebilirsin."
    await update.message.reply_text(
        text,
        reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id))
    )

async def yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    text = (
        "📘 Yardım\n\n"
        "Bot özelde ve grupta çalışır.\n"
        "Grupta eklenen hatırlatmalar gruba gider.\n"
        "Özelde eklenen hatırlatmalar sana özel gelir.\n\n"
        "Komutlar:\n"
        "/start\n"
        "/liste\n"
        "/duzenle_mesaj ID yeni mesaj\n"
        "/duzenle_saat ID HH:MM\n"
        "/duzenle_kategori ID kategori\n"
    )
    await update.message.reply_text(text, reply_markup=support_keyboard())

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    text = update.message.text

    if text == "📋 Hatırlatmalarım":
        await liste(update, context)
        return ConversationHandler.END

    if text == "ℹ️ Yardım":
        await yardim(update, context)
        return ConversationHandler.END

    if text == "📞 Destek":
        await update.message.reply_text("Destek için butona bas:", reply_markup=support_keyboard())
        return ConversationHandler.END

    if text == "🛠 Admin Panel":
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Bu alan sadece admin içindir.")
            return ConversationHandler.END

        keyboard = [
            ["👥 Kullanıcılar", "📝 Tüm Hatırlatmalar"],
            ["📊 İstatistik", "⬅️ Geri"]
        ]
        await update.message.reply_text(
            "Admin paneli",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return ConversationHandler.END

    if text == "👥 Kullanıcılar":
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        await admin_users(update, context)
        return ConversationHandler.END

    if text == "📝 Tüm Hatırlatmalar":
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        await admin_reminders(update, context)
        return ConversationHandler.END

    if text == "📊 İstatistik":
        if not is_admin(update.effective_user.id):
            return ConversationHandler.END
        await admin_stats(update, context)
        return ConversationHandler.END

    if text == "⬅️ Geri":
        await update.message.reply_text(
            "Ana menü",
            reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id))
        )
        return ConversationHandler.END

    return ConversationHandler.END


# ---------------------------
# ADD FLOW
# ---------------------------

async def add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    keyboard = [
        ["Günlük", "Tarihli"],
        ["Aylık", "Haftalık"],
        ["İptal"]
    ]
    await update.message.reply_text(
        "Hatırlatma türünü seç:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CHOOSING_TYPE

async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if text == "iptal":
        await update.message.reply_text("İşlem iptal edildi.", reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id)))
        return ConversationHandler.END

    type_map = {
        "günlük": "daily",
        "gunluk": "daily",
        "tarihli": "once",
        "aylık": "monthly",
        "aylik": "monthly",
        "haftalık": "weekly",
        "haftalik": "weekly",
    }

    if text not in type_map:
        await update.message.reply_text("Geçerli bir tür seç.")
        return CHOOSING_TYPE

    context.user_data["rtype"] = type_map[text]
    keyboard = [[c] for c in CATEGORIES] + [["İptal"]]

    await update.message.reply_text(
        "Kategori seç:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CHOOSING_CATEGORY

async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    if text == "iptal":
        await update.message.reply_text("İşlem iptal edildi.", reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id)))
        return ConversationHandler.END

    if text not in CATEGORIES:
        await update.message.reply_text("Geçerli kategori seç.")
        return CHOOSING_CATEGORY

    context.user_data["category"] = text
    rtype = context.user_data["rtype"]

    if rtype == "once":
        await update.message.reply_text("Tarih gir (YYYY-MM-DD):", reply_markup=ReplyKeyboardRemove())
        return ASK_DATE
    elif rtype == "monthly":
        await update.message.reply_text("Ayın kaçıncı günü? (1-31)", reply_markup=ReplyKeyboardRemove())
        return ASK_DAY
    elif rtype == "weekly":
        await update.message.reply_text("Haftanın günü? (pazartesi, salı, cuma...)", reply_markup=ReplyKeyboardRemove())
        return ASK_DAY
    else:
        await update.message.reply_text("Saat gir (HH:MM):", reply_markup=ReplyKeyboardRemove())
        return ASK_TIME

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
        context.user_data["remind_date"] = text
        await update.message.reply_text("Saat gir (HH:MM):")
        return ASK_TIME
    except:
        await update.message.reply_text("Tarih yanlış. Örnek: 2026-04-04")
        return ASK_DATE

async def ask_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    rtype = context.user_data["rtype"]

    if rtype == "monthly":
        try:
            day = int(text)
            if day < 1 or day > 31:
                raise ValueError
            context.user_data["remind_date"] = str(day)
            await update.message.reply_text("Saat gir (HH:MM):")
            return ASK_TIME
        except:
            await update.message.reply_text("1-31 arasında gün gir.")
            return ASK_DAY

    if rtype == "weekly":
        if text not in WEEKDAY_MAP:
            await update.message.reply_text("Geçerli gün gir. Örnek: pazartesi")
            return ASK_DAY
        context.user_data["remind_date"] = str(WEEKDAY_MAP[text])
        await update.message.reply_text("Saat gir (HH:MM):")
        return ASK_TIME

async def ask_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%H:%M")
        context.user_data["remind_time"] = text
        await update.message.reply_text("Mesajı yaz:")
        return ASK_MESSAGE
    except:
        await update.message.reply_text("Saat yanlış. Örnek: 14:30")
        return ASK_TIME

async def ask_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text.strip()
    if not message:
        await update.message.reply_text("Mesaj boş olamaz.")
        return ASK_MESSAGE

    user = update.effective_user
    chat = update.effective_chat
    rtype = context.user_data["rtype"]
    category = context.user_data["category"]
    remind_time = context.user_data["remind_time"]
    remind_date = context.user_data.get("remind_date")

    if rtype == "once":
        target_dt = datetime.strptime(f"{remind_date} {remind_time}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        if target_dt <= datetime.now(TZ):
            await update.message.reply_text("Geçmiş tarih veremezsin.")
            return ConversationHandler.END

    reminder_id = add_reminder(
        chat_id=chat.id,
        user_id=user.id,
        username=user.username or "",
        full_name=user.full_name or "",
        chat_type=chat.type,
        r_type=rtype,
        category=category,
        remind_time=remind_time,
        remind_date=remind_date,
        message=message
    )

    schedule_all_types(context.application, reminder_id, chat.id, rtype, remind_date, remind_time)

    await update.message.reply_text(
        f"✅ Hatırlatma eklendi\n\n"
        f"🆔 ID: {reminder_id}\n"
        f"🗂 Kategori: {category}\n"
        f"📝 Mesaj: {message}",
        reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id))
    )

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "İşlem iptal edildi.",
        reply_markup=main_menu_keyboard(admin=is_admin(update.effective_user.id))
    )
    return ConversationHandler.END


# ---------------------------
# LIST / CALLBACK
# ---------------------------

async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    chat_id = update.effective_chat.id
    reminders = get_user_reminders(chat_id)

    if not reminders:
        await update.message.reply_text("📭 Hatırlatma bulunamadı.")
        return

    for r in reminders[:20]:
        reminder_id, r_type, category, remind_time, remind_date, message, active = r

        if r_type == "daily":
            desc = f"🔁 Günlük\n🕒 {remind_time}"
        elif r_type == "once":
            desc = f"📌 Tek Seferlik\n📅 {remind_date}\n🕒 {remind_time}"
        elif r_type == "monthly":
            desc = f"🗓 Aylık\n📅 Her ayın {remind_date}. günü\n🕒 {remind_time}"
        elif r_type == "weekly":
            desc = f"📆 Haftalık\n📅 {WEEKDAY_NAMES.get(int(remind_date), remind_date)}\n🕒 {remind_time}"
        else:
            desc = r_type

        status = "Aktif ✅" if active == 1 else "Pasif ❌"

        text = (
            f"🆔 {reminder_id}\n"
            f"{desc}\n"
            f"🗂 Kategori: {category}\n"
            f"📝 Mesaj: {message}\n"
            f"📊 Durum: {status}"
        )

        await update.message.reply_text(text, reply_markup=list_delete_keyboard(reminder_id))

async def inline_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if ":" not in data:
        return

    action, reminder_id_str = data.split(":", 1)
    reminder_id = int(reminder_id_str)

    row = get_reminder(reminder_id)
    if not row:
        await query.edit_message_text("Hatırlatma bulunamadı.")
        return

    _, chat_id, user_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message, active, repeat_count, pending = row

    current_user_id = query.from_user.id
    current_chat_id = query.message.chat_id

    # sadece admin veya o reminderın sahibi işlem yapabilsin
    if not (is_admin(current_user_id) or current_chat_id == chat_id or current_user_id == user_id):
        await query.answer("Bu işlem sana ait değil.", show_alert=True)
        return

    if action == "done":
        set_pending(reminder_id, 0)
        reset_repeat_count(reminder_id)

        if r_type == "once":
            deactivate_reminder(reminder_id)
            remove_jobs_for_reminder(context.application, reminder_id)

        await query.edit_message_text(
            f"✅ Tamamlandı\n\n🗂 Kategori: {category}\n📝 Mesaj: {message}"
        )

    elif action in ["delete", "userdelete"]:
        delete_reminder(reminder_id, chat_id=chat_id)
        remove_jobs_for_reminder(context.application, reminder_id)
        await query.edit_message_text("🗑 Hatırlatma silindi.")

    elif action == "snooze10":
        set_pending(reminder_id, 0)
        reset_repeat_count(reminder_id)
        context.application.job_queue.run_once(
            send_reminder,
            when=10 * 60,
            data={"id": reminder_id},
            name=f"snooze_{reminder_id}_{datetime.now(TZ).timestamp()}"
        )
        await query.edit_message_text(
            f"⏰ 10 dakika ertelendi\n\n🗂 Kategori: {category}\n📝 Mesaj: {message}"
        )

    elif action == "snooze60":
        set_pending(reminder_id, 0)
        reset_repeat_count(reminder_id)
        context.application.job_queue.run_once(
            send_reminder,
            when=60 * 60,
            data={"id": reminder_id},
            name=f"snooze_{reminder_id}_{datetime.now(TZ).timestamp()}"
        )
        await query.edit_message_text(
            f"⏰ 1 saat ertelendi\n\n🗂 Kategori: {category}\n📝 Mesaj: {message}"
        )


# ---------------------------
# EDIT COMMANDS
# ---------------------------

async def duzenle_mesaj(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    try:
        chat_id = update.effective_chat.id
        reminder_id = int(context.args[0])
        new_message = " ".join(context.args[1:]).strip()

        if not new_message:
            raise ValueError

        ok = update_reminder_message(reminder_id, chat_id, new_message)
        if not ok:
            await update.message.reply_text("Hatırlatma bulunamadı.")
            return

        row = get_reminder(reminder_id)
        remove_jobs_for_reminder(context.application, reminder_id)
        _, r_chat_id, _, _, _, _, r_type, _, remind_time, remind_date, _, active, _, _ = row
        if active == 1:
            schedule_all_types(context.application, reminder_id, r_chat_id, r_type, remind_date, remind_time)

        await update.message.reply_text("✅ Mesaj güncellendi.")

    except:
        await update.message.reply_text("Kullanım:\n/duzenle_mesaj ID yeni mesaj")

async def duzenle_saat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    try:
        chat_id = update.effective_chat.id
        reminder_id = int(context.args[0])
        new_time = context.args[1]

        datetime.strptime(new_time, "%H:%M")

        ok = update_reminder_time(reminder_id, chat_id, new_time)
        if not ok:
            await update.message.reply_text("Hatırlatma bulunamadı.")
            return

        row = get_reminder(reminder_id)
        remove_jobs_for_reminder(context.application, reminder_id)
        _, r_chat_id, _, _, _, _, r_type, _, remind_time, remind_date, _, active, _, _ = row
        if active == 1:
            schedule_all_types(context.application, reminder_id, r_chat_id, r_type, remind_date, remind_time)

        await update.message.reply_text("✅ Saat güncellendi.")

    except:
        await update.message.reply_text("Kullanım:\n/duzenle_saat ID HH:MM")

async def duzenle_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_or_chat(update)
    try:
        chat_id = update.effective_chat.id
        reminder_id = int(context.args[0])
        category = " ".join(context.args[1:]).strip().lower()

        if category not in CATEGORIES:
            await update.message.reply_text(f"Geçerli kategoriler: {', '.join(CATEGORIES)}")
            return

        ok = update_reminder_category(reminder_id, chat_id, category)
        if not ok:
            await update.message.reply_text("Hatırlatma bulunamadı.")
            return

        await update.message.reply_text("✅ Kategori güncellendi.")

    except:
        await update.message.reply_text("Kullanım:\n/duzenle_kategori ID kategori")


# ---------------------------
# ADMIN
# ---------------------------

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Yetkisiz.")
        return

    rows = get_admin_users()
    if not rows:
        await update.message.reply_text("Kullanıcı yok.")
        return

    for r in rows[:50]:
        user_id, chat_id, username, full_name, chat_type, first_seen, last_seen = r
        text = (
            f"👤 {full_name}\n"
            f"🔹 Username: @{username}\n"
            f"🆔 User ID: {user_id}\n"
            f"💬 Chat ID: {chat_id}\n"
            f"📦 Tür: {chat_type}\n"
            f"🕒 İlk: {first_seen}\n"
            f"🕒 Son: {last_seen}"
        )
        await update.message.reply_text(text)

async def admin_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Yetkisiz.")
        return

    rows = get_admin_reminders()
    if not rows:
        await update.message.reply_text("Hatırlatma yok.")
        return

    for r in rows[:50]:
        reminder_id, user_id, chat_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message, active = r
        text = (
            f"🆔 {reminder_id}\n"
            f"👤 {full_name} (@{username})\n"
            f"🆔 User ID: {user_id}\n"
            f"💬 Chat ID: {chat_id}\n"
            f"📦 Chat Türü: {chat_type}\n"
            f"📌 Tür: {r_type}\n"
            f"🗂 Kategori: {category}\n"
            f"📅 Tarih/Gün: {remind_date}\n"
            f"🕒 Saat: {remind_time}\n"
            f"📝 Mesaj: {message}\n"
            f"📊 Aktif: {active}"
        )
        await update.message.reply_text(text)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Yetkisiz.")
        return

    total_users, unique_people, total_groups, total_reminders, active_reminders = get_stats()

    text = (
        f"📊 İstatistik\n\n"
        f"Toplam user/chat kaydı: {total_users}\n"
        f"Tekil kullanıcı: {unique_people}\n"
        f"Toplam grup: {total_groups}\n"
        f"Toplam hatırlatma: {total_reminders}\n"
        f"Aktif hatırlatma: {active_reminders}"
    )
    await update.message.reply_text(text)

async def admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Yetkisiz.")
        return

    try:
        user_id = int(context.args[0])
        rows = get_user_reminders_by_user_id(user_id)

        if not rows:
            await update.message.reply_text("Bu kullanıcıya ait kayıt bulunamadı.")
            return

        for r in rows[:50]:
            reminder_id, user_id, chat_id, username, full_name, chat_type, r_type, category, remind_time, remind_date, message, active = r
            text = (
                f"🆔 {reminder_id}\n"
                f"👤 {full_name} (@{username})\n"
                f"💬 Chat ID: {chat_id}\n"
                f"📦 Tür: {chat_type}\n"
                f"📌 Reminder Türü: {r_type}\n"
                f"🗂 Kategori: {category}\n"
                f"📅 Tarih/Gün: {remind_date}\n"
                f"🕒 Saat: {remind_time}\n"
                f"📝 Mesaj: {message}\n"
                f"📊 Aktif: {active}"
            )
            await update.message.reply_text(text)

    except:
        await update.message.reply_text("Kullanım:\n/admin_user USER_ID")


# ---------------------------
# MAIN
# ---------------------------

def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Hatırlatma Ekle$"), add_entry)],
        states={
            CHOOSING_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_type)],
            CHOOSING_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_category)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
            ASK_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_day)],
            ASK_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_time)],
            ASK_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_message)],
        },
        fallbacks=[MessageHandler(filters.Regex("^İptal$"), cancel_flow)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("yardim", yardim))
    app.add_handler(CommandHandler("liste", liste))
    app.add_handler(CommandHandler("duzenle_mesaj", duzenle_mesaj))
    app.add_handler(CommandHandler("duzenle_saat", duzenle_saat))
    app.add_handler(CommandHandler("duzenle_kategori", duzenle_kategori))

    app.add_handler(CommandHandler("admin_users", admin_users))
    app.add_handler(CommandHandler("admin_reminders", admin_reminders))
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("admin_user", admin_user))

    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(inline_actions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    load_jobs(app)

    logging.info("Bot başlatıldı...")
    app.run_polling()

if __name__ == "__main__":
    main()
