"""Совместимость с backend: всё, что backend может отправить, проходит строгую валидацию сервиса.

Это важно не только для ответа: на любой 4xx backend не повторяет запрос и сразу считает резервными C#-правилами
(HttpScoringService, результат помечается RulesFallback). С rules-0.3 итог при этом тот же, но в карточке менеджера
источник будет RulesFallback, а в логах ошибка, поэтому корректный запрос backend сервис не отвергает никогда.
"""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import probability_of_default

client = TestClient(app)
REQUESTS = Path(__file__).parent.parent / "data" / "requests"
GOOD = json.loads((REQUESTS / "demo-good.json").read_text(encoding="utf-8"))


def post(payload):
    return client.post("/api/v1/score", json=payload)


def factors(body: dict) -> dict:
    return {f["code"]: f for f in body["factors"]}


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


# Поля, которые в C# не nullable: backend присылает их всегда, поэтому null или отсутствие = ошибка запроса.
REQUIRED_FROM_BACKEND = {
    "company.financials.revenue_rub": lambda x: x["company"]["financials"].update(revenue_rub=None),
    "company.financials.monthly_debt_payments_rub": lambda x: x["company"]["financials"].update(monthly_debt_payments_rub=None),
    "company.business_age_months": lambda x: x["company"].update(business_age_months=None),
    "application.estimated_monthly_payment_rub": lambda x: x["application"].pop("estimated_monthly_payment_rub"),
    "company.financials": lambda x: x["company"].update(financials=None),
    "company": lambda x: x.pop("company"),
}


@pytest.mark.parametrize("field", REQUIRED_FROM_BACKEND)
def test_fields_backend_always_sends_are_required(field):
    payload = copy.deepcopy(GOOD)
    REQUIRED_FROM_BACKEND[field](payload)
    r = post(payload)
    assert r.status_code == 400
    assert field in r.json()["details"]


def test_missing_expenses_skip_the_factor_with_warning():
    # Нет расходов: фактор expense_ratio не считается, предупреждение expenses_missing, остальное как обычно.
    payload = copy.deepcopy(GOOD)
    payload["company"]["financials"]["expenses_rub"] = None
    body = post(payload).json()
    assert (body["score"], body["risk_grade"]) == (95, "A")
    assert "expense_ratio" not in factors(body) and body["warnings"] == ["expenses_missing"]
    assert body["missing_data"] == ["company.financials.expenses_rub"]


def test_missing_net_profit_is_revenue_minus_expenses():
    # Без чистой прибыли берётся выручка − расходы (у «Ромашки» те же 7,2 млн), а без расходов 10 % выручки (4,8 млн).
    payload = copy.deepcopy(GOOD)
    payload["company"]["financials"]["net_profit_rub"] = None
    body = post(payload).json()
    assert factors(body)["debt_load"]["value"] == "0.48" and body["warnings"] == ["net_profit_missing"]
    payload["company"]["financials"]["expenses_rub"] = None
    body = post(payload).json()
    assert factors(body)["debt_load"]["value"] == "0.72" and factors(body)["debt_load"]["impact"] == -25
    assert body["warnings"] == ["expenses_missing", "net_profit_missing"]


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
    assert body["probability_of_default"] == probability_of_default(body["score"]) == 0.035
    assert 3 <= len(body["factors"]) <= 8
    assert all(isinstance(f["impact"], int) and f["title"] and f["client_explanation"] for f in body["factors"])
    assert isinstance(body["warnings"], list) and isinstance(body["missing_data"], list)


def test_factor_codes_have_no_prefix():
    # Обновлённый контракт: коды как у резервных правил; backend сам добавляет factor_ в причины решения.
    allowed = {"business_age", "revenue_to_amount", "expense_ratio", "debt_load", "credit_history", "tax_debt",
               "enforcement", "collateral"}
    for path in REQUESTS.glob("*.json"):
        body = post(json.loads(path.read_text(encoding="utf-8"))).json()
        assert {f["code"] for f in body["factors"]} <= allowed
        assert not any(f["code"].startswith("factor_") for f in body["factors"])
