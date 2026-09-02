import os
import sqlite3
import asyncio
import logging

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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY is missing")

client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# ----------------------------------
# DATABASE
# ----------------------------------

def init_db():

    conn = sqlite3.connect("memory.db")

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_memory(note):

    conn = sqlite3.connect("memory.db")

    cursor = conn.cursor()

    cursor.execute(
        "INSERT INTO memories (note) VALUES (?)",
        (note,)
    )

    conn.commit()
    conn.close()


def get_memories():

    conn = sqlite3.connect("memory.db")

    cursor = conn.cursor()

    cursor.execute(
        "SELECT note FROM memories ORDER BY id DESC"
    )

    data = cursor.fetchall()

    conn.close()

    return data


# ----------------------------------
# BOT COMMANDS
# ----------------------------------

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "✅ Digital Staff is online."
    )


# ----------------------------------
# MESSAGE HANDLER
# ----------------------------------

async def reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    question = update.message.text.strip()

    try:

        # --------------------------
        # REMEMBER COMMAND
        # --------------------------

        if question.lower().startswith("remember"):

            note = question[8:].strip()

            if not note:

                await update.message.reply_text(
                    "Usage:\nRemember follow up vendor next week"
                )

                return

            save_memory(note)

            await update.message.reply_text(
                "✅ Memory saved."
            )

            return

        # --------------------------
        # MEMORIES COMMAND
        # --------------------------

        if question.lower() == "memories":

            memories = get_memories()

            if not memories:

                await update.message.reply_text(
                    "No memories found."
                )

                return

            result = "📌 Stored Memories\n\n"

            for idx, row in enumerate(memories, start=1):

                result += f"{idx}. {row[0]}\n"

            await update.message.reply_text(result)

            return

        # --------------------------
        # LOAD MEMORIES FOR AI
        # --------------------------

        memories = get_memories()

        memory_text = "\n".join(
            [m[0] for m in memories[:20]]
        )

        await update.message.chat.send_action(
            "typing"
        )

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": f"""
You are Fadzlan's digital staff.

CRITICAL RULES:

When the user asks to:

- list reminders
- show reminders
- list tasks
- show tasks
- list memories
- show memories
- show outstanding work

You MUST only return the information that exists in the database.

Do NOT:

- suggest additional work
- propose next actions
- make recommendations
- create extra tasks
- infer missing information
- add commentary

If information does not exist, say:

No information found.

Stored memories:

{memory_text}

Stored reminders:

{reminder_text}
"""
                },
                {
                    "role": "user",
                    "content": question
                }
            ]
        )

        answer = (
            response
            .choices[0]
            .message
            .content
        )

        if not answer:
            answer = "No response generated."

        for i in range(0, len(answer), 4000):

            await update.message.reply_text(
                answer[i:i + 4000]
            )

    except Exception as e:

        logging.exception("Error")

        await update.message.reply_text(
            f"Error:\n{str(e)}"
        )


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

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            reply
        )
    )

    logging.info(
        "Digital Staff is online"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
