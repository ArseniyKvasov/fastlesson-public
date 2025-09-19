import json
import logging
import uuid
from decimal import Decimal

from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import LabeledPrice, PreCheckoutQuery, ContentType, KeyboardButton, ReplyKeyboardMarkup, \
    InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from asgiref.sync import sync_to_async
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from core.models import User, Payment
from fastlesson_bot.config import YOOMONEY_PROVIDER_TOKEN
from fastlesson_bot.services.rate_limit import check_rate_limit

# --- Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

router = Router()

PRODUCT_TITLE = "50 генераций FastLesson"
PRODUCT_DESCRIPTION = "Пакет — 50 генераций."
PRODUCT_PAYLOAD_PREFIX = "fastlesson_50"
PRODUCT_AMOUNT_RUB = Decimal("290.00")
PRODUCT_AMOUNT_SMALLEST = int(PRODUCT_AMOUNT_RUB * 100)
PRODUCT_QTY = 50


def _main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏠 На главную", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


def _cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отмена", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


async def _build_payment_kwargs(payment_model, user, amount, currency, provider_charge_id, payload):
    """Создает параметры для модели Payment с учетом доступных полей"""
    allowed_fields = {f.name for f in payment_model._meta.get_fields() if getattr(f, "concrete", True)}
    data = {}

    if "user" in allowed_fields:
        data["user"] = user
    elif "user_id" in allowed_fields:
        data["user_id"] = user.id

    if "amount" in allowed_fields:
        data["amount"] = amount
    if "currency" in allowed_fields:
        data["currency"] = currency
    if "payload" in allowed_fields:
        data["payload"] = payload

    for candidate in ("provider_payment_charge_id", "provider_id", "transaction_id",
                      "external_id", "kassa_id", "payment_id"):
        if candidate in allowed_fields:
            data[candidate] = provider_charge_id or ""
            break

    if "created_at" in allowed_fields:
        data["created_at"] = timezone.now()
    elif "created" in allowed_fields:
        data["created"] = timezone.now()

    return data


async def _build_shop_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Купить", callback_data="buy")
    kb.button(text="⬅️ Назад", callback_data="main_menu")
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("shop"))
async def shop_command(message: types.Message, state: FSMContext):
    await state.clear()

    try:
        default_text = (
            "Пополните генерации и продолжайте создавать задания!\n"
            "💎 50 генераций — всего за 290 ₽"
        )
        reply_markup = await _build_shop_kb()

        await message.answer(text=default_text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        logger.exception("Ошибка в shop_command", exc_info=True)
        await message.answer("Произошла ошибка при открытии магазина.")


@router.callback_query(F.data == "shop")
async def shop_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()

    try:
        default_text = (
            "Пополните генерации и продолжайте создавать задания!\n"
            "💎 50 генераций — всего за 290 ₽"
        )
        reply_markup = await _build_shop_kb()

        try:
            await callback.message.edit_text(
                text=default_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception:
            await callback.message.answer(
                text=default_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        await callback.answer()
    except Exception as e:
        logger.exception("Ошибка в shop_callback", exc_info=True)
        await callback.answer("Произошла ошибка.", show_alert=True)


@router.callback_query(F.data == "buy")
async def buy_callback(callback: types.CallbackQuery):
    try:
        chat_id = callback.from_user.id
        await callback.answer()

        try:
            await sync_to_async(check_rate_limit)(
                callback.from_user.id,
                "create_payment",
                limit=1,
                window=30
            )
        except PermissionDenied as e:
            await callback.answer(f"⚠️ {str(e)}")
            return

        # Получаем или создаем пользователя
        user, created = await sync_to_async(
            lambda: User.objects.get_or_create(
                telegram_id=callback.from_user.id,
            )
        )()

        # Создаём уникальный payload для этой транзакции
        invoice_payload = f"{PRODUCT_PAYLOAD_PREFIX}_{uuid.uuid4().hex}"

        prices = [LabeledPrice(label=PRODUCT_TITLE, amount=PRODUCT_AMOUNT_SMALLEST)]

        # Подготовка данных для чека
        provider_data = {
            "receipt": {
                "items": [
                    {
                        "description": PRODUCT_TITLE,
                        "quantity": 1,
                        "amount": {
                            "value":  prices[0].amount / 100,  # Конвертируем из копеек в рубли
                            "currency": "RUB"
                        },
                        "vat_code": 1,
                        "payment_mode": "full_payment",
                        "payment_subject": "commodity"
                    }
                ],
                "tax_system_code": 1
            }
        }

        # Отправляем инвойс
        await callback.bot.send_invoice(
            chat_id=chat_id,
            title=PRODUCT_TITLE,
            description=PRODUCT_DESCRIPTION,
            payload=invoice_payload,
            provider_token=YOOMONEY_PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
            start_parameter="fastlesson_payment",
            need_email=True,
            send_email_to_provider=True,  # Добавляем эту опцию
            provider_data=json.dumps(provider_data)  # Добавляем данные провайдера
        )

    except Exception as e:
        logger.exception("Ошибка в buy_callback", exc_info=True)
        await callback.answer("Произошла ошибка при создании платежа.", show_alert=True)


@router.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    """
    Обработка pre-checkout запроса от Telegram.
    Нужно обязательно подтвердить платеж.
    """
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    try:
        sp = message.successful_payment

        # основные поля
        provider_payment_charge_id = getattr(sp, "provider_payment_charge_id", None)
        telegram_payment_charge_id = getattr(sp, "telegram_payment_charge_id", None)
        invoice_payload = getattr(sp, "invoice_payload", None)
        total_amount = (Decimal(getattr(sp, "total_amount", 0)) / 100).quantize(Decimal("0.01"))
        currency = getattr(sp, "currency", "RUB")

        # order_info — попробуем преобразовать в dict (без падений)
        order_info = None
        order_info_obj = getattr(sp, "order_info", None)
        if order_info_obj:
            try:
                # aiogram order_info может иметь метод .to_python() или атрибуты — пробуем безопасно
                order_info = order_info_obj.to_python()
            except Exception:
                # fallback - собрать ручками
                order_info = {
                    "name": getattr(order_info_obj, "name", None),
                    "phone_number": getattr(order_info_obj, "phone_number", None),
                    "email": getattr(order_info_obj, "email", None),
                    "shipping_address": None
                }
                shipping = getattr(order_info_obj, "shipping_address", None)
                if shipping:
                    try:
                        order_info["shipping_address"] = shipping.to_python()
                    except Exception:
                        order_info["shipping_address"] = {
                            "country_code": getattr(shipping, "country_code", None),
                            "state": getattr(shipping, "state", None),
                            "city": getattr(shipping, "city", None),
                            "street_line1": getattr(shipping, "street_line1", None),
                            "street_line2": getattr(shipping, "street_line2", None),
                            "post_code": getattr(shipping, "post_code", None),
                        }

        # provider_data — если ты передаёшь provider_data в send_invoice, Telegram должен вернуть provider_payment_charge_id,
        # но провайдерские данные можно сохранить отдельно (если приходят). Попробуем взять raw provider_data из sp (если есть)
        provider_data = None
        if hasattr(sp, "provider_payment_charge_id") or hasattr(sp, "provider_data"):
            # aiogram не всегда сохраняет provider_data в успешном платеже, но если есть - сохраняем
            provider_data = getattr(sp, "provider_data", None)

        # Получаем или создаём пользователя
        def get_user():
            return User.objects.filter(telegram_id=message.from_user.id).first()

        user = await sync_to_async(get_user)()
        if not user:
            def create_user():
                return User.objects.create(telegram_id=message.from_user.id)
            user = await sync_to_async(create_user)()

        # Собираем kwargs для создания Payment
        payment_kwargs = {
            "user": user,
            "amount": total_amount,
            "currency": currency,
            "provider_charge_id": provider_payment_charge_id or None,
            "telegram_charge_id": telegram_payment_charge_id or None,
            "payload": invoice_payload or None,
            "telegram_invoice_payload": invoice_payload or None,
            "provider_data": provider_data,
            "order_info": order_info,
            "processing_chat_id": getattr(message, "chat", None) and getattr(message.chat, "id", None),
            "processing_message_id": getattr(message, "message_id", None),
            "status": "succeeded"
        }

        def create_payment():
            return Payment.objects.create(**{k: v for k, v in payment_kwargs.items() if v is not None})

        payment = await sync_to_async(create_payment)()

        # Начисляем продукт (тот же код, что у тебя был)
        success = True
        try:
            updated = False
            if hasattr(user, "remaining_generations"):
                user.remaining_generations = (user.remaining_generations or 0) + PRODUCT_QTY
                updated = True
            elif hasattr(user, "tokens"):
                user.tokens = (user.tokens or 0) + PRODUCT_QTY
                updated = True
            elif hasattr(user, "balance"):
                user.balance = (user.balance or Decimal("0.00")) + total_amount
                updated = True

            if updated:
                def save_user():
                    user.save()
                await sync_to_async(save_user)()
        except Exception:
            logger.exception("Ошибка при начислении генераций", exc_info=True)
            success = False

        # Ответ пользователю
        if success:
            text = (
                f"✅ Платёж успешно принят. Спасибо!\n"
                f"Транзакция: {provider_payment_charge_id or '—'}.\n"
                f"Вам начислено: {PRODUCT_QTY} генераций."
            )
        else:
            text = (
                "❗ Платёж прошёл, но возникла проблема при записи операции или начислении пакета.\n"
                "Мы уже работаем над этим. Если средства списались — напишите в поддержку."
            )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="На главную", callback_data="main_menu")]]
        )
        await message.answer(text, reply_markup=keyboard)

    except Exception:
        logger.exception("Ошибка в successful_payment_handler", exc_info=True)
