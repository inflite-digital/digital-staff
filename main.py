import os
import asyncio
import logging

from google import genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
You are Fadzlan Digital Staff.

You work for M Fadzlan,
Executive (Shift Superintendent),
KLIA Aviation Fuel Terminal.

Responsibilities:
- Draft professional emails
- Draft WhatsApp messages
- Manage action items
- Assist with daily operational reporting
- Track EBITS issues
- Support audit documentation
- Support project discussions

Always be concise, practical and professional.
"""

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "✅ KAFS Digital Staff is online."
    )

async def reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:

        question = update.message.text

        await update.message.chat.send_action(
            "typing"
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.6-flash",
            contents=question,
        )

        answer = response.text

        if not answer:
            answer = "No response generated."

        for i in range(0, len(answer), 4000):
            await update.message.reply_text(
                answer[i:i+4000]
            )

    except Exception as e:

        logging.exception("Gemini request failed")

        await update.message.reply_text(
            f"Error: {str(e)}"
        )

def main():

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
