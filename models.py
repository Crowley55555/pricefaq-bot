"""
Pydantic-модели данных проекта.

Используются API-слоем, базой знаний и FSM сбора заявки
для единообразной валидации и сериализации.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ─── Карточка услуги ───────────────────────────────────────────────────────────

class Service(BaseModel):
    """Одна позиция из прайса автосервиса."""

    code: str = Field(..., description="Внутренний артикул услуги")
    service: str = Field(..., description="Название услуги")
    category: str = Field(..., description="Категория: ТО, шиномонтаж и т.д.")
    price_from: int = Field(..., ge=0, description="Цена от, руб.")
    duration_min: int = Field(..., ge=0, description="Длительность, мин.")
    description: str = Field("", description="Короткое описание")


# ─── FAQ ───────────────────────────────────────────────────────────────────────

class FaqItem(BaseModel):
    """Один вопрос-ответ из базы FAQ."""

    question: str
    answer: str
    tags: list[str] = Field(default_factory=list)


# ─── Заявка (лид) ──────────────────────────────────────────────────────────────

_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-\(\)]{9,}$")


class Lead(BaseModel):
    """Собранная через FSM заявка клиента."""

    name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., description="Телефон в произвольном формате")
    service: str = Field(..., min_length=2, max_length=200)
    preferred_time: str = Field(..., min_length=1, max_length=100)
    source: str = Field("telegram", description="Канал: telegram | web")
    user_id: Optional[int] = Field(None, description="Telegram user id, если применимо")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not _PHONE_RE.match(value.strip()):
            raise ValueError("Телефон должен содержать 10+ цифр, допускаются +, пробелы, (), -")
        return value.strip()


# ─── API: запрос / ответ ──────────────────────────────────────────────────────

class ChatAction(str, Enum):
    ASK_QUESTION = "ask_question"
    SHOW_SERVICE = "show_service"
    CONTACT_MANAGER = "contact_manager"
    COLLECT_LEAD = "collect_lead"


class ChatRequest(BaseModel):
    message: str
    session_id: str
    source: str = "web"  # web | telegram


class ButtonOption(BaseModel):
    label: str
    value: str


class ChatResponse(BaseModel):
    reply: str
    action: ChatAction = ChatAction.ASK_QUESTION
    service_data: Optional[dict] = None
    services: Optional[list[dict]] = None
    buttons: list[ButtonOption] = Field(default_factory=list)
