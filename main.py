"""
FastAPI-бэкенд демо-бота «AI-консультант по прайсу и FAQ».

Реализует:
- Воронку выбора категории → подшаг → RAG-ответ.
- Семантический поиск по услугам и FAQ (ChromaDB).
- Единый эндпоинт /api/chat для веб-виджета и Telegram.
- Fallback на менеджера, если уверенность низкая или LLM недоступен.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, SystemMessage

from config import (
    FUNNEL_SCENARIOS,
    INTENT_TRIGGERS,
    MANAGER_CONTACTS,
    PRODUCT_TYPE_STEP,
    RAG_CONFIDENCE_THRESHOLD,
    STATIC_DIR,
    SYSTEM_PROMPT,
)
from llm_factory import get_llm
from logger import get_logger
from models import ButtonOption, ChatAction, ChatRequest, ChatResponse
from vector_store import get_collection, search

log = get_logger(__name__)


# ─── Хранилище сессий (in-memory) ─────────────────────────────────────────────

def _new_session() -> dict[str, Any]:
    return {
        "funnel_phase": None,       # None | "category" | "scenario"
        "scenario_key": None,
        "step_idx": 0,
        "active_filters": {},
    }


_sessions: dict[str, dict[str, Any]] = defaultdict(_new_session)


def _get_session(session_id: str) -> dict[str, Any]:
    return _sessions[session_id]


def _reset_funnel(session_id: str) -> None:
    _sessions[session_id] = _new_session()


def _get_scenario(session_id: str) -> dict:
    key = _get_session(session_id).get("scenario_key") or "_default"
    return FUNNEL_SCENARIOS.get(key, FUNNEL_SCENARIOS["_default"])


# ─── Навигация ────────────────────────────────────────────────────────────────

def _make_buttons(step_config: dict) -> list[ButtonOption]:
    return [
        ButtonOption(
            label=opt["label"],
            value=opt.get("filter_value") or opt.get("value") or opt["label"],
        )
        for opt in step_config.get("options", [])
    ]


def _goto_main_menu(session_id: str) -> ChatResponse:
    _reset_funnel(session_id)
    _get_session(session_id)["funnel_phase"] = "category"
    return ChatResponse(
        reply=PRODUCT_TYPE_STEP["question"],
        action=ChatAction.ASK_QUESTION,
        buttons=_make_buttons(PRODUCT_TYPE_STEP),
    )


def _current_step_response(session_id: str) -> ChatResponse:
    session = _get_session(session_id)
    scenario = _get_scenario(session_id)
    steps = scenario["steps"]
    if not steps:
        return _do_filtered_search_sync(session_id, "")
    idx = max(0, min(session["step_idx"], len(steps) - 1))
    session["step_idx"] = idx
    step = steps[idx]
    return ChatResponse(
        reply=step["question"],
        action=ChatAction.ASK_QUESTION,
        buttons=_make_buttons(step),
    )


def _activate_scenario(session_id: str, scenario_key: str) -> ChatResponse:
    session = _get_session(session_id)
    effective_key = scenario_key if scenario_key in FUNNEL_SCENARIOS else "_default"
    scenario = FUNNEL_SCENARIOS[effective_key]

    session["scenario_key"] = effective_key
    session["funnel_phase"] = "scenario"
    session["step_idx"] = 0
    session["active_filters"] = {"category": scenario_key} if scenario_key else {}
    for k, v in scenario.get("auto_filters", {}).items():
        session["active_filters"][k] = v

    if not scenario["steps"]:
        return _goto_free_rag(session_id)
    return _current_step_response(session_id)


def _goto_free_rag(session_id: str) -> ChatResponse:
    """Если воронка без подшагов — переходим к свободному режиму."""
    return ChatResponse(
        reply=(
            "Готов ответить по услугам и прайсу этой категории. "
            "Задайте вопрос или напишите «записаться», чтобы оставить заявку."
        ),
        action=ChatAction.ASK_QUESTION,
        buttons=[
            ButtonOption(label="Оставить заявку", value="__collect_lead__"),
            ButtonOption(label="Главное меню", value="__main_menu__"),
        ],
    )


# ─── RAG / LLM ────────────────────────────────────────────────────────────────

def _build_context(results: list[dict]) -> str:
    if not results:
        return "В базе знаний ничего не найдено по данному запросу."
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"--- Фрагмент {i} ---\n{r['text']}")
    return "\n\n".join(parts)


def _format_active_filters(session_id: str) -> str:
    active = _get_session(session_id).get("active_filters", {})
    if not active:
        return "Не заданы"
    return ", ".join(f"{k}={v}" for k, v in active.items() if v)


async def _ask_llm(user_message: str, session_id: str, context: str) -> str | None:
    """Отправка запроса в LLM. Возвращает None, если провайдер недоступен."""
    try:
        llm = get_llm()
    except RuntimeError as exc:
        log.warning("LLM недоступен: %s", exc)
        return None

    prompt = SYSTEM_PROMPT.format(
        active_filters=_format_active_filters(session_id),
        context=context,
    )
    try:
        response = await llm.ainvoke([
            SystemMessage(content=prompt),
            HumanMessage(content=user_message),
        ])
        text = (response.content or "").strip() if hasattr(response, "content") else ""
        return text or None
    except Exception as exc:
        log.error("LLM ошибка: %s", exc)
        return None


def _build_where(active_filters: dict[str, Any]) -> dict | None:
    cat = active_filters.get("category")
    if not cat or cat in ("other", "_default"):
        return None
    # Разрешаем категории прайса ИЛИ FAQ (общие ответы по графику/оплате)
    return {"$or": [{"category": cat}, {"type": "faq"}]}


def _service_data(result: dict) -> dict | None:
    meta = result.get("metadata", {}) or {}
    if meta.get("type") != "service":
        return None
    return {
        "code": meta.get("item_id"),
        "service": meta.get("service"),
        "category": meta.get("category"),
        "price_from": meta.get("price_from"),
        "duration_min": meta.get("duration_min"),
    }


async def _do_filtered_search(session_id: str, user_message: str) -> ChatResponse:
    """Основной путь: поиск по базе и ответ LLM. Fallback — на менеджера."""
    session = _get_session(session_id)
    results = search(
        user_message or _get_scenario(session_id).get("label", ""),
        n_results=5,
        where=_build_where(session["active_filters"]),
    )

    best_distance = results[0]["distance"] if results else 1.0
    confident = bool(results) and best_distance <= RAG_CONFIDENCE_THRESHOLD

    reply = await _ask_llm(user_message, session_id, _build_context(results))

    if reply is None or not confident:
        return ChatResponse(
            reply=(
                "К сожалению, по вашему запросу я не нашёл точного ответа.\n\n"
                "Хотите, я передам заявку мастеру? Напишите «записаться» "
                f"или свяжитесь напрямую: {MANAGER_CONTACTS['phone']}."
            ),
            action=ChatAction.CONTACT_MANAGER,
            buttons=[
                ButtonOption(label="Оставить заявку", value="__collect_lead__"),
                ButtonOption(label="Главное меню", value="__main_menu__"),
            ],
        )

    first_service = _service_data(results[0]) if results else None
    buttons = [
        ButtonOption(label="Оставить заявку", value="__collect_lead__"),
        ButtonOption(label="Главное меню", value="__main_menu__"),
    ]
    action = ChatAction.SHOW_SERVICE if first_service else ChatAction.ASK_QUESTION
    return ChatResponse(
        reply=reply,
        action=action,
        service_data=first_service,
        buttons=buttons,
    )


def _do_filtered_search_sync(session_id: str, user_message: str) -> ChatResponse:
    """Синхронная версия для веток без await (используется в _activate_scenario)."""
    return ChatResponse(
        reply=(
            "Задайте вопрос по этой категории: цена, запись, подробности услуги. "
            "Если нужно записаться — нажмите «Оставить заявку»."
        ),
        action=ChatAction.ASK_QUESTION,
        buttons=[
            ButtonOption(label="Оставить заявку", value="__collect_lead__"),
            ButtonOption(label="Главное меню", value="__main_menu__"),
        ],
    )


# ─── Распознавание намерений ──────────────────────────────────────────────────

def _match_any(text: str, triggers: list[str]) -> bool:
    low = text.lower()
    return any(trg in low for trg in triggers)


def _is_lead_request(text: str) -> bool:
    return _match_any(text, INTENT_TRIGGERS["lead"])


def _is_manager_request(text: str) -> bool:
    return _match_any(text, INTENT_TRIGGERS["manager"])


# ─── Основной обработчик ──────────────────────────────────────────────────────

async def process_message(request: ChatRequest) -> ChatResponse:
    session_id = request.session_id
    message = request.message.strip()
    session = _get_session(session_id)

    # ── Навигация ──
    if message == "__main_menu__":
        return _goto_main_menu(session_id)

    if message == "__back__":
        if session["funnel_phase"] == "scenario":
            idx = session["step_idx"]
            scenario = _get_scenario(session_id)
            if idx > 0:
                prev_step = scenario["steps"][idx - 1]
                session["active_filters"].pop(prev_step["step_id"], None)
                session["step_idx"] = idx - 1
                return _current_step_response(session_id)
            session["active_filters"].pop("category", None)
            session["funnel_phase"] = "category"
            session["scenario_key"] = None
            return ChatResponse(
                reply=PRODUCT_TYPE_STEP["question"],
                action=ChatAction.ASK_QUESTION,
                buttons=_make_buttons(PRODUCT_TYPE_STEP),
            )
        return _goto_main_menu(session_id)

    # ── Запрос на запись (лид) ──
    if message == "__collect_lead__" or _is_lead_request(message):
        return ChatResponse(
            reply=(
                "Отлично! Чтобы передать заявку мастеру, мне нужны:\n"
                "— ваше имя\n"
                "— телефон\n"
                "— услуга\n"
                "— удобное время\n\n"
                "В Telegram я спрошу всё по шагам. На сайте — свяжитесь по телефону "
                f"{MANAGER_CONTACTS['phone']} или напишите на {MANAGER_CONTACTS['email']}."
            ),
            action=ChatAction.COLLECT_LEAD,
            buttons=[ButtonOption(label="Главное меню", value="__main_menu__")],
        )

    # ── Запрос «позовите менеджера» ──
    if _is_manager_request(message):
        return ChatResponse(
            reply=(
                "Свяжитесь с мастером:\n"
                f"Телефон: {MANAGER_CONTACTS['phone']}\n"
                f"Почта: {MANAGER_CONTACTS['email']}\n"
                f"Адрес: {MANAGER_CONTACTS['address']}\n"
                f"Часы: {MANAGER_CONTACTS['work_hours']}"
            ),
            action=ChatAction.CONTACT_MANAGER,
            buttons=[
                ButtonOption(label="Оставить заявку", value="__collect_lead__"),
                ButtonOption(label="Главное меню", value="__main_menu__"),
            ],
        )

    # ── Старт (первый ввод) ──
    if session["funnel_phase"] is None or message in ("Старт", "/start", "start"):
        return _goto_main_menu(session_id)

    # ── Фаза: выбор категории ──
    if session["funnel_phase"] == "category":
        for opt in PRODUCT_TYPE_STEP["options"]:
            if opt["filter_value"] == message or opt["label"] == message:
                return _activate_scenario(session_id, opt["filter_value"] or "_default")
        # Свободный текст в состоянии "category" — пустим в RAG на дефолтных настройках
        return await _do_filtered_search(session_id, message)

    # ── Фаза: сценарий (шаг воронки) ──
    if session["funnel_phase"] == "scenario":
        scenario = _get_scenario(session_id)
        steps = scenario["steps"]
        idx = session["step_idx"]
        if idx < len(steps):
            step = steps[idx]
            matched = None
            for opt in step.get("options", []):
                if opt.get("filter_value") == message or opt["label"] == message:
                    matched = opt.get("filter_value", "")
                    break
            if matched is not None:
                session["active_filters"][step["step_id"]] = matched
                session["step_idx"] = idx + 1
                if session["step_idx"] < len(steps):
                    return _current_step_response(session_id)
                # Воронка закончена → RAG
                return await _do_filtered_search(session_id, scenario["label"])

        # Иначе — свободный текст: отправляем в RAG
        return await _do_filtered_search(session_id, message)

    # ── Fallback ──
    return await _do_filtered_search(session_id, message)


# ─── FastAPI lifecycle ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Запуск FastAPI-бэкенда …")
    try:
        get_llm()
    except RuntimeError as exc:
        log.critical(str(exc))

    col = get_collection()
    if col.count() == 0:
        log.info("ChromaDB пуста — попытка начальной индексации …")
        try:
            from scripts.ingest import ingest_upsert
            ingest_upsert()
        except Exception as exc:
            log.warning("Начальная индексация не удалась: %s", exc)

    yield
    log.info("FastAPI-бэкенд остановлен.")


app = FastAPI(title="Demo Price/FAQ Bot", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest) -> ChatResponse:
    log.info("Запрос [%s] session=%s: %s", request.source, request.session_id[:8], request.message[:100])
    response = await process_message(request)
    log.info("Ответ [%s] action=%s: %s", request.source, response.action.value, response.reply[:100])
    return response


@app.get("/health")
async def health_check() -> dict:
    col = get_collection()
    llm_ok = True
    try:
        get_llm()
    except RuntimeError:
        llm_ok = False
    return {"status": "ok", "llm_available": llm_ok, "chroma_documents": col.count()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
