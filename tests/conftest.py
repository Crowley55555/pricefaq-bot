"""
Общая обвязка для тестов:
- добавляет корень проекта в sys.path;
- гарантирует наличие минимальных .env-переменных для импорта модулей;
- предоставляет фикстуры: lightweight mock ChromaDB-коллекции и mock LLM.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Устанавливаем тестовый токен бота до импорта telegram_bot.py.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN-FOR-UNIT-TESTS")
os.environ.setdefault("MANAGER_CHAT_ID", "999999")


class FakeCollection:
    """Lightweight замена chromadb.Collection для тестов."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def count(self) -> int:
        return len(self.docs)

    def upsert(self, ids: list[str], documents: list[str], metadatas: list[dict]) -> None:
        for i, _id in enumerate(ids):
            self.docs[_id] = {"text": documents[i], "metadata": metadatas[i]}

    def delete(self, ids: list[str]) -> None:
        for _id in ids:
            self.docs.pop(_id, None)

    def get(self, include=None):
        return {"ids": list(self.docs.keys())}

    def query(self, query_texts, n_results=5, where=None):
        items = list(self.docs.items())[:n_results]
        return {
            "ids": [[k for k, _ in items]],
            "documents": [[v["text"] for _, v in items]],
            "metadatas": [[v["metadata"] for _, v in items]],
            "distances": [[0.1 for _ in items]],
        }


@pytest.fixture
def fake_collection(monkeypatch) -> FakeCollection:
    """Подменяет vector_store.get_collection и main.search на лёгкий стаб."""
    import main
    import vector_store

    col = FakeCollection()
    monkeypatch.setattr(vector_store, "_collection", col, raising=False)
    monkeypatch.setattr(vector_store, "_client", object(), raising=False)
    monkeypatch.setattr(vector_store, "get_collection", lambda: col)

    # main.py импортирует get_collection/search в свой namespace — подменяем и там.
    monkeypatch.setattr(main, "get_collection", lambda: col)

    def _fake_search(query, n_results=5, where=None):
        return vector_store.search(query, n_results=n_results, where=where)

    monkeypatch.setattr(vector_store, "search", _fake_search)
    monkeypatch.setattr(main, "search", _fake_search)
    return col


@pytest.fixture
def fake_llm(monkeypatch):
    """Подменяет llm_factory.get_llm на мок с ainvoke."""
    import llm_factory
    import main

    mock = MagicMock()
    mock.ainvoke = AsyncMock(return_value=MagicMock(content="Это тестовый ответ LLM."))
    monkeypatch.setattr(llm_factory, "_cached_llm", mock, raising=False)
    monkeypatch.setattr(llm_factory, "get_llm", lambda: mock)
    monkeypatch.setattr(main, "get_llm", lambda: mock)
    return mock


@pytest.fixture(autouse=True)
def _reset_main_sessions():
    """Между тестами чистим session storage в main.py."""
    try:
        import main
        main._sessions.clear()
    except Exception:
        pass
    yield
