import asyncio
import logging
import threading
from typing import Optional, Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramAPIError

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import Chat

# упрощённый импорт токена
from fastlesson_bot.config import BOT_TOKEN as token

logger = logging.getLogger(__name__)


async def _aio_send_message(
    token: str,
    chat_id: int,
    text: str,
    reply_markup: Any = None,
    parse_mode: str = "Markdown"
) -> bool:
    """
    Асинхронная отправка через aiogram. Закрывает сессию бота в конце.
    Возвращает True при успехе, False при исключении.
    """
    bot = Bot(token=token)
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
            parse_mode=parse_mode,  # 👈 добавили
        )
        return True
    finally:
        try:
            await bot.session.close()
        except Exception as exc:
            logger.debug("Error closing bot session: %s", exc)


def _run_coro_in_thread(coro):
    """
    Запускает coroutine в новом event loop внутри отдельного потока и возвращает результат.
    Защищает от ошибок типа 'Event loop is closed'.
    """
    result = {}
    exc = {}

    def target():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result["value"] = loop.run_until_complete(coro)
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()
        except Exception as e:
            exc["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join()

    if "error" in exc:
        raise exc["error"]
    return result.get("value")


def send_message_to_user(user, text: str,
                         button_text: Optional[str] = None,
                         button_command: Optional[str] = None,
                         button_url: Optional[str] = None,
                         storage: Optional[BaseStorage] = None,
                         reset_fsm: bool = False) -> bool:
    """
    Синхронная оболочка для отправки сообщения пользователю через aiogram.
    Сообщения отправляются в формате Markdown и всегда содержат кнопку "На главную".
    Работает в shell и в Celery.
    При reset_fsm=True очищает состояние пользователя.
    """
    chat_id = getattr(user, "telegram_id", None)
    if not chat_id:
        logger.info("send_message_to_user: user %s has no telegram_id", getattr(user, "id", "unknown"))
        return False

    # клавиатура
    buttons = []
    if button_text:
        try:
            if button_command:
                buttons.append([InlineKeyboardButton(text=button_text, callback_data=button_command)])
            elif button_url:
                buttons.append([InlineKeyboardButton(text=button_text, url=button_url)])
        except Exception:
            logger.exception("Failed to build custom button for user %s", getattr(user, "id", "unknown"))

    buttons.append([InlineKeyboardButton(text="🏠 На главную", callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        # отправка сообщения
        success = _run_coro_in_thread(
            _aio_send_message(
                token=token,
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )
        )

        # сброс FSM, если указано
        if reset_fsm and storage:
            async def _reset_state():
                state = FSMContext(
                    storage=storage,
                    chat=Chat(id=chat_id, type="private"),
                    user=user.id
                )
                await state.clear()
                await state.finish()

            _run_coro_in_thread(_reset_state())
            logger.info("FSM state cleared for user %s", getattr(user, "id", "unknown"))

        return bool(success)

    except Exception as e:
        logger.exception("Unexpected error for user %s: %s", getattr(user, "id", "unknown"), e)
        return False
