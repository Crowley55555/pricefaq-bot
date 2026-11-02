"""Тесты Pydantic-моделей: ChatRequest, ChatResponse, Lead."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import ChatAction, ChatRequest, ChatResponse, Lead


def test_chat_request_minimal():
    req = ChatRequest(message="Привет", session_id="s1")
    assert req.source == "web"
    assert req.message == "Привет"


def test_chat_response_defaults():
    resp = ChatResponse(reply="hi")
    assert resp.action == ChatAction.ASK_QUESTION
    assert resp.buttons == []
    assert resp.service_data is None
    assert resp.services is None


def test_lead_valid_phone_formats():
    for phone in ["+7 999 123-45-67", "89991234567", "+7(999)123-45-67"]:
        lead = Lead(
            name="Иван",
            phone=phone,
            service="Шиномонтаж",
            preferred_time="завтра",
        )
        assert lead.phone == phone.strip()
        assert lead.source == "telegram"


def test_lead_invalid_phone_rejected():
    with pytest.raises(ValidationError):
        Lead(name="Иван", phone="abc", service="s", preferred_time="t")


def test_lead_short_name_rejected():
    with pytest.raises(ValidationError):
        Lead(name="A", phone="+79991234567", service="s", preferred_time="t")
