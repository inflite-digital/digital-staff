import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ----------------------------------
# CONFIG
# ----------------------------------

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.getenv("DB_PATH", "memory.db")
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "7"))
BRIEFING_MINUTE = int(os.getenv("BRIEFING_MINUTE", "0"))
MEMORY_LIMIT = int(os.getenv("MEMORY_LIMIT", "20"))
DEFAULT_MEMORY_DAYS = int(os.getenv("DEFAULT_MEMORY_DAYS", "30"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

LOCAL_TIMEZONE = ZoneInfo(TIMEZONE_NAME)

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

# ----------------------------------
# DATABASE AND MIGRATION
# ----------------------------------

def connect_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(cursor, table_name, column_name):
    columns = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(column[1] == column_name for column in columns)


def init_db():
    """Keep the existing memories table and add lifecycle fields safely."""
    with connect_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note TEXT NOT NULL
            )
            """
        )

        migrations = {
            "active": "INTEGER NOT NULL DEFAULT 1",
            "created_at": "TEXT",
            "expires_at": "TEXT",
            "category": "TEXT NOT NULL DEFAULT 'general'",
            "include_in_briefing": "INTEGER NOT NULL DEFAULT 0",
            "completed_at": "TEXT",
        }

        for column_name, definition in migrations.items():
            if not column_exists(cursor, "memories", column_name):
                cursor.execute(
                    f"ALTER TABLE memories ADD COLUMN {column_name} {definition}"
                )

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "UPDATE memories SET created_at = ? WHERE created_at IS NULL",
            (now,),
        )
        conn.commit()


def remove_expired_memories():
    now = datetime.now(timezone.utc).isoformat()
    with connect_db() as conn:
        conn.execute(
            """
            UPDATE memories
            SET active = 0
            WHERE active = 1
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now,),
        )
        conn.commit()


def save_memory(note, category="general", retention_days=None, include_in_briefing=0):
    if retention_days is None:
        retention_days = DEFAULT_MEMORY_DAYS

    created_at = datetime.now(timezone.utc)
    expires_at = None

    if retention_days > 0:
        expires_at = (created_at + timedelta(days=retention_days)).isoformat()

    with connect_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO memories
                (note, active, created_at, expires_at, category, include_in_briefing)
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (
                note,
                created_at.isoformat(),
                expires_at,
                category,
                include_in_briefing,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_active_memories(limit=None):
    remove_expired_memories()

    sql = """
        SELECT id, note, category, created_at, expires_at
        FROM memories
        WHERE active = 1
        ORDER BY id DESC
    """
    parameters = ()

    if limit is not None:
        sql += " LIMIT ?"
        parameters = (limit,)

    with connect_db() as conn:
        return conn.execute(sql, parameters).fetchall()


def deactivate_memory(memory_id):
    with connect_db() as conn:
        cursor = conn.execute(
            "UPDATE memories SET active = 0 WHERE id = ? AND active = 1",
            (memory_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def clear_active_memories():
    with connect_db() as conn:
        cursor = conn.execute(
            "UPDATE memories SET active = 0 WHERE active = 1"
        )
        conn.commit()
        return cursor.rowcount

def get_briefing_items():
    remove_expired_memories()
    with connect_db() as conn:
        return conn.execute(
            """
            SELECT id, note, created_at
            FROM memories
            WHERE active = 1 AND include_in_briefing = 1
            ORDER BY id ASC
            """
        ).fetchall()


def complete_briefing_item(memory_id):
    with connect_db() as conn:
        cursor = conn.execute(
            """
            UPDATE memories
            SET active = 0, include_in_briefing = 0, completed_at = ?
            WHERE id = ? AND active = 1 AND include_in_briefing = 1
            """,
            (datetime.now(timezone.utc).isoformat(), memory_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def clear_briefing_items():
    with connect_db() as conn:
        cursor = conn.execute(
            """
            UPDATE memories
            SET active = 0, include_in_briefing = 0, completed_at = ?
            WHERE active = 1 AND include_in_briefing = 1
            """,
            (datetime.now(timezone.utc).isoformat(),),
        )
        conn.commit()
        return cursor.rowcount

# ----------------------------------
# MORNING BRIEFING
# ----------------------------------

def build_morning_briefing():
    memories = get_briefing_items()

    lines = [
        "Good Morning Fadzlan",
        "",
        f"Outstanding Items ({len(memories)})",
    ]

    if memories:
        for index, memory in enumerate(reversed(memories), start=1):
            lines.append(f"{index}. {memory['note']}")
    else:
        lines.append("No outstanding items found.")

    lines.extend(["", "Have a productive shift."])
    return "\n".join(lines)


async def send_morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    if not TELEGRAM_CHAT_ID:
        logging.warning(
            "Morning briefing skipped because TELEGRAM_CHAT_ID is missing"
        )
        return

    await context.bot.send_message(
        chat_id=int(TELEGRAM_CHAT_ID),
        text=build_morning_briefing(),
    )


async def test_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(build_morning_briefing())

# ----------------------------------
# COMMANDS
# ----------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Digital Staff is online.\n\n"
        "Save general memory: Remember vendor contact is Raihan\n"
        "Add outstanding item: /additem Follow up vendor RCA\n"
        "List outstanding items: /items\n"
        "Complete item: /done 3\n"
        "Clear briefing list: /clearbriefing\n"
        "List general memory: /memories\n"
        "Remove memory: /forget 3\n"
        "Preview briefing: /testbriefing\n"
        "Get chat ID: /myid"
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(str(update.effective_chat.id))


async def memories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = get_active_memories()

    if not memories:
        await update.effective_message.reply_text("No active memories found.")
        return

    lines = ["Active Memories", ""]
    for memory in memories:
        expiry = "No expiry"
        if memory["expires_at"]:
            expiry_time = datetime.fromisoformat(memory["expires_at"])
            expiry = expiry_time.astimezone(LOCAL_TIMEZONE).strftime("%d %b %Y")

        lines.append(
            f"ID {memory['id']}: {memory['note']}\n"
            f"Expires: {expiry}"
        )

    await update.effective_message.reply_text("\n\n".join(lines))


async def forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Use: /forget ID\nExample: /forget 3"
        )
        return

    memory_id = int(context.args[0])

    if deactivate_memory(memory_id):
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} removed from active memory."
        )
    else:
        await update.effective_message.reply_text(
            f"Active memory ID {memory_id} was not found."
        )


async def clearall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    removed_count = clear_active_memories()
    await update.effective_message.reply_text(
        f"Removed {removed_count} item(s) from active memory."
    )

async def additem_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note = " ".join(context.args).strip()
    if not note:
        await update.effective_message.reply_text(
            "Use: /additem Contact vendor about the RCA"
        )
        return
    item_id = save_memory(
        note,
        category="outstanding",
        retention_days=0,
        include_in_briefing=1,
    )
    await update.effective_message.reply_text(
        f"Outstanding item saved. ID {item_id}."
    )


async def items_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = get_briefing_items()
    if not items:
        await update.effective_message.reply_text("No outstanding items found.")
        return
    lines = ["Outstanding Items", ""]
    for item in items:
        lines.append(f"ID {item['id']}: {item['note']}")
    await update.effective_message.reply_text("\n".join(lines))


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("Use: /done ID\nExample: /done 3")
        return
    item_id = int(context.args[0])
    if complete_briefing_item(item_id):
        await update.effective_message.reply_text(
            f"Outstanding item ID {item_id} completed and removed from briefing."
        )
    else:
        await update.effective_message.reply_text(
            f"Outstanding item ID {item_id} was not found."
        )


async def clearbriefing_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = clear_briefing_items()
    await update.effective_message.reply_text(
        f"Cleared {count} outstanding item(s) from the briefing."
    )

# ----------------------------------
# NORMAL MESSAGE HANDLER
# ----------------------------------

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = (update.effective_message.text or "").strip()

    try:
        lower_question = question.lower()

        if lower_question.startswith("remember forever "):
            note = question[len("remember forever "):].strip()
            if not note:
                await update.effective_message.reply_text(
                    "Use: Remember forever <information>"
                )
                return

            memory_id = save_memory(note, retention_days=0)
            await update.effective_message.reply_text(
                f"Permanent memory saved. ID {memory_id}."
            )
            return

        if lower_question.startswith("remember"):
            note = question[8:].strip()
            if not note:
                await update.effective_message.reply_text(
                    "Use: Remember follow up vendor next week"
                )
                return

            memory_id = save_memory(note)
            await update.effective_message.reply_text(
                f"Memory saved. ID {memory_id}. "
                f"It will remain active for {DEFAULT_MEMORY_DAYS} days."
            )
            return

        strict_retrieval_phrases = (
            "memories",
            "list memories",
            "show memories",
            "list my memories",
            "show my memories",
            "show outstanding work",
            "list outstanding work",
            "outstanding items",
        )

        if lower_question in strict_retrieval_phrases:
            await memories_command(update, context)
            return

        memories = get_active_memories(limit=MEMORY_LIMIT)
        memory_text = "\n".join(
            f"ID {memory['id']}: {memory['note']}"
            for memory in memories
        )

        if not memory_text:
            memory_text = "No active memories."

        await update.effective_message.chat.send_action("typing")

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": f"""
You are Fadzlan's digital staff.

ACTIVE MEMORY RULES:
- Treat only the active memories below as remembered facts.
- Do not revive deleted, expired, completed, or unstated items.
- Do not invent additional tasks, owners, dates, or next actions.
- When asked what is remembered or outstanding, repeat only the active memories.
- Do not add recommendations unless the user explicitly asks for advice.
- If the answer is absent from active memory, say: No information found.
- For writing and general advice, answer concisely and professionally.

Active memories:
{memory_text}
""",
                },
                {
                    "role": "user",
                    "content": question,
                },
            ],
            temperature=0.2,
            max_completion_tokens=1600,
        )

        answer = response.choices[0].message.content or "No response generated."

        for position in range(0, len(answer), 4000):
            await update.effective_message.reply_text(
                answer[position:position + 4000]
            )

    except Exception as error:
        logging.exception("Bot error")
        await update.effective_message.reply_text(f"Error:\n{error}")

# ----------------------------------
# MAIN
# ----------------------------------

def main():
    init_db()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("memories", memories_command))
    app.add_handler(CommandHandler("forget", forget_command))
    app.add_handler(CommandHandler("clearall", clearall_command))
    app.add_handler(CommandHandler("testbriefing", test_briefing))
    app.add_handler(CommandHandler("additem", additem_command))
    app.add_handler(CommandHandler("items", items_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("clearbriefing", clearbriefing_command))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            reply,
        )
    )

    if app.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. "
            "Use python-telegram-bot[job-queue] in requirements.txt"
        )

    app.job_queue.run_daily(
        send_morning_briefing,
        time=time(
            hour=BRIEFING_HOUR,
            minute=BRIEFING_MINUTE,
            tzinfo=LOCAL_TIMEZONE,
        ),
        name="daily_morning_briefing",
    )

    logging.info(
        "Digital Staff online. Briefing scheduled at %02d:%02d %s",
        BRIEFING_HOUR,
        BRIEFING_MINUTE,
        TIMEZONE_NAME,
    )

    app.run_polling()


if __name__ == "__main__":
    main()
