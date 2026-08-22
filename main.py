import os
import google.generativeai as genai

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters
)

TELEGRAM_TOKEN = os.getenv("8918758371:AAGI59NhdznEGM3HW4CNByD51am4CpFq8uw")
GEMINI_API_KEY = os.getenv("AQ.Ab8RN6KioZmp_shgRunNIxlEq6VLKiPsQ8qF2QRxwKXSIljPZg")

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
