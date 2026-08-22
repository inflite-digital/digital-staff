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
    raise RuntimeError("TELEGRAM_TOKEN is missing from Railway Variables")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing from Railway Variables")

client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = """
You are Fadzlan's Digital Staff.

You support Fadzlan, Executive Shift Superintendent at KLIA Aviation
Fuel Terminal.

Your responsibilities include:
- Drafting concise and professional messages and emails
- Organizing operational notes and action items
- Supporting daily reporting
- Assisting with EBITS follow-ups
- Supporting audit and improvement-project documentation

Be concise, practical, professional, and factual.
Do not claim that you accessed company systems or files unless the
information was provided directly in the conversation.
"""


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "Hello Fadzlan. Digital Staff is online. "
        "Send me a message or ask me to draft something."
    )


async def reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message or not update.message.text:
        return

    question = update.message.text

    await update.message.chat.send_action("typing")

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.6-flash",
            contents=question,
            config={
                "system_instruction": SYSTEM_INSTRUCTION,
            },
        )

        answer = response.text or "I could not generate a response."

        for start_index in range(0, len(answer), 4000):
            await update.message.reply_text(
                answer[start_index:start_index + 4000]
            )

    except Exception as error:
        logging.exception("Gemini request failed")

        await update.message.reply_text(
            "I received your message, but Gemini could not process it. "
            "Please check the Railway deployment logs."
        )


def main():
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            reply,
        )
    )

    logging.info("Digital Staff is online")
    application.run_polling()


if __name__ == "__main__":
    main()
