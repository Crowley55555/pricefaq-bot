"""
Построение индекса ChromaDB из data/services.csv и data/faq.json.

Запуск вручную:
    python -m scripts.ingest        # обновить индекс (upsert)
    python -m scripts.ingest --all  # очистить и перестроить с нуля
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Iterable

# Позволяем запуск как `python scripts/ingest.py` из корня проекта.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import FAQ_JSON_PATH, SERVICES_CSV_PATH  # noqa: E402
from logger import get_logger  # noqa: E402
from vector_store import clear_collection, index_chunks  # noqa: E402

log = get_logger(__name__)


# ─── Чанки из CSV прайса ──────────────────────────────────────────────────────

def load_services(path: Path = SERVICES_CSV_PATH) -> list[dict]:
    """Читает services.csv и возвращает список чанков для индексации."""
    if not path.exists():
        log.warning("Прайс не найден: %s", path)
        return []

    chunks: list[dict] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            code = (row.get("code") or "").strip()
            if not code:
                continue
            service = (row.get("service") or "").strip()
            category = (row.get("category") or "other").strip()
            price_from = (row.get("price_from") or "0").strip()
            duration = (row.get("duration_min") or "0").strip()
            description = (row.get("description") or "").strip()

            text = (
                f"{service}\n"
                f"Категория: {category}\n"
                f"Цена: от {price_from} ₽\n"
                f"Длительность: {duration} мин.\n"
                f"{description}"
            )
            chunks.append({
                "id": f"service:{code}",
                "text": text,
                "metadata": {
                    "item_id": code,
                    "type": "service",
                    "category": category,
                    "service": service,
                    "price_from": int(price_from or 0),
                    "duration_min": int(duration or 0),
                },
            })
    log.info("Прайс: загружено %d услуг", len(chunks))
    return chunks


# ─── Чанки из FAQ JSON ────────────────────────────────────────────────────────

def load_faq(path: Path = FAQ_JSON_PATH) -> list[dict]:
    """Читает faq.json и возвращает список чанков для индексации."""
    if not path.exists():
        log.warning("FAQ не найден: %s", path)
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.error("FAQ: не удалось распарсить JSON — %s", exc)
        return []

    if not isinstance(raw, list):
        log.error("FAQ: ожидался список словарей, получен %s", type(raw).__name__)
        return []

    chunks: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        question = (item.get("question") or "").strip()
        answer = (item.get("answer") or "").strip()
        if not question or not answer:
            continue
        tags = item.get("tags") or []
        tags_str = ", ".join(tags) if isinstance(tags, list) else ""
        text = f"Вопрос: {question}\nОтвет: {answer}"
        chunks.append({
            "id": f"faq:{i:03d}",
            "text": text,
            "metadata": {
                "item_id": f"faq_{i:03d}",
                "type": "faq",
                "category": "faq",
                "tags": tags_str,
            },
        })
    log.info("FAQ: загружено %d вопросов", len(chunks))
    return chunks


# ─── Публичный API ────────────────────────────────────────────────────────────

def build_chunks(
    services_path: Path = SERVICES_CSV_PATH,
    faq_path: Path = FAQ_JSON_PATH,
) -> list[dict]:
    """Собирает все чанки (сервисы + FAQ) в один список."""
    return [*load_services(services_path), *load_faq(faq_path)]


def ingest_all() -> int:
    """
    Полная переиндексация: очищает коллекцию и загружает заново.
    Возвращает количество документов.
    """
    clear_collection()
    chunks = build_chunks()
    if not chunks:
        log.warning("ingest_all: нет данных для индексации")
        return 0
    return index_chunks(chunks)


def ingest_upsert() -> int:
    """Инкрементальная индексация (upsert). Возвращает количество документов."""
    chunks = build_chunks()
    if not chunks:
        log.warning("ingest_upsert: нет данных для индексации")
        return 0
    return index_chunks(chunks)


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Построение индекса базы знаний")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Очистить коллекцию и перестроить с нуля",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.all:
        count = ingest_all()
    else:
        count = ingest_upsert()
    print(f"Проиндексировано документов: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
