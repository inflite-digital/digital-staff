import os
import sqlite3
import asyncio
import logging
from datetime import time
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

# Reduce logs that may expose the Telegram bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DB_PATH = os.getenv("DB_PATH", "memory.db")
TIMEZONE_NAME = os.getenv("TIMEZONE", "Asia/Kuala_Lumpur")
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "7"))
BRIEFING_MINUTE = int(os.getenv("BRIEFING_MINUTE", "0"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)

LOCAL_TIMEZONE = ZoneInfo(TIMEZONE_NAME)

# ----------------------------------
# DATABASE
# ----------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Existing memory schema is preserved.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def save_memory(note):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO memories (note) VALUES (?)",
        (note,),
    )

    conn.commit()
    conn.close()


def get_memories():

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, note FROM memories ORDER BY id DESC"
    )

    data = cursor.fetchall()

    conn.close()

    return data

def delete_memory(memory_id):

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM memories WHERE id = ?",
        (memory_id,)
    )

    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted > 0
# ----------------------------------
# MORNING BRIEFING
# ----------------------------------

def build_morning_briefing():
    memories = get_memories()

    message = "Good Morning Fadzlan\n\n"
    message += f"Outstanding Items ({len(memories)})\n"

    if memories:
        for index, row in enumerate(memories, start=1):
            message += f"{index}. {row[1]}\n"
    else:
        message += "No outstanding items found.\n"

    message += "\nHave a productive shift."
    return message


async def send_morning_briefing(
    context: ContextTypes.DEFAULT_TYPE,
):
    if not TELEGRAM_CHAT_ID:
        logging.warning(
            "Morning briefing skipped because TELEGRAM_CHAT_ID is missing"
        )
        return

    await context.bot.send_message(
        chat_id=int(TELEGRAM_CHAT_ID),
        text=build_morning_briefing(),
    )


async def test_briefing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    # Sends the test to the current chat, so no chat ID setup is needed.
    await update.effective_message.reply_text(
        build_morning_briefing()
    )

# ----------------------------------
# BOT COMMANDS
# ----------------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        "Digital Staff is online.\n\n"
        "Use:\n"
        "/myid to get your Telegram chat ID\n"
        "/testbriefing to preview the morning briefing\n"
        "Remember <information> to save information\n"
        "Memories to list saved information"
    )


async def myid(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        str(update.effective_chat.id)
    )

# ----------------------------------
# MESSAGE HANDLER
# ----------------------------------

async def reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    question = update.effective_message.text.strip()

    try:
        # Save memory directly to SQLite.
        if question.lower().startswith("remember"):
            note = question[8:].strip()

            if not note:
                await update.effective_message.reply_text(
                    "Usage:\nRemember follow up vendor next week"
                )
                return

            save_memory(note)
            await update.effective_message.reply_text(
                "Memory saved."
            )
            return

        # Read memory directly from SQLite, without AI additions.
        if question.lower() in (
            "memories",
            "list memories",
            "show memories",
            "list my memories",
            "show my memories",
            "show outstanding work",
            "list outstanding work",
        ):
            memories = get_memories()

            if not memories:
                await update.effective_message.reply_text(
                    "No memories found."
                )
                return

            result = "Stored Memories\n\n"

            for index, row in enumerate(memories, start=1):
                result += f"{index}. {row[1]}\n"

            await update.effective_message.reply_text(result)
            return

        # Load saved memory for normal AI conversations.
        memories = get_memories()
        memory_text = "\n".join(
            row[1] for row in memories[:20]
        )

        if not memory_text:
            memory_text = "No stored memories."

        await update.effective_message.chat.send_action("typing")

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": f"""
You are Fadzlan's digital staff.

Use the stored memories below when relevant.
Do not invent memories, tasks, dates, owners, or status.
Do not claim something was saved unless the program saved it.
When asked to retrieve stored information, only repeat information
that appears in the stored memories.
If information is unavailable, say: No information found.
For general advice or drafting, answer concisely and professionally.

Stored memories:
{memory_text}
""",
                },
                {
                    "role": "user",
                    "content": question,
                },
            ],
        )

        answer = response.choices[0].message.content

        if not answer:
            answer = "No response generated."

        for position in range(0, len(answer), 4000):
            await update.effective_message.reply_text(
                answer[position:position + 4000]
            )

    except Exception as error:
        logging.exception("Error")
        await update.effective_message.reply_text(
            f"Error:\n{str(error)}"
        )

# ----------------------------------
# MAIN
# ----------------------------------
async def delete_item(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:

        await update.effective_message.reply_text(
            "Please provide the item ID.\n\n"
            "Example:\n"
            "/deleteitem 3"
        )

        return

    try:

        memory_id = int(context.args[0])

    except ValueError:

        await update.effective_message.reply_text(
            "The item ID must be a number.\n\n"
            "Example:\n"
            "/deleteitem 3"
        )

        return

    deleted = delete_memory(memory_id)

    if deleted:

        await update.effective_message.reply_text(
            f"Item {memory_id} removed."
        )

    else:

        await update.effective_message.reply_text(
            f"Item {memory_id} was not found."
        )

def main():
    init_db()

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("testbriefing", test_briefing))
    app.add_handler(CommandHandler("deleteitem", delete_item))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            reply,
        )
    )

    # Send every day at the configured local time.
    if app.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Use python-telegram-bot[job-queue] "
            "in requirements.txt"
        )

    app.add_handler(
    CommandHandler(
        "deleteitem",
        delete_item
        )
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
        "Digital Staff is online. Morning briefing scheduled at %02d:%02d %s",
        BRIEFING_HOUR,
        BRIEFING_MINUTE,
        TIMEZONE_NAME,
    )

    app.run_polling()


if __name__ == "__main__":
    main()
