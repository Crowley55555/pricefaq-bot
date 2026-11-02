"""Тесты FSM сбора заявки в telegram_bot.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import telegram_bot as tg
from models import Lead


class StateStub:
    """Простой стаб FSMContext с data/state внутри словаря."""

    def __init__(self) -> None:
        self._data: dict = {}
        self._state = None

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kwargs):
        self._data.update(kwargs)

    async def set_state(self, state):
        self._state = state

    async def clear(self):
        self._data.clear()
        self._state = None


def _make_message(text: str, user_id: int = 42):
    msg = MagicMock()
    msg.text = text
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=user_id)
    return msg


@pytest.mark.asyncio
async def test_lead_phone_validation_rejects_bad_input():
    state = StateStub()
    await state.update_data(name="Иван")
    msg = _make_message("не телефон")
    await tg.lead_phone(msg, state)
    msg.answer.assert_awaited()
    # Данные не должны быть обновлены
    data = await state.get_data()
    assert "phone" not in data


@pytest.mark.asyncio
async def test_lead_phone_validation_accepts_good_input():
    state = StateStub()
    msg = _make_message("+7 999 123-45-67")
    await tg.lead_phone(msg, state)
    data = await state.get_data()
    assert data["phone"] == "+7 999 123-45-67"


@pytest.mark.asyncio
async def test_lead_time_sends_to_manager_and_writes_jsonl(tmp_path, monkeypatch):
    """Завершение FSM → bot.send_message(MANAGER_CHAT_ID) + запись в JSONL."""
    leads_path = tmp_path / "leads.jsonl"
    monkeypatch.setattr(tg, "LEADS_JSONL_PATH", leads_path)
    monkeypatch.setattr(tg, "MANAGER_CHAT_ID", "999999")

    state = StateStub()
    await state.update_data(name="Иван", phone="+7 999 123-45-67", service="Шиномонтаж")

    msg = _make_message("завтра утром", user_id=7)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await tg.lead_time(msg, state, bot)

    bot.send_message.assert_awaited_once()
    args, _ = bot.send_message.call_args
    assert args[0] == "999999"
    assert "Иван" in args[1]
    assert "+7 999 123-45-67" in args[1]
    assert "Шиномонтаж" in args[1]
    assert "завтра утром" in args[1]

    # Запись в JSONL
    assert leads_path.exists()
    line = leads_path.read_text(encoding="utf-8").strip().splitlines()[0]
    record = json.loads(line)
    assert record["name"] == "Иван"
    assert record["user_id"] == 7
    assert "created_at" in record


@pytest.mark.asyncio
async def test_notify_manager_skipped_when_chat_id_missing(monkeypatch):
    """Если MANAGER_CHAT_ID пуст — лид не шлётся, но ошибки нет."""
    monkeypatch.setattr(tg, "MANAGER_CHAT_ID", "")
    bot = MagicMock()
    bot.send_message = AsyncMock()
    lead = Lead(name="Иван", phone="+79991234567", service="Шиномонтаж", preferred_time="завтра")
    await tg._notify_manager(bot, lead)
    bot.send_message.assert_not_awaited()
