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
# SETTINGS
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Prevent the Telegram token from appearing repeatedly in logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b",
)
DB_PATH = os.getenv(
    "DB_PATH",
    "memory.db",
)

if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "TELEGRAM_TOKEN is missing from Railway Variables."
    )

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is missing from Railway Variables."
    )

groq_client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================================
# DIGITAL STAFF INSTRUCTIONS
# =========================================================

SYSTEM_PROMPT = """
You are a personal Digital Staff.

You help the user:
- Remember work information
- Review outstanding work
- Organize action items
- Draft professional emails
- Draft natural WhatsApp messages
- Summarize updates
- Track unresolved matters
- Retrieve useful details from stored memory

Rules:
- Be concise, practical and professional.
- Use only the information supplied by the user or stored in memory.
- Never invent names, dates, owners, deadlines or statuses.
- Clearly state when information is unavailable.
- Follow the selected command description carefully.
- Treat the command description as the task instruction.
"""


# =========================================================
# SQLITE DATABASE
# =========================================================

def get_connection():
    return sqlite3.connect(
        DB_PATH,
        timeout=20,
    )


def init_database():
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

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS custom_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (
                    telegram_user_id,
                    command_name
                )
            )
            """
        )

        connection.commit()


# =========================================================
# MEMORY DATABASE FUNCTIONS
# =========================================================

def save_memory(
    telegram_user_id,
    note,
):
    created_at = datetime.now(
        timezone.utc
    ).isoformat()

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
            VALUES (?, ?, ?, ?)
            """,
            (
                telegram_user_id,
                note,
                "open",
                created_at,
            ),
        )

        memory_id = cursor.lastrowid
        connection.commit()

    return memory_id


def get_memories(
    telegram_user_id,
    limit=100,
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


def close_memory(
    telegram_user_id,
    memory_id,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE memories
            SET status = 'closed'
            WHERE telegram_user_id = ?
              AND id = ?
            """,
            (
                telegram_user_id,
                memory_id,
            ),
        )

        changed = cursor.rowcount
        connection.commit()

    return changed > 0


def delete_memory(
    telegram_user_id,
    memory_id,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM memories
            WHERE telegram_user_id = ?
              AND id = ?
            """,
            (
                telegram_user_id,
                memory_id,
            ),
        )

        changed = cursor.rowcount
        connection.commit()

    return changed > 0


def format_memories_for_ai(memories):
    if not memories:
        return "No memories have been saved."

    formatted_items = []

    for memory in memories:
        memory_id = memory[0]
        note = memory[1]
        status = memory[2]
        created_at = memory[3]

        formatted_items.append(
            f"Memory ID: {memory_id}\n"
            f"Status: {status}\n"
            f"Details: {note}\n"
            f"Saved: {created_at}"
        )

    return "\n\n".join(
        formatted_items
    )


# =========================================================
# CUSTOM COMMAND DATABASE FUNCTIONS
# =========================================================

def save_custom_command(
    telegram_user_id,
    command_name,
    description,
):
    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO custom_commands (
                telegram_user_id,
                command_name,
                description,
                created_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(
                telegram_user_id,
                command_name
            )
            DO UPDATE SET
                description = excluded.description,
                created_at = excluded.created_at
            """,
            (
                telegram_user_id,
                command_name,
                description,
                created_at,
            ),
        )

        connection.commit()


def get_custom_command(
    telegram_user_id,
    command_name,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT description
            FROM custom_commands
            WHERE telegram_user_id = ?
              AND command_name = ?
            """,
            (
                telegram_user_id,
                command_name,
            ),
        )

        result = cursor.fetchone()

    if result:
        return result[0]

    return None


def get_custom_commands(
    telegram_user_id,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                command_name,
                description
            FROM custom_commands
            WHERE telegram_user_id = ?
            ORDER BY command_name ASC
            """,
            (
                telegram_user_id,
            ),
        )

        return cursor.fetchall()


def delete_custom_command(
    telegram_user_id,
    command_name,
):
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            DELETE FROM custom_commands
            WHERE telegram_user_id = ?
              AND command_name = ?
            """,
            (
                telegram_user_id,
                command_name,
            ),
        )

        changed = cursor.rowcount
        connection.commit()

    return changed > 0


# =========================================================
# GROQ
# =========================================================

def ask_groq(
    user_message,
    stored_memory,
    command_description=None,
):
    command_prompt = ""

    if command_description:
        command_prompt = (
            "\n\nThe user selected a custom command.\n"
            "Use the following command description as the "
            "main task instruction:\n\n"
            f"{command_description}"
        )

    complete_system_prompt = (
        SYSTEM_PROMPT
        + command_prompt
        + "\n\nStored work memory:\n\n"
        + stored_memory
    )

    completion = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": complete_system_prompt,
            },
            {
                "role": "user",
                "content": user_message,
            },
        ],
        temperature=0.3,
        max_completion_tokens=2048,
    )

    return completion.choices[0].message.content


# =========================================================
# TELEGRAM HELPER
# =========================================================

async def send_long_message(
    update,
    message,
):
    if not message:
        message = "No response was generated."

    for starting_position in range(
        0,
        len(message),
        4000,
    ):
        await update.effective_message.reply_text(
            message[
                starting_position:
                starting_position + 4000
            ]
        )


async def refresh_command_menu(
    application,
    telegram_user_id,
):
    custom_commands = get_custom_commands(
        telegram_user_id
    )

    command_menu = [
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
            "View saved work information",
        ),
        BotCommand(
            "addcommand",
            "Create or update a command",
        ),
        BotCommand(
            "commands",
            "View custom commands",
        ),
        BotCommand(
            "deletecommand",
            "Delete a custom command",
        ),
        BotCommand(
            "close",
            "Close a saved memory",
        ),
        BotCommand(
            "delete",
            "Delete a saved memory",
        ),
    ]

    reserved_commands = {
        "start",
        "remember",
        "memories",
        "addcommand",
        "commands",
        "deletecommand",
        "close",
        "delete",
    }

    for command_name, description in custom_commands:
        if command_name in reserved_commands:
            continue

        short_description = description.replace(
            "\n",
            " ",
        ).strip()

        if len(short_description) > 200:
            short_description = (
                short_description[:197] + "..."
            )

        command_menu.append(
            BotCommand(
                command_name,
                short_description,
            )
        )

    try:
        await application.bot.set_my_commands(
            command_menu
        )
    except Exception:
        logging.exception(
            "Unable to refresh Telegram command menu"
        )


# =========================================================
# BASIC COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    await refresh_command_menu(
        context.application,
        telegram_user_id,
    )

    message = (
        "Digital Staff is online.\n\n"
        "Create a custom command like this:\n\n"
        "/addcommand outstandingwork | "
        "Review my stored memories and show all open "
        "work, pending actions and unresolved issues.\n\n"
        "Then use:\n\n"
        "/outstandingwork\n\n"
        "The command description will be used as the "
        "AI instruction."
    )

    await update.effective_message.reply_text(
        message
    )


async def remember_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id
    note = " ".join(
        context.args
    ).strip()

    if not note:
        await update.effective_message.reply_text(
            "Add the memory after the command.\n\n"
            "Example:\n"
            "/remember Follow up with vendor regarding RCA"
        )
        return

    memory_id = save_memory(
        telegram_user_id,
        note,
    )

    await update.effective_message.reply_text(
        f"Memory saved. ID: {memory_id}"
    )


async def memories_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    memories = get_memories(
        telegram_user_id
    )

    if not memories:
        await update.effective_message.reply_text(
            "No memories have been saved."
        )
        return

    output_lines = [
        "Stored memories:",
        "",
    ]

    for memory in memories:
        memory_id = memory[0]
        note = memory[1]
        status = memory[2]

        output_lines.append(
            f"ID {memory_id} | {status.upper()}\n"
            f"{note}\n"
        )

    await send_long_message(
        update,
        "\n".join(output_lines),
    )


async def close_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "Provide the memory ID.\n\n"
            "Example:\n"
            "/close 3"
        )
        return

    try:
        memory_id = int(
            context.args[0]
        )
    except ValueError:
        await update.effective_message.reply_text(
            "The memory ID must be a number."
        )
        return

    success = close_memory(
        telegram_user_id,
        memory_id,
    )

    if success:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} marked as closed."
        )
    else:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} was not found."
        )


async def delete_memory_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "Provide the memory ID.\n\n"
            "Example:\n"
            "/delete 3"
        )
        return

    try:
        memory_id = int(
            context.args[0]
        )
    except ValueError:
        await update.effective_message.reply_text(
            "The memory ID must be a number."
        )
        return

    success = delete_memory(
        telegram_user_id,
        memory_id,
    )

    if success:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} deleted."
        )
    else:
        await update.effective_message.reply_text(
            f"Memory ID {memory_id} was not found."
        )


# =========================================================
# CUSTOM COMMAND MANAGEMENT
# =========================================================

async def add_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    full_text = (
        update.effective_message.text or ""
    )

    command_content = full_text.replace(
        "/addcommand",
        "",
        1,
    ).strip()

    if "|" not in command_content:
        await update.effective_message.reply_text(
            "Use this format:\n\n"
            "/addcommand commandname | command description\n\n"
            "Example:\n"
            "/addcommand outstandingwork | "
            "Review stored memories and show all open work, "
            "pending actions and unresolved issues."
        )
        return

    command_name, description = command_content.split(
        "|",
        1,
    )

    command_name = command_name.strip().lower()
    description = description.strip()

    command_name = command_name.lstrip("/")

    if not command_name:
        await update.effective_message.reply_text(
            "The command name is missing."
        )
        return

    if not description:
        await update.effective_message.reply_text(
            "The command description is missing."
        )
        return

    if not command_name.replace(
        "_",
        "",
    ).isalnum():
        await update.effective_message.reply_text(
            "Use only letters, numbers or underscores "
            "in the command name."
        )
        return

    reserved_commands = {
        "start",
        "remember",
        "memories",
        "addcommand",
        "commands",
        "deletecommand",
        "close",
        "delete",
    }

    if command_name in reserved_commands:
        await update.effective_message.reply_text(
            "That command name is reserved. "
            "Please choose another name."
        )
        return

    save_custom_command(
        telegram_user_id,
        command_name,
        description,
    )

    await refresh_command_menu(
        context.application,
        telegram_user_id,
    )

    await update.effective_message.reply_text(
        f"/{command_name} has been saved.\n\n"
        f"Instruction:\n{description}"
    )


async def list_commands(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    commands = get_custom_commands(
        telegram_user_id
    )

    if not commands:
        await update.effective_message.reply_text(
            "No custom commands have been created."
        )
        return

    output_lines = [
        "Custom commands:",
        "",
    ]

    for command_name, description in commands:
        output_lines.append(
            f"/{command_name}\n"
            f"{description}\n"
        )

    await send_long_message(
        update,
        "\n".join(output_lines),
    )


async def delete_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    telegram_user_id = update.effective_user.id

    if not context.args:
        await update.effective_message.reply_text(
            "Provide the command name.\n\n"
            "Example:\n"
            "/deletecommand outstandingwork"
        )
        return

    command_name = (
        context.args[0]
        .strip()
        .lower()
        .lstrip("/")
    )

    success = delete_custom_command(
        telegram_user_id,
        command_name,
    )

    if success:
        await refresh_command_menu(
            context.application,
            telegram_user_id,
        )

        await update.effective_message.reply_text(
            f"/{command_name} deleted."
        )
    else:
        await update.effective_message.reply_text(
            f"/{command_name} was not found."
        )


