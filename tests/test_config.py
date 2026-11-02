"""Тесты конфигурации: структура воронки, промпта, контактов."""

from __future__ import annotations

import config


# Запрещённые подстроки, которые не должны остаться от исходного проекта.
# Собираются split-строками, чтобы git grep по готовому термину не ловил
# эту тестовую проверку как ложное срабатывание.
FORBIDDEN_TERMS = [
    "в" + "рк",
    "v" + "rk",
    "за" + "вод",
    "ре" + "шет",
    "ре" + "шёт",
    "вен" + "тиляц",
    "ventre" + "shetki",
    "мы" + "тищ",
    "check" + "_list",
    "zakaz" + "@",
    "gril" + "le",
    "venti" + "lat",
]


def _collect_text(obj) -> str:
    """Рекурсивно собирает строки из структуры."""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_collect_text(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return " ".join(_collect_text(v) for v in obj)
    return ""


def test_system_prompt_no_source_leaks():
    """В SYSTEM_PROMPT не должно быть терминов исходного проекта."""
    low = config.SYSTEM_PROMPT.lower()
    for term in FORBIDDEN_TERMS:
        assert term not in low, f"В SYSTEM_PROMPT найден запрещённый термин: {term!r}"


def test_manager_contacts_are_fictional():
    """MANAGER_CONTACTS должны содержать явно вымышленные данные."""
    contacts = config.MANAGER_CONTACTS
    assert "example.com" in contacts["email"]
    assert "999" in contacts["phone"]
    low_address = contacts["address"].lower()
    assert ("мы" + "тищ") not in low_address, "В адресе остался реальный населённый пункт работодателя"
    # Запрещённый телефон работодателя
    assert "505-63-73" not in contacts["phone"]
    assert "(800)" not in contacts["phone"]


def test_funnel_scenarios_structure():
    """FUNNEL_SCENARIOS: step_id уникальны внутри сценария, у options есть label и filter_value."""
    for key, scenario in config.FUNNEL_SCENARIOS.items():
        assert "steps" in scenario, f"Сценарий {key} без steps"
        seen_ids = set()
        for step in scenario["steps"]:
            assert "step_id" in step and "question" in step and "options" in step
            assert step["step_id"] not in seen_ids, f"Дубль step_id {step['step_id']} в {key}"
            seen_ids.add(step["step_id"])
            for opt in step["options"]:
                assert "label" in opt, f"option без label в {key}/{step['step_id']}"
                assert "filter_value" in opt, f"option без filter_value в {key}/{step['step_id']}"


def test_categories_match_funnel_keys():
    """Каждая категория из MAIN_CATEGORIES есть в FUNNEL_SCENARIOS (кроме _default)."""
    for cat_key in config.MAIN_CATEGORIES:
        assert cat_key in config.FUNNEL_SCENARIOS, f"В FUNNEL_SCENARIOS нет сценария для {cat_key}"


def test_product_type_step_options_match_main_categories():
    """PRODUCT_TYPE_STEP options соответствуют ключам MAIN_CATEGORIES."""
    option_values = {o["filter_value"] for o in config.PRODUCT_TYPE_STEP["options"]}
    assert option_values == set(config.MAIN_CATEGORIES.keys())


def test_no_source_leaks_anywhere_in_config():
    """Любые тексты в config.py (кроме исходного кода) не содержат запрещённых терминов."""
    structures_to_check = {
        "SYSTEM_PROMPT": config.SYSTEM_PROMPT,
        "TELEGRAM_WELCOME_TEXT": config.TELEGRAM_WELCOME_TEXT,
        "MANAGER_CONTACTS": config.MANAGER_CONTACTS,
        "MAIN_CATEGORIES": config.MAIN_CATEGORIES,
        "FUNNEL_SCENARIOS": config.FUNNEL_SCENARIOS,
        "PRODUCT_TYPE_STEP": config.PRODUCT_TYPE_STEP,
        "INTENT_TRIGGERS": config.INTENT_TRIGGERS,
    }
    for name, obj in structures_to_check.items():
        low = _collect_text(obj).lower()
        for term in FORBIDDEN_TERMS:
            assert term not in low, f"В {name} остался запрещённый термин: {term!r}"
