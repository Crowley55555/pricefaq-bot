"""Интеграционные тесты FastAPI через httpx TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

import main


def _client() -> TestClient:
    # Создаём клиент без lifespan, чтобы не триггерить индексацию.
    return TestClient(main.app)


def test_health_ok(fake_collection, fake_llm):
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["llm_available"] is True
    assert "chroma_documents" in data


def test_chat_endpoint_start_shows_main_menu(fake_collection, fake_llm):
    """POST /api/chat с 'Старт' возвращает главное меню с кнопками категорий."""
    with _client() as client:
        resp = client.post("/api/chat", json={
            "message": "Старт",
            "session_id": "s_api1",
            "source": "web",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "Что вас интересует?" in data["reply"]
    labels = [b["label"] for b in data["buttons"]]
    assert "Шиномонтаж" in labels
    assert "Техническое обслуживание" in labels


def test_chat_endpoint_lead_trigger_returns_collect_action(fake_collection, fake_llm):
    """Текст 'записаться' возвращает ChatAction.COLLECT_LEAD."""
    with _client() as client:
        # Сначала инициализируем сессию
        client.post("/api/chat", json={
            "message": "Старт",
            "session_id": "s_api2",
            "source": "web",
        })
        resp = client.post("/api/chat", json={
            "message": "хочу записаться",
            "session_id": "s_api2",
            "source": "web",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "collect_lead"
