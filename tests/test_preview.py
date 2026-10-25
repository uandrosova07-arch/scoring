"""Предварительная подсказка (POST /api/v1/preview): отдельно от кредитного решения, согласована с оценкой и политикой backend."""
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Эти тесты проверяют rules-0.3 (резервные правила backend); logreg-synthetic-0.1 — tests/test_model.py.
app = create_app("rules-0.3")

ROOT = Path(__file__).parent.parent
client = TestClient(app)
DEMO = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
FIN_CODES = {"business_age", "revenue_to_amount", "expense_ratio", "debt_load"}

sys.path.insert(0, str(ROOT / "scripts"))
import backend_reference as br  # noqa: E402


def preview_from(score_request: dict, with_payment: bool = False, min_amount: int = 300_000) -> dict:
    """Форма заявки на тех же данных, что и запрос к оценке; платёж по умолчанию считает сам preview по ставке."""
    a, c = score_request["application"], score_request["company"]
    body = {
        "amount_rub": a["amount_rub"], "term_months": a["term_months"],
        "product": {"code": a["product"]["code"], "min_amount_rub": min_amount, "base_rate_percent": a["product"]["base_rate_percent"]},
        "company": {"business_age_months": c["business_age_months"],
                    "financials": {k: c["financials"][k] for k in ("revenue_rub", "expenses_rub", "net_profit_rub", "monthly_debt_payments_rub")}},
    }
    if with_payment:
        body["estimated_monthly_payment_rub"] = a["estimated_monthly_payment_rub"]
    return body


def post(body: dict):
    return client.post("/api/v1/preview", json=body)


@pytest.mark.parametrize("company", DEMO, ids=[c["id"] for c in DEMO])
def test_preview_is_not_a_decision(company):
    body = post(preview_from(company["input"])).json()
    assert body["kind"] == "preview" and body["is_credit_decision"] is False
    assert not {"score", "risk_grade", "probability_of_default", "decision"} & set(body)
    assert body["not_evaluated"] == ["credit_history", "tax_debt", "enforcement"]
    assert "не решение банка" in body["disclaimer"]


@pytest.mark.parametrize("company", DEMO, ids=[c["id"] for c in DEMO])
@pytest.mark.parametrize("with_payment", [False, True], ids=["payment-from-rate", "payment-given"])
def test_hints_equal_score_factors_on_same_data(company, with_payment):
    # Подсказки считаются теми же функциями, что оценка: на тех же данных те же вклады, значения и тексты.
    score = client.post("/api/v1/score", json=company["input"]).json()
    body = post(preview_from(company["input"], with_payment)).json()
    fin = company["input"]["company"]["financials"]
    if fin["net_profit_rub"] is None and fin["expenses_rub"] is None:
        # Согласованное расхождение: оценка считает нагрузку по 10 % выручки с предупреждениями, preview её не показывает.
        assert body["hints"] == [f for f in score["factors"] if f["code"] in FIN_CODES - {"debt_load"}]
        assert "debt_load" in {f["code"] for f in score["factors"]} and "net_profit_missing" in score["warnings"]
        assert body["affordability"]["level"] == "InsufficientData" and body["status"] == "incomplete"
    else:
        assert body["hints"] == [f for f in score["factors"] if f["code"] in FIN_CODES]
        assert body["status"] == "ready"


@pytest.mark.parametrize("company", DEMO, ids=[c["id"] for c in DEMO])
def test_payment_from_rate_matches_backend(company):
    from fractions import Fraction

    from app.preview import annuity_payment
    a = company["input"]["application"]
    got = annuity_payment(Fraction(a["amount_rub"]), Fraction(repr(float(a["product"]["base_rate_percent"]))), a["term_months"])
    assert got == a["estimated_monthly_payment_rub"]


def test_reduced_amount_hint_for_demo():
    by = {c["id"]: post(preview_from(c["input"])).json()["amount_hint"] for c in DEMO}
    assert by["demo-medium"]["reduced_amount_rub"] == 2_400_000 and by["demo-medium"]["would_be_reduced"] is True
    assert "2 400 000 ₽" in by["demo-medium"]["explanation"]
    assert by["demo-good"]["would_be_reduced"] is False and by["demo-good"]["reduced_amount_rub"] == 1_500_000
    # Сумма в подсказке совпадает с тем, что выдала политика backend по этой заявке (грейд C, сумма снижена).
    medium = next(c for c in DEMO if c["id"] == "demo-medium")
    assert medium["decision"]["python"]["auto"]["amount"] == by["demo-medium"]["reduced_amount_rub"]


def test_reduced_amount_formula_matches_decision_policy():
    rng = random.Random(7)
    scenario = br.DEMO_SCENARIOS[1]
    for _ in range(500):
        revenue = rng.choice([rng.randint(1, 50_000_000), rng.randint(1, 1000) * 2 + 1, 10, 14])
        amount = rng.randint(100_000, 10_000_000)
        product_min = rng.choice([0, 100_000, 300_000, 1_000_000])
        req = preview_from(next(c for c in DEMO if c["id"] == "demo-medium")["input"], min_amount=product_min)
        req["amount_rub"] = amount
        req["company"]["financials"]["revenue_rub"] = revenue
        hint = post(req).json()["amount_hint"]
        # DecisionPolicy через копию C#: Math.Max(Math.Min(сумма, Math.Round(выручка * 0.25, 0)), минимум продукта)
        cap = max(min(Decimal(amount), br.net_round(Decimal(revenue) * Decimal("0.25"), 0)), Decimal(product_min))
        assert hint["reduced_amount_rub"] == int(cap), (revenue, amount, product_min)
        assert hint["would_be_reduced"] == (cap < amount)
    assert scenario.id == "demo-medium"


@pytest.mark.parametrize("revenue, cap", [(10, 2), (14, 4), (18, 4), (22, 6)])
def test_reduced_amount_uses_bankers_rounding_like_math_round(revenue, cap):
    from fractions import Fraction

    from app.preview import reduced_amount
    assert reduced_amount(Fraction(10**9), Fraction(revenue), Fraction(0)) == cap


# ---- пропуски: preview не падает, а показывает, чего не хватает ----

def test_empty_form_is_incomplete_without_amount_hint():
    body = post({"amount_rub": 1_500_000, "term_months": 12}).json()
    assert body["status"] == "incomplete" and body["hints"] == [] and body["amount_hint"] is None
    assert body["missing_data"] == ["company.business_age_months", "company.financials.revenue_rub",
                                    "company.financials.expenses_rub", "company.financials.net_profit_rub",
                                    "estimated_monthly_payment_rub", "company.financials.monthly_debt_payments_rub"]


def test_revenue_only_gives_ratio_and_amount_hint_but_stays_incomplete():
    body = post({"amount_rub": 3_000_000, "term_months": 24, "company": {"financials": {"revenue_rub": 9_600_000}}}).json()
    assert body["status"] == "incomplete"
    assert [h["code"] for h in body["hints"]] == ["revenue_to_amount"]
    assert body["amount_hint"]["reduced_amount_rub"] == 2_400_000


def test_no_profit_and_no_expenses_give_insufficient_data_not_ten_percent():
    good = next(c for c in DEMO if c["id"] == "demo-good")["input"]
    req = preview_from(good)
    req["company"]["financials"].update(expenses_rub=None, net_profit_rub=None)
    body = post(req).json()
    assert body["affordability"] == {
        "level": "InsufficientData", "debt_load": None, "monthly_profit_rub": None, "monthly_payments_rub": None,
        "profit_basis": None, "explanation": body["affordability"]["explanation"]}
    assert "10 % выручки" in body["affordability"]["explanation"]
    assert "debt_load" not in {h["code"] for h in body["hints"]} and body["status"] == "incomplete"
    # Оценка на тех же данных по-прежнему считает нагрузку: прибыль = 10 % выручки, предупреждения (rules-0.3).
    scored = client.post("/api/v1/score", json={**good, "company": {**good["company"], "financials": {
        **good["company"]["financials"], "expenses_rub": None, "net_profit_rub": None}}}).json()
    assert {f["code"]: f["value"] for f in scored["factors"]}["debt_load"] == "0.72"
    assert scored["warnings"] == ["expenses_missing", "net_profit_missing"]


def test_profit_from_revenue_minus_expenses_is_shown_with_its_basis():
    good = next(c for c in DEMO if c["id"] == "demo-good")["input"]
    req = preview_from(good)
    req["company"]["financials"]["net_profit_rub"] = None  # 48 млн − 40,8 млн = 7,2 млн
    a = post(req).json()["affordability"]
    assert (a["level"], a["profit_basis"], a["monthly_profit_rub"], a["debt_load"]) == ("Moderate", "revenue_minus_expenses", 600_000.0, 0.481)


# ---- уровни нагрузки = полосы фактора debt_load (30 / 50 / 70 %) ----

@pytest.mark.parametrize("debt, level", [
    (41_406, "Comfortable"), (41_407, "Moderate"), (161_406, "Moderate"), (161_407, "Elevated"),
    (281_406, "Elevated"), (281_407, "High"),
])
def test_levels_on_band_boundaries(debt, level):
    # Прибыль 7,2 млн в год, новый платёж 138 593: (debt + 138 593) * 12 / 7 200 000 ровно 0,3 / 0,5 / 0,7 при 41 407 / 161 407 / 281 407.
    good = next(c for c in DEMO if c["id"] == "demo-good")["input"]
    req = preview_from(good, with_payment=True)
    req["company"]["financials"]["monthly_debt_payments_rub"] = debt
    assert post(req).json()["affordability"]["level"] == level


@pytest.mark.parametrize("net", [0, -1_000_000])
def test_no_profit_is_high_with_null_load(net):
    good = next(c for c in DEMO if c["id"] == "demo-good")["input"]
    req = preview_from(good)
    req["company"]["financials"]["net_profit_rub"] = net
    a = post(req).json()["affordability"]
    assert (a["level"], a["debt_load"], a["profit_basis"]) == ("High", None, "net_profit")


def test_level_matches_debt_load_impact_on_all_shared_cases():
    # На всех случаях общего эталона, где прибыль известна, уровень preview соответствует вкладу фактора в оценке.
    cases = json.loads((ROOT / "data" / "conformance" / "rules-0.3.json").read_text(encoding="utf-8"))["cases"]
    by_impact = {10: "Comfortable", 0: "Moderate", -10: "Elevated", -25: "High"}
    checked = 0
    for c in cases:
        fin = c["input"]["company"]["financials"]
        if fin["net_profit_rub"] is None and fin["expenses_rub"] is None:
            continue
        a = post(preview_from(c["input"], with_payment=True)).json()["affordability"]
        impact = next(f["impact"] for f in c["expected"]["factors"] if f["code"] == "debt_load")
        assert a["level"] == by_impact[impact], c["id"]
        checked += 1
    assert checked >= 55


def test_no_payment_and_no_rate_skips_debt_load():
    good = next(c for c in DEMO if c["id"] == "demo-good")["input"]
    req = preview_from(good)
    req["product"].pop("base_rate_percent")
    body = post(req).json()
    assert "debt_load" not in {h["code"] for h in body["hints"]} and "estimated_monthly_payment_rub" in body["missing_data"]
    assert body["status"] == "incomplete"


@pytest.mark.parametrize("field, value", [("amount_rub", 0), ("term_months", 0), ("amount_rub", "1500000")])
def test_invalid_form_values_give_400(field, value):
    body = {"amount_rub": 1_500_000, "term_months": 12, field: value}
    r = post(body)
    assert r.status_code == 400 and field in r.json()["details"]


def test_negative_revenue_gives_400():
    r = post({"amount_rub": 1_500_000, "term_months": 12, "company": {"financials": {"revenue_rub": -1}}})
    assert r.status_code == 400 and "company.financials.revenue_rub" in r.json()["details"]


def test_preview_matches_shared_affordability_bands():
    # Тот же файл backend использует для своего предварительного подбора (уровни без баллов).
    bands = json.loads((ROOT / "data" / "conformance" / "affordability-bands-rules-0.3.json").read_text(encoding="utf-8"))["cases"]
    for row in bands:
        body = post({"amount_rub": 1_000_000, "term_months": 12, "estimated_monthly_payment_rub": row["estimated_monthly_payment_rub"],
                     "company": {"financials": {k: row[k] for k in ("revenue_rub", "expenses_rub", "net_profit_rub", "monthly_debt_payments_rub")}}}).json()
        got = {k: v for k, v in body["affordability"].items() if k != "explanation"}
        assert got == row["expected"], row["id"]
