"""Telegram bot application setup and lifecycle."""
import logging

from telegram.ext import Application

from app.config import settings

logger = logging.getLogger(__name__)

_app: Application | None = None


def get_application() -> Application:
    from app.bot.handlers import build_handlers

    application = Application.builder().token(settings.telegram_bot_token).build()
    for handler in build_handlers():
        application.add_handler(handler)
    return application


async def start_bot() -> None:
    global _app
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, bot disabled")
        return
    _app = get_application()
    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started (polling)")


async def stop_bot() -> None:
    global _app
    if _app is None:
        return
    await _app.updater.stop()
    await _app.stop()
    await _app.shutdown()
    logger.info("Telegram bot stopped")
