"""
Telegram-бот демо-витрины «AI-консультант по прайсу и FAQ» (aiogram 3.x).

Поддерживает:
- /start → главное меню (воронка через process_message).
- Inline-кнопки воронки и навигация (Назад / Главное меню).
- FSM сбора заявки: имя → телефон → услуга → время → отправка менеджеру.
- Триггеры текста «записаться / оставить заявку / связаться» запускают FSM.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from pydantic import ValidationError

from config import (
    INTENT_TRIGGERS,
    LEADS_JSONL_PATH,
    MANAGER_CHAT_ID,
    MANAGER_CONTACTS,
    PRODUCT_TYPE_STEP,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WELCOME_TEXT,
)
from logger import get_logger
from main import process_message
from models import ButtonOption, ChatAction, ChatRequest, ChatResponse, Lead

log = get_logger(__name__)

router = Router()

_user_sessions: dict[int, str] = {}

_NAV_ROW = [
    InlineKeyboardButton(text="Назад", callback_data="__back__"),
    InlineKeyboardButton(text="Главное меню", callback_data="__main_menu__"),
]

_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-\(\)]{9,}$")


# ─── FSM: сбор заявки ─────────────────────────────────────────────────────────

class LeadStates(StatesGroup):
    name = State()
    phone = State()
    service = State()
    time = State()


def _session_id(user_id: int) -> str:
    if user_id not in _user_sessions:
        _user_sessions[user_id] = f"tg_{user_id}_{uuid.uuid4().hex[:8]}"
    return _user_sessions[user_id]


def _reset_session(user_id: int) -> None:
    _user_sessions.pop(user_id, None)


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _is_main_menu(response: ChatResponse) -> bool:
    return response.reply == PRODUCT_TYPE_STEP["question"]


def _build_inline_keyboard(
    buttons: Optional[list[ButtonOption]] = None,
    with_nav: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if buttons:
        for btn in buttons:
            cb_data = (btn.value or btn.label)[:64]
            rows.append([InlineKeyboardButton(text=btn.label, callback_data=cb_data)])
    if with_nav:
        rows.append(_NAV_ROW)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_service_card(data: dict) -> str:
    parts = []
    if data.get("service"):
        parts.append(f"<b>{data['service']}</b>")
    if data.get("code"):
        parts.append(f"Код: {data['code']}")
    if data.get("price_from") is not None:
        parts.append(f"Цена: от <b>{data['price_from']} ₽</b>")
    if data.get("duration_min") is not None:
        parts.append(f"Длительность: {data['duration_min']} мин.")
    return "\n".join(parts)


async def _send_response(target: Message | CallbackQuery, response: ChatResponse) -> None:
    """Отправляет ответ бота в чат."""
    send = target.message.answer if isinstance(target, CallbackQuery) else target.answer

    text = response.reply
    if response.action == ChatAction.SHOW_SERVICE and response.service_data:
        text = f"{response.reply}\n\n{_format_service_card(response.service_data)}"

    show_nav = not _is_main_menu(response)
    inline_kb = _build_inline_keyboard(
        response.buttons if response.buttons else None,
        with_nav=show_nav,
    )

    await send(text, parse_mode=ParseMode.HTML, reply_markup=inline_kb)


# ─── FSM: запуск и шаги ───────────────────────────────────────────────────────

async def _start_lead_flow(target: Message | CallbackQuery, state: FSMContext) -> None:
    """Инициирует сбор заявки у пользователя."""
    send = target.message.answer if isinstance(target, CallbackQuery) else target.answer
    await state.clear()
    await state.set_state(LeadStates.name)
    await send(
        "Запишу вас к мастеру. Как вас зовут?",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(LeadStates.name)
async def lead_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("Имя слишком короткое или слишком длинное. Напишите ещё раз, пожалуйста.")
        return
    await state.update_data(name=name)
    await state.set_state(LeadStates.phone)
    await message.answer("Отлично. Укажите телефон для связи (например, +7 999 123-45-67).")


@router.message(LeadStates.phone)
async def lead_phone(message: Message, state: FSMContext) -> None:
    phone = (message.text or "").strip()
    if not _PHONE_RE.match(phone):
        await message.answer(
            "Не похоже на телефон. Пример: +7 999 123-45-67. "
            "Повторите, пожалуйста."
        )
        return
    await state.update_data(phone=phone)
    await state.set_state(LeadStates.service)
    await message.answer("Какая услуга вас интересует? (например, «замена масла» или «шиномонтаж R16»)")


@router.message(LeadStates.service)
async def lead_service(message: Message, state: FSMContext) -> None:
    service = (message.text or "").strip()
    if len(service) < 2:
        await message.answer("Опишите услугу чуть подробнее, пожалуйста.")
        return
    await state.update_data(service=service)
    await state.set_state(LeadStates.time)
    await message.answer("Когда вам удобно? (например, «сегодня вечером», «завтра утром», «в выходные»)")


@router.message(LeadStates.time)
async def lead_time(message: Message, state: FSMContext, bot: Bot) -> None:
    time_text = (message.text or "").strip()
    if len(time_text) < 1 or len(time_text) > 100:
        await message.answer("Опишите время короткой фразой, пожалуйста.")
        return

    data = await state.get_data()
    try:
        lead = Lead(
            name=data["name"],
            phone=data["phone"],
            service=data["service"],
            preferred_time=time_text,
            source="telegram",
            user_id=message.from_user.id if message.from_user else None,
        )
    except ValidationError as exc:
        await message.answer(f"Не получилось сохранить заявку: {exc.errors()[0]['msg']}. Начнём заново: /start")
        await state.clear()
        return

    await _persist_lead(lead)
    await _notify_manager(bot, lead)

    await state.clear()
    await message.answer(
        "Спасибо! Заявка принята, мастер свяжется с вами в ближайшее время.\n"
        f"Если очень срочно — звоните {MANAGER_CONTACTS['phone']}.",
        reply_markup=_build_inline_keyboard(
            [ButtonOption(label="Главное меню", value="__main_menu__")],
            with_nav=False,
        ),
    )


async def _persist_lead(lead: Lead) -> None:
    """Пишет заявку в data/leads.jsonl (локально, в .gitignore)."""
    try:
        LEADS_JSONL_PATH.parent.mkdir(exist_ok=True, parents=True)
        with LEADS_JSONL_PATH.open("a", encoding="utf-8") as fh:
            record = lead.model_dump()
            record["created_at"] = datetime.now(timezone.utc).isoformat()
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.error("Не удалось записать лид: %s", exc)


async def _notify_manager(bot: Bot, lead: Lead) -> None:
    """Шлёт заявку в чат менеджера, если задан MANAGER_CHAT_ID."""
    if not MANAGER_CHAT_ID:
        log.warning("MANAGER_CHAT_ID не задан — заявка не отправлена")
        return
    text = (
        "<b>Новая заявка</b>\n"
        f"Имя: {lead.name}\n"
        f"Телефон: {lead.phone}\n"
        f"Услуга: {lead.service}\n"
        f"Время: {lead.preferred_time}\n"
        f"Источник: {lead.source}"
    )
    try:
        await bot.send_message(MANAGER_CHAT_ID, text, parse_mode=ParseMode.HTML)
    except Exception as exc:
        log.error("Не удалось отправить лид менеджеру: %s", exc)


# ─── Хендлеры команд и свободного ввода ───────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    _reset_session(message.from_user.id)
    session = _session_id(message.from_user.id)
    request = ChatRequest(message="Старт", session_id=session, source="telegram")
    response = await process_message(request)
    await _send_response(message, response)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отмена. Возвращаюсь в главное меню.")
    session = _session_id(message.from_user.id)
    request = ChatRequest(message="__main_menu__", session_id=session, source="telegram")
    response = await process_message(request)
    await _send_response(message, response)


@router.callback_query(F.data == "__back__")
async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    session = _session_id(callback.from_user.id)
    request = ChatRequest(message="__back__", session_id=session, source="telegram")
    response = await process_message(request)
    await _send_response(callback, response)


@router.callback_query(F.data == "__main_menu__")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    _reset_session(callback.from_user.id)
    session = _session_id(callback.from_user.id)
    request = ChatRequest(message="__main_menu__", session_id=session, source="telegram")
    response = await process_message(request)
    await _send_response(callback, response)


@router.callback_query(F.data == "__collect_lead__")
async def cb_collect_lead(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _start_lead_flow(callback, state)


@router.callback_query()
async def cb_funnel_step(callback: CallbackQuery, state: FSMContext) -> None:
    """Обработчик всех остальных Inline-кнопок (варианты воронки)."""
    await callback.answer()
    await state.clear()
    chosen = callback.data or ""
    session = _session_id(callback.from_user.id)
    request = ChatRequest(message=chosen, session_id=session, source="telegram")
    response = await process_message(request)
    await _send_response(callback, response)


@router.message()
async def msg_free_text(message: Message, state: FSMContext) -> None:
    """Свободный текст: триггеры → FSM лида, иначе — через process_message."""
    text = (message.text or "").strip()
    low = text.lower()

    if any(trg in low for trg in INTENT_TRIGGERS["lead"]):
        await _start_lead_flow(message, state)
        return

    session = _session_id(message.from_user.id)
    request = ChatRequest(message=text, session_id=session, source="telegram")
    response = await process_message(request)

    # Если бэкенд предлагает собрать лид — запускаем FSM
    if response.action == ChatAction.COLLECT_LEAD:
        await message.answer(response.reply)
        await _start_lead_flow(message, state)
        return

    await _send_response(message, response)


# ─── Запуск бота ──────────────────────────────────────────────────────────────

async def run_bot() -> None:
    if not TELEGRAM_BOT_TOKEN:
        log.critical("TELEGRAM_BOT_TOKEN не задан в .env!")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    try:
        await bot.set_my_description(description=TELEGRAM_WELCOME_TEXT)
        log.info("Описание бота установлено (экран первого запуска)")
    except Exception as exc:
        log.warning("Не удалось установить описание бота: %s", exc)

    log.info("Telegram-бот запущен (long-polling) …")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
