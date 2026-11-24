"""Telegram bot: a thin client of POST /api/v1/chat (long polling)."""

from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.api_client import ChatApiClient, ChatApiError
from app.bot.formatting import format_chat_response
from app.config import get_settings
from app.logging_config import configure_logging
from app.schemas.chat import MAX_MESSAGE_LENGTH

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Опишите квартиру, которую ищете, обычными словами — я подберу варианты из каталога DOGMA.\n\n"
    "Примеры:\n"
    "• Найди студию в ЖК Самолёт до 7 млн, от 30 квадратов\n"
    "• Двушка в Краснодаре от 5 до 9 миллионов\n"
    "• Самая просторная трёшка в Рекорд 2"
)


async def answer_text(text: str, api: ChatApiClient) -> str:
    """Business-free handler: validate length, call the API, format the result."""
    text = text.strip()
    if not text:
        return HELP_TEXT
    if len(text) > MAX_MESSAGE_LENGTH:
        return f"Сообщение слишком длинное: максимум {MAX_MESSAGE_LENGTH} символов."
    try:
        payload = await api.chat(text)
    except ChatApiError as exc:
        return escape(exc.message)
    return format_chat_response(payload)


def build_router(api: ChatApiClient) -> Router:
    router = Router()

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def on_help(message: Message) -> None:
        await message.answer(escape(HELP_TEXT))

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        await message.answer(await answer_text(message.text or "", api))

    return router


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    token = settings.telegram_bot_token.get_secret_value()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    async with ChatApiClient(
        settings.bot_api_base_url, timeout=settings.bot_request_timeout
    ) as api:
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(api))
        logger.info("telegram bot started", extra={"api": settings.bot_api_base_url})
        try:
            await dispatcher.start_polling(bot, handle_signals=True)
        finally:
            await bot.session.close()


def run() -> None:
    asyncio.run(main())
