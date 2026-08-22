import os
import google.generativeai as genai

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")

async def reply(update: Update,
                context: ContextTypes.DEFAULT_TYPE):

    question = update.message.text

    response = model.generate_content(
        f"""
        You are KAFS Digital Supervisor.

        You support Fadzlan,
        Shift Superintendent
        KLIA Aviation Fuel Terminal.

        Be concise and practical.

        User:
        {question}
        """
    )

    await update.message.reply_text(
        response.text[:4000]
    )

app = ApplicationBuilder()\
    .token(TELEGRAM_TOKEN)\
    .build()

app.add_handler(
    MessageHandler(
        filters.TEXT,
        reply
    )
)

app.run_polling()
