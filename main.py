import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone

from groq import Groq
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIGURATION
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Prevent Telegram token from appearing repeatedly in Railway logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Railway persistent-volume path can be entered as DB_PATH.
# Without a Railway volume, the default memory.db may disappear
# after a redeployment.
DB_PATH = os.getenv("DB_PATH", "memory.db")

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b",
)

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_TOKEN is missing from Railway Variables"
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing from Railway Variables"
    )

groq_client = Groq(api_key=GROQ_API_KEY)


# =========================================================
# BOT IDENTITY
# =========================================================

SYSTEM_INSTRUCTION = """
You are Fadzlan's personal Digital Staff.

Your role is to assist with:
- Managing outstanding work and action items
- Remembering work-related information
- Drafting concise professional emails
- Drafting natural WhatsApp messages
- Summarizing operational updates
- Organizing issues, follow-ups and project information
- Retrieving relevant information from stored memories

Communication requirements:
- Be concise, practical and professional.
- Use clear headings and bullet points when useful.
- Do not claim to access company systems, emails, files or calendars.
- Only use information provided by the user or stored in memory.
- Clearly state when required information is unavailable.
"""


# =========================================================
# COMMAND REGISTRY
# =========================================================
#
# Add future commands here.
#
# Telegram does not automatically send BotFather command
# descriptions to the Python program. The instructions must be
# defined here so the bot knows what each command should do.
#

COMMANDS = {
    "outstandingwork": {
        "menu_description": "Show all outstanding work",
        "instruction": """
Review the stored memories and identify outstanding work,
open actions, unresolved issues, pending follow-ups and deadlines.

Organize the response under:
1. Urgent or time-sensitive
2. Open actions
3. Awaiting response
4. Items with unclear status

Do not invent missing owners, dates, deadlines or statuses.
If no outstanding work is stored, clearly say so.
""",
    },

    "openissues": {
        "menu_description": "Show open issues",
        "instruction": """
Review stored memories and list unresolved issues.

For each issue, show only information that is available:
- Issue
- Current status
- Required action
- Owner
- Due date

Do not invent missing information.
""",
    },

    "followups": {
        "menu_description": "Show pending follow-ups",
        "instruction": """
Review stored memories and identify items requiring follow-up.

Group the results by subject or project.
State what needs to be followed up and with whom, but only
when that information exists in memory.
""",
    },

    "draftemail": {
        "menu_description": "Draft a professional email",
        "instruction": """
Draft a concise and professional email based on the user's text
after the command.

Provide:
- Subject
- Email body

Use a firm but respectful tone unless the user requests another tone.
Do not add facts that were not supplied by the user or stored in memory.
""",
    },

    "draftwhatsapp": {
        "menu_description": "Draft a WhatsApp message",
        "instruction": """
Draft a concise, natural and professional WhatsApp message based
on the user's text after the command.

The message should sound human and direct, not overly formal.
Do not add facts that were not provided.
""",
    },

    "summary": {
        "menu_description": "Summarize stored work",
        "instruction": """
Summarize the relevant stored memories into:
- Key updates
- Outstanding actions
- Open issues
- Completed items
- Missing information

Do not assume that an item is completed unless the stored memory
explicitly says it is completed or closed.
""",
    },
}


# =========================================================
# DATABASE
# =========================================================

def get_connection():
    return sqlite3.connect(
        DB_PATH,
        timeout=20,
    )


def init_db():
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL
            )
            """
        )

        connection.commit()


def save_memory(
    telegram_user_id: int,
    note: str,
):
    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO memories (
                telegram_user_id,
                note,
                status,
                created_at
            )
            VALUES (?, ?, 'open', ?)
            """,
            (
                telegram_user_id,
                note,
                created_at,
            ),
        )

        memory_id = cursor.lastrowid
        connection.commit()

    return memory_id


def get_memories(
    telegram_user_id: int,
    limit: int = 100,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                note,
                status,
                created_at
            FROM memories
            WHERE telegram_user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                telegram_user_id,
                limit,
            ),
        )

        return cursor.fetchall()


def get_open_memories(
    telegram_user_id: int,
    limit: int = 100,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                note,
                status,
                created_at
            FROM memories
            WHERE telegram_user_id = ?
              AND status = 'open'
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                telegram_user_id,
                limit,
            ),
        )

        return cursor.fetchall()


def close_memory(
    telegram_user_id: int,
    memory_id: int,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE memories
            SET status = 'closed'
            WHERE id = ?
              AND telegram_user_id = ?
            """,
            (
                memory_id,
                telegram_user_id,
            ),
        )

        changed = cursor.rowcount
        connection.commit()

    return changed > 0


def delete_memory(
    telegram_user_id: int,
    memory_id: int,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM memories
            WHERE id = ?
              AND telegram_user_id = ?
            """,
            (
                memory_id,
                telegram_user_id,
            ),
        )

        changed = cursor.rowcount
        connection.commit()

    return changed > 0


def build_memory_text(memories):
    if not memories:
        return "No stored memories are available."

    lines = []

    for memory_id, note, status, created_at in memories:
        lines.append(
            f"Memory ID: {memory_id}\n"
            f"Status: {status}\n"
            f"Note: {note}\n"
            f"Saved at: {created_at}"
        )

    return "\n\n".join(lines)


# =========================================================
# GROQ AI
# =========================================================

def request_groq(
    user_message: str,
    memory_text: str,
    command_instruction: str = "",
):
    command_section = ""

    if command_instruction:
        command_section = f"""
The user selected a Telegram command.

Command instructions:
{command_instruction}
"""

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    SYSTEM_INSTRUCTION
                    + command_section
                    + "\n\nStored memories:\n"
                    + memory_text
                ),
            },
            {
                "role": "user",
                "content": user_message,
            },
        ],
        temperature=0.3,
        max_completion_tokens=2048,
    )

    return response.choices[0].message.content


async def send_long_message(
    update: Update,
    text: str,
):
    if not text:
        text = "No response was generated."

    for start_index in range(0, len(text), 4000):
        await update.effective_message.reply_text(
            text[start_index:start_index + 4000]
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = """
Digital Staff is online.

Use these commands:

/remember <details>
Save work information.

/memories
Show stored memories.

/outstandingwork
Review open and outstanding work.

/openissues
Review unresolved issues.

/followups
Review pending follow-ups.

/close <memory ID>
Mark a memory as closed.

/delete <memory ID>
Delete a memory.

/draftemail <details>
Draft a professional email.

/draftwhatsapp <details>
Draft a WhatsApp message.

/summary
Summarize stored work.

/help
Show command guidance.

You can also send normal messages without a command.
"""

    await update.effective_message.reply_text(
        message.strip()
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    lines = [
        "Available commands:",
        "",
        "/remember <details>",
        "Save information into memory.",
        "",
        "/memories",
        "Show all saved memories and their IDs.",
        "",
        "/outstandingwork",
        "Review outstanding work from stored memory.",
        "",
        "/openissues",
        "Review unresolved issues.",
        "",
        "/followups",
        "Review pending follow-ups.",
        "",
        "/close <memory ID>",
        "Mark a saved item as closed.",
        "",
        "/delete <memory ID>",
        "Permanently delete a saved item.",
        "",
        "/draftemail <details>",
        "Draft a professional email.",
        "",
        "/draftwhatsapp <details>",
        "Draft a WhatsApp message.",
        "",
        "/summary",
        "Summarize saved work information.",
    ]

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


async def remember_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id
    note = " ".join(context.args).strip()

    if not note:
        await update.effective_message.reply_text(
            "Please include the details after the command.\n\n"
            "Example:\n"
            "/remember Follow up with vendor regarding RCA"
        )
        return

    memory_id = save_memory(
        telegram_user_id,
        note,
    )

    await update.effective_message.reply_text(
        f"Memory saved with ID {memory_id}."
    )


async def memories_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id
    memories = get_memories(telegram_user_id)

    if not memories:
        await update.effective_message.reply_text(
            "No memories have been saved."
        )
        return

    lines = ["Stored memories:", ""]

    for memory_id, note, status, created_at in memories:
        lines.append(
            f"ID {memory_id} | {status.upper()}\n{note}\n"
        )

    await send_long_message(
        update,
        "\n".join(lines),
    )


async def close_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "Please provide the memory ID.\n\n"
            "Example:\n"
            "/close 3"
        )
        return

    try:
        memory_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "The memory ID must be a number."
        )
        return

    was_closed = close_memory(
        telegram_user_id,
        memory_id,
    )

    if was_closed:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} marked as closed."
        )
    else:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} was not found."
        )


async def delete_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "Please provide the memory ID.\n\n"
            "Example:\n"
            "/delete 3"
        )
        return

    try:
        memory_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "The memory ID must be a number."
        )
        return

    was_deleted = delete_memory(
        telegram_user_id,
        memory_id,
    )

    if was_deleted:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} deleted."
        )
    else:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} was not found."
        )


async def dynamic_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    raw_text = update.effective_message.text or ""
    command_name = raw_text.split()[0]
    command_name = command_name.split("@")[0]
    command_name = command_name.lstrip("/").lower()

    command_data = COMMANDS.get(command_name)

    if not command_data:
        await update.effective_message.reply_text(
            "Unknown command. Use /help to view available commands."
        )
        return

    command_arguments = " ".join(context.args).strip()

    memories = get_memories(
        telegram_user_id,
        limit=100,
    )

    memory_text = build_memory_text(memories)

    if command_arguments:
        user_request = (
            f"Execute /{command_name} using these additional details:\n"
            f"{command_arguments}"
        )
    else:
        user_request = (
            f"Execute the /{command_name} command using the "
            f"stored memories."
        )

    await update.effective_message.chat.send_action(
        "typing"
    )

    try:
        answer = await asyncio.to_thread(
            request_groq,
            user_request,
            memory_text,
            command_data["instruction"],
        )

        await send_long_message(
            update,
            answer,
        )

    except Exception as error:
        logging.exception(
            "Groq command request failed"
        )

        await update.effective_message.reply_text(
            f"AI request failed:\n{str(error)}"
        )


# =========================================================
# NORMAL CHAT
# =========================================================

async def normal_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_message:
        return

    question = (
        update.effective_message.text or ""
    ).strip()

    if not question:
        return

    telegram_user_id = update.effective_user.id

    memories = get_memories(
        telegram_user_id,
        limit=100,
    )

    memory_text = build_memory_text(memories)

    await update.effective_message.chat.send_action(
        "typing"
    )

    try:
        answer = await asyncio.to_thread(
            request_groq,
            question,
            memory_text,
        )

        await send_long_message(
            update,
            answer,
        )

    except Exception as error:
        logging.exception(
            "Groq chat request failed"
        )

        await update.effective_message.reply_text(
            f"AI request failed:\n{str(error)}"
        )


# =========================================================
# TELEGRAM COMMAND MENU
# =========================================================

async def configure_bot_commands(application):
    bot_commands = [
        BotCommand(
            "start",
            "Start Digital Staff",
        ),
        BotCommand(
            "remember",
            "Save work information",
        ),
        BotCommand(
            "memories",
            "Show stored memories",
        ),
        BotCommand(
            "close",
            "Mark a memory as closed",
        ),
        BotCommand(
            "delete",
            "Delete a stored memory",
        ),
        BotCommand(
            "help",
            "Show available commands",
        ),
    ]

    for command_name, command_data in COMMANDS.items():
        bot_commands.append(
            BotCommand(
                command_name,
                command_data["menu_description"],
            )
        )

    await application.bot.set_my_commands(
        bot_commands
    )


async def post_init(application):
    await configure_bot_commands(application)
    logging.info(
        "Telegram command menu configured"
    )


# =========================================================
# MAIN
# =========================================================

def main():
    init_db()

    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Fixed commands
    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "remember",
            remember_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "memories",
            memories_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "close",
            close_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "delete",
            delete_command,
        )
    )

    # AI-powered commands from the command registry
    for command_name in COMMANDS:
        application.add_handler(
            CommandHandler(
                command_name,
                dynamic_command,
            )
        )

    # Catch unknown Telegram commands
    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            dynamic_command,
        )
    )

    # Normal text chat
    application.add_handler(
