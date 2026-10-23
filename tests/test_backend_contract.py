"""Совместимость с backend: всё, что backend может отправить, проходит строгую валидацию сервиса.

Это важно не только для ответа: на любой 4xx backend не повторяет запрос и сразу считает резервными C#-правилами
(HttpScoringService, результат помечается RulesFallback). Значит, отказ сервиса от корректного запроса backend молча
подменил бы Python-грейд на C#-грейд, а в таблице docs/grade-comparison.md это разные исходы для клиента.
"""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
REQUESTS = Path(__file__).parent.parent / "data" / "requests"
GOOD = json.loads((REQUESTS / "demo-good.json").read_text(encoding="utf-8"))


def post(payload):
    return client.post("/api/v1/score", json=payload)


@pytest.mark.parametrize("path", sorted(REQUESTS.glob("*.json")), ids=lambda p: p.stem)
def test_backend_request_files_are_accepted_as_bytes(path):
    # Тело отправляется как есть, без пересериализации: числа вида 23.0 и 19.5, null, порядок полей backend.
    r = client.post("/api/v1/score", content=path.read_bytes(), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text


# Поля, которые backend отправляет как null (nullable в ScoringFinancials, FnsResult и ScoringChecks).
NULLABLE_FROM_BACKEND = {
    "expenses_rub": lambda x: x["company"]["financials"].update(expenses_rub=None),
    "net_profit_rub": lambda x: x["company"]["financials"].update(net_profit_rub=None),
    "fns.reported_revenue_rub": lambda x: x["checks"]["fns"].update(reported_revenue_rub=None),
    "checks.fns": lambda x: x["checks"].update(fns=None),
    "checks.fssp": lambda x: x["checks"].update(fssp=None),
    # ЕГРЮЛ и БКИ без ответа backend до скоринга не доводит (заявка уходит менеджеру), но контракт их допускает.
    "checks.egrul": lambda x: x["checks"].update(egrul=None),
    "checks.credit_bureau": lambda x: x["checks"].update(credit_bureau=None),
}


@pytest.mark.parametrize("name", NULLABLE_FROM_BACKEND)
def test_nulls_that_backend_can_send_are_accepted(name):
    payload = copy.deepcopy(GOOD)
    NULLABLE_FROM_BACKEND[name](payload)
    assert post(payload).status_code == 200


def test_only_missing_expenses_triggers_insufficient_data_in_practice():
    # Выручку и платежи по кредитам backend всегда отправляет числом (в C# они не nullable), выручка > 0 гарантирована
    # этапом Validate. Поэтому из трёх обязательных полей реально может не прийти только expenses_rub.
    payload = copy.deepcopy(GOOD)
    payload["company"]["financials"]["expenses_rub"] = None
    body = post(payload).json()
    assert (body["score"], body["risk_grade"], body["missing_data"]) == (40, "D", ["company.financials.expenses_rub"])


def test_missing_net_profit_is_derived_not_insufficient():
    # Без чистой прибыли Python берёт выручку минус расходы, а резервные C#-правила берут 10 % выручки:
    # на «Ромашке» это 7,2 млн против 4,8 млн в год. Здесь проверяем только Python.
    payload = copy.deepcopy(GOOD)
    payload["company"]["financials"]["net_profit_rub"] = None
    body = post(payload).json()
    assert body["missing_data"] == [] and "insufficient_data" not in body["warnings"]
    assert next(f for f in body["factors"] if f["code"] == "factor_debt_load")["value"] == "48%"


def test_whole_ruble_amount_from_kopecks_is_accepted():
    # Сумму с копейками backend округляет до рубля (Money.WholeRubles) и отправляет целым числом.
    payload = copy.deepcopy(GOOD)
    payload["application"]["amount_rub"] = 1_500_001
    assert post(payload).status_code == 200


def test_response_fields_backend_reads_are_present_and_typed():
    # HttpScoringService.Map: нужен score; грейд A..E; impact числом; списки могут быть пустыми.
    body = post(GOOD).json()
    assert isinstance(body["score"], int) and 0 <= body["score"] <= 100
    assert body["risk_grade"] in "ABCDE"
    assert body["probability_of_default"] is None
    assert 3 <= len(body["factors"]) <= 7
    assert all(isinstance(f["impact"], int) and f["title"] and f["client_explanation"] for f in body["factors"])
    assert isinstance(body["warnings"], list) and isinstance(body["missing_data"], list)
