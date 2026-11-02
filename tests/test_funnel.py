"""Тесты поведения воронки: главное меню, back, main_menu, выбор категории."""

from __future__ import annotations

import pytest

import main
from models import ChatRequest, ChatAction


@pytest.mark.asyncio
async def test_main_menu_resets_session(fake_collection, fake_llm):
    """__main_menu__ очищает сессию и возвращает первый шаг."""
    req = ChatRequest(message="__main_menu__", session_id="s1", source="web")
    resp = await main.process_message(req)
    assert resp.action == ChatAction.ASK_QUESTION
    assert resp.reply.startswith("Что вас интересует")
    assert any(b.value == "maintenance" for b in resp.buttons)


@pytest.mark.asyncio
async def test_back_from_scenario_returns_to_category(fake_collection, fake_llm):
    """Из шага сценария __back__ возвращает к выбору категории."""
    sid = "s_back"
    # Шаг 1: главное меню
    await main.process_message(ChatRequest(message="Старт", session_id=sid, source="web"))
    # Шаг 2: выбираем категорию → попадаем на подшаг
    r2 = await main.process_message(ChatRequest(message="tires", session_id=sid, source="web"))
    assert "размер колеса" in r2.reply.lower() or "что именно" in r2.reply.lower()
    # Шаг 3: __back__ → возврат на выбор категории
    r3 = await main.process_message(ChatRequest(message="__back__", session_id=sid, source="web"))
    assert r3.reply.startswith("Что вас интересует")


@pytest.mark.asyncio
async def test_category_choice_activates_scenario(fake_collection, fake_llm):
    """Выбор категории maintenance активирует её сценарий и его первый шаг."""
    sid = "s_cat"
    await main.process_message(ChatRequest(message="Старт", session_id=sid, source="web"))
    resp = await main.process_message(ChatRequest(message="maintenance", session_id=sid, source="web"))
    assert "ТО" in resp.reply or "обслуживани" in resp.reply.lower()
    labels = [b.label for b in resp.buttons]
    assert "Замена масла и фильтров" in labels
