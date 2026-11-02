"""Тесты скрипта индексации (scripts/ingest.py)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import ingest


def test_build_chunks_from_sample_files(tmp_path):
    """ingest читает CSV и JSON и формирует корректные чанки."""
    services_csv = tmp_path / "services.csv"
    services_csv.write_text(
        "code,service,category,price_from,duration_min,description\n"
        "T1,Замена масла,maintenance,800,30,описание\n"
        "T2,Балансировка,tires,250,10,одно колесо\n",
        encoding="utf-8",
    )
    faq_json = tmp_path / "faq.json"
    faq_json.write_text(
        json.dumps([
            {"question": "Работаете в выходные?", "answer": "Да.", "tags": ["график"]},
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    chunks = ingest.build_chunks(services_csv, faq_json)
    assert len(chunks) == 3

    # Прайс
    service_chunks = [c for c in chunks if c["metadata"]["type"] == "service"]
    assert len(service_chunks) == 2
    assert service_chunks[0]["id"] == "service:T1"
    assert service_chunks[0]["metadata"]["price_from"] == 800
    assert service_chunks[0]["metadata"]["category"] == "maintenance"
    assert "Замена масла" in service_chunks[0]["text"]

    # FAQ
    faq_chunks = [c for c in chunks if c["metadata"]["type"] == "faq"]
    assert len(faq_chunks) == 1
    assert faq_chunks[0]["metadata"]["tags"] == "график"
    assert "Работаете в выходные?" in faq_chunks[0]["text"]


def test_build_chunks_tolerates_missing_files(tmp_path):
    """Отсутствующие файлы не ломают пайплайн."""
    missing_csv = tmp_path / "nope.csv"
    missing_json = tmp_path / "nope.json"
    chunks = ingest.build_chunks(missing_csv, missing_json)
    assert chunks == []


def test_build_chunks_tolerates_broken_json(tmp_path):
    """Битый JSON FAQ не ломает ingest, прайс при этом загружается."""
    services_csv = tmp_path / "services.csv"
    services_csv.write_text(
        "code,service,category,price_from,duration_min,description\n"
        "T1,Замена масла,maintenance,800,30,описание\n",
        encoding="utf-8",
    )
    broken_json = tmp_path / "faq.json"
    broken_json.write_text("not a json {", encoding="utf-8")

    chunks = ingest.build_chunks(services_csv, broken_json)
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["type"] == "service"
