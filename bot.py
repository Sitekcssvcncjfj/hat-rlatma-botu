import os
import sqlite3
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "/data/reminders.db"
TZ = ZoneInfo("Europe/Istanbul")

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable bulunamadı!")

# ---------------------------
# DATABASE
# ---------------------------

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            remind_time TEXT,
            remind_date TEXT,
            message TEXT NOT NULL,
            active INTEGER DEFAULT 1
        )
    """)

    conn.commit()
    conn.close()

def add_daily_reminder(chat_id, remind_time, message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reminders (chat_id, type, remind_time, message)
        VALUES (?, 'daily', ?, ?)
    """, (chat_id, remind_time, message))
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def add_once_reminder(chat_id, remind_date, remind_time, message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reminders (chat_id, type, remind_date, remind_time, message)
        VALUES (?, 'once', ?, ?, ?)
    """, (chat_id, remind_date, remind_time, message))
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def add_monthly_reminder(chat_id, day, remind_time, message):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO reminders (chat_id, type, remind_date, remind_time, message)
        VALUES (?, 'monthly', ?, ?, ?)
    """, (chat_id, str(day), remind_time, message))
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def get_all_active_reminders():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, chat_id, type, remind_time, remind_date, message
        FROM reminders
        WHERE active = 1
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_user_reminders(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, type, remind_time, remind_date, message
        FROM reminders
        WHERE chat_id = ? AND active = 1
        ORDER BY id DESC
    """, (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_reminder(reminder_id, chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM reminders
        WHERE id = ? AND chat_id = ?
    """, (reminder_id, chat_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def deactivate_once_reminder(reminder_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE reminders
        SET active = 0
        WHERE id = ?
    """, (reminder_id,))
    conn.commit()
    conn.close()

# ---------------------------
# JOB
# ---------------------------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    reminder_id = job_data["id"]
    chat_id = job_data["chat_id"]
    message = job_data["message"]
    reminder_type = job_data["type"]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⏰ Hatırlatma:\n{message}"
    )

    if reminder_type == "once":
        deactivate_once_reminder(reminder_id)

def schedule_daily_job(app, reminder_id, chat_id, remind_time, message):
    t = datetime.strptime(remind_time, "%H:%M").time().replace(tzinfo=TZ)

    app.job_queue.run_daily(
        send_reminder,
        time=t,
        data={
            "id": reminder_id,
            "chat_id": chat_id,
            "message": message,
            "type": "daily"
        },
        name=f"daily_{reminder_id}"
    )

def schedule_monthly_job(app, reminder_id, chat_id, day, remind_time, message):
    t = datetime.strptime(remind_time, "%H:%M").time().replace(tzinfo=TZ)

    app.job_queue.run_monthly(
        send_reminder,
        when=t,
        day=int(day),
        data={
            "id": reminder_id,
            "chat_id": chat_id,
            "message": message,
            "type": "monthly"
        },
        name=f"monthly_{reminder_id}"
    )

def schedule_once_job(app, reminder_id, chat_id, remind_date, remind_time, message):
    target_dt = datetime.strptime(
        f"{remind_date} {remind_time}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=TZ)

    now = datetime.now(TZ)

    if target_dt <= now:
        return

    delay = (target_dt - now).total_seconds()

    app.job_queue.run_once(
        send_reminder,
        when=delay,
        data={
            "id": reminder_id,
            "chat_id": chat_id,
            "message": message,
            "type": "once"
        },
        name=f"once_{reminder_id}"
    )

def load_jobs(app):
    reminders = get_all_active_reminders()

    for reminder in reminders:
        reminder_id, chat_id, r_type, remind_time, remind_date, message = reminder

        try:
            if r_type == "daily":
                schedule_daily_job(app, reminder_id, chat_id, remind_time, message)

            elif r_type == "monthly":
                schedule_monthly_job(app, reminder_id, chat_id, remind_date, remind_time, message)

            elif r_type == "once":
                schedule_once_job(app, reminder_id, chat_id, remind_date, remind_time, message)

        except Exception as e:
            logging.error(f"Job yüklenemedi: {reminder} | Hata: {e}")

# ---------------------------
# COMMANDS
# ---------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Merhaba, hatırlatma botuna hoş geldin.\n\n"
        "Komutlar:\n"
        "/gunluk HH:MM mesaj\n"
        "Örnek: /gunluk 14:00 İlacını iç\n\n"
        "/tarih YYYY-MM-DD HH:MM mesaj\n"
        "Örnek: /tarih 2026-04-04 09:00 Doğum günün\n\n"
        "/aylik GUN HH:MM mesaj\n"
        "Örnek: /aylik 4 09:00 Kira günü\n\n"
        "/liste\n"
        "/sil ID\n"
    )
    await update.message.reply_text(text)

async def gunluk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        remind_time = context.args[0]
        message = " ".join(context.args[1:])

        datetime.strptime(remind_time, "%H:%M")

        reminder_id = add_daily_reminder(chat_id, remind_time, message)
        schedule_daily_job(context.application, reminder_id, chat_id, remind_time, message)

        await update.message.reply_text(
            f"✅ Günlük hatırlatma eklendi.\nID: {reminder_id}\nSaat: {remind_time}\nMesaj: {message}"
        )

    except Exception as e:
        logging.error(f"/gunluk hata: {e}")
        await update.message.reply_text("Kullanım:\n/gunluk 14:00 İlacını iç")

async def tarih(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        remind_date = context.args[0]
        remind_time = context.args[1]
        message = " ".join(context.args[2:])

        target_dt = datetime.strptime(
            f"{remind_date} {remind_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TZ)

        if target_dt <= datetime.now(TZ):
            await update.message.reply_text("Geçmiş bir tarih veremezsin.")
            return

        reminder_id = add_once_reminder(chat_id, remind_date, remind_time, message)
        schedule_once_job(context.application, reminder_id, chat_id, remind_date, remind_time, message)

        await update.message.reply_text(
            f"✅ Tek seferlik hatırlatma eklendi.\nID: {reminder_id}\nTarih: {remind_date}\nSaat: {remind_time}\nMesaj: {message}"
        )

    except Exception as e:
        logging.error(f"/tarih hata: {e}")
        await update.message.reply_text("Kullanım:\n/tarih 2026-04-04 09:00 Doğum günün")

async def aylik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        day = int(context.args[0])
        remind_time = context.args[1]
        message = " ".join(context.args[2:])

        if day < 1 or day > 31:
            await update.message.reply_text("Gün 1 ile 31 arasında olmalı.")
            return

        datetime.strptime(remind_time, "%H:%M")

        reminder_id = add_monthly_reminder(chat_id, day, remind_time, message)
        schedule_monthly_job(context.application, reminder_id, chat_id, day, remind_time, message)

        await update.message.reply_text(
            f"✅ Aylık hatırlatma eklendi.\nID: {reminder_id}\nHer ayın {day}. günü {remind_time}\nMesaj: {message}"
        )

    except Exception as e:
        logging.error(f"/aylik hata: {e}")
        await update.message.reply_text("Kullanım:\n/aylik 4 09:00 Doğum günün")

async def liste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    reminders = get_user_reminders(chat_id)

    if not reminders:
        await update.message.reply_text("Aktif hatırlatma bulunamadı.")
        return

    lines = ["📋 Hatırlatmaların:\n"]
    for r in reminders:
        reminder_id, r_type, remind_time, remind_date, message = r

        if r_type == "daily":
            lines.append(f"ID {reminder_id} | Günlük | {remind_time} | {message}")
        elif r_type == "once":
            lines.append(f"ID {reminder_id} | Tek sefer | {remind_date} {remind_time} | {message}")
        elif r_type == "monthly":
            lines.append(f"ID {reminder_id} | Aylık | Her ayın {remind_date}. günü {remind_time} | {message}")

    await update.message.reply_text("\n".join(lines))

async def sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        reminder_id = int(context.args[0])

        success = delete_reminder(reminder_id, chat_id)

        for job in context.application.job_queue.jobs():
            if job.name in [f"daily_{reminder_id}", f"monthly_{reminder_id}", f"once_{reminder_id}"]:
                job.schedule_removal()

        if success:
            await update.message.reply_text(f"🗑️ Hatırlatma silindi. ID: {reminder_id}")
        else:
            await update.message.reply_text("Böyle bir hatırlatma bulunamadı.")

    except Exception as e:
        logging.error(f"/sil hata: {e}")
        await update.message.reply_text("Kullanım:\n/sil ID")

def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gunluk", gunluk))
    app.add_handler(CommandHandler("tarih", tarih))
    app.add_handler(CommandHandler("aylik", aylik))
    app.add_handler(CommandHandler("liste", liste))
    app.add_handler(CommandHandler("sil", sil))

    load_jobs(app)

    logging.info("Bot başlatıldı...")
    app.run_polling()

if __name__ == "__main__":
    main()
