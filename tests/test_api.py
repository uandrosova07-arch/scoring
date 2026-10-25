import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Эти тесты проверяют rules-0.3 (резервные правила backend); logreg-synthetic-0.1 — tests/test_model.py.
app = create_app("rules-0.3")
from app.rules import grade_for, probability_of_default

client = TestClient(app)
ROOT = Path(__file__).parent.parent
SAMPLES = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))["samples"]
BY_ID = {s["id"]: s for s in SAMPLES}
URL = "/api/v1/score"

sys.path.insert(0, str(ROOT / "scripts"))
import backend_reference as br  # noqa: E402


def stable() -> dict:
    return copy.deepcopy(BY_ID["stable-1"]["input"])


def post(payload: dict):
    return client.post(URL, json=payload)


def by_code(payload: dict) -> dict:
    return {f["code"]: f for f in post(payload).json()["factors"]}


# ---- примеры ----

def test_sample_count_and_scenarios():
    assert 8 <= len(SAMPLES) <= 12
    assert len({s["scenario"] for s in SAMPLES}) == 4


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_samples_match_expected(sample):
    r = post(sample["input"])
    assert r.status_code == 200
    body, exp = r.json(), sample["expected"]
    assert body["score"] == exp["score"]
    assert body["risk_grade"] == exp["risk_grade"]
    assert body["missing_data"] == exp["missing_data"]
    assert body["warnings"] == exp["warnings"]
    assert {f["code"]: f["impact"] for f in body["factors"]} == exp["factor_impacts"]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_samples_expected_still_match_csharp_copy(sample):
    # expected в samples.json не устарел: его по-прежнему даёт копия C# с правками rules-0.3.
    r = br.score_rules_v1(sample["input"], agreed=True)
    assert (r["score"], r["risk_grade"]) == (sample["expected"]["score"], sample["expected"]["risk_grade"])


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_same_input_same_output(sample):
    first = post(sample["input"]).content
    for _ in range(3):
        assert post(sample["input"]).content == first


# ---- соответствие контракту ----

@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_response_shape_follows_contract(sample):
    body = post(sample["input"]).json()
    assert body["request_id"] == sample["input"]["request_id"]
    assert body["model_version"] == "rules-0.3"
    assert isinstance(body["score"], int) and 0 <= body["score"] <= 100
    assert body["risk_grade"] == grade_for(body["score"])
    assert body["probability_of_default"] == probability_of_default(body["score"])
    assert 3 <= len(body["factors"]) <= 8
    key = [(-abs(f["impact"]), f["code"]) for f in body["factors"]]
    assert key == sorted(key)  # по убыванию модуля вклада, при равенстве по коду
    for f in body["factors"]:
        assert set(f) == {"code", "title", "impact", "value", "client_explanation"}
        assert not f["code"].startswith("factor_")
    assert isinstance(body["warnings"], list)


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_score_is_base_plus_factor_impacts(sample):
    # Причины соответствуют выполненным правилам: балл = 50 + сумма вкладов (с ограничением 0..100).
    body = post(sample["input"]).json()
    assert body["score"] == max(0, min(100, 50 + sum(f["impact"] for f in body["factors"])))


@pytest.mark.parametrize("score,grade", [(100, "A"), (80, "A"), (79, "B"), (65, "B"), (64, "C"),
                                         (50, "C"), (49, "D"), (35, "D"), (34, "E"), (0, "E")])
def test_grade_table_matches_backend(score, grade):
    assert grade_for(score) == grade


@pytest.mark.parametrize("score,pd", [(100, 0.02), (95, 0.035), (70, 0.11), (25, 0.245), (0, 0.32)])
def test_probability_of_default_is_backend_formula(score, pd):
    assert probability_of_default(score) == pd


# ---- факторы ----

def test_factor_values_match_independent_calculation():
    f = by_code(stable())
    s = stable()
    assert f["revenue_to_amount"]["value"] == "32.0x"   # 48 000 000 / 1 500 000
    assert f["expense_ratio"]["value"] == "0.63"        # 30 000 000 / 48 000 000 = 0.625, половина вверх
    load = (100_000 + s["application"]["estimated_monthly_payment_rub"]) * 12 / 18_000_000
    assert f["debt_load"]["value"] == f"{load:.2f}" == "0.16"
    assert f["business_age"]["value"] == "7 г."         # 84 месяца
    assert f["credit_history"]["value"] == "без просрочек" and f["tax_debt"]["value"] == "нет"


def test_estimated_payment_from_backend_is_used():
    # Платёж, который прислал backend, входит в нагрузку как есть.
    big = stable()
    big["application"]["estimated_monthly_payment_rub"] = 5_000_000  # против прибыли 1,5 млн в месяц
    assert by_code(big)["debt_load"]["impact"] == -25
    assert by_code(stable())["debt_load"]["impact"] == 10


def test_net_profit_from_request_is_used_else_derived():
    low = stable()
    low["company"]["financials"]["net_profit_rub"] = 1_000_000
    assert by_code(low)["debt_load"]["impact"] == -25
    derived = stable()
    derived["company"]["financials"]["net_profit_rub"] = None  # 48 млн − 30 млн = 18 млн, как в запросе
    assert by_code(derived)["debt_load"] == by_code(stable())["debt_load"]


@pytest.mark.parametrize("net", [0, -5])
def test_no_profit(net):
    s = stable()
    s["company"]["financials"]["net_profit_rub"] = net
    f = by_code(s)["debt_load"]
    assert f["impact"] == -25 and f["value"] == "прибыль отсутствует"


def test_high_expense_ratio_is_penalised_once():
    s = stable()
    s["company"]["financials"]["expenses_rub"] = 44_000_000  # 0.92 выручки
    f = by_code(s)["expense_ratio"]
    assert (f["impact"], f["value"]) == (-10, "0.92")


def test_collateral_adds_points():
    s = stable()
    s["application"]["collateral_offered"] = True
    assert by_code(s)["collateral"]["impact"] == 10


def test_health_and_alias():
    assert client.get("/health").json() == {"status": "ok", "model_version": "rules-0.3"}
    assert client.post("/score", json=stable()).json() == post(stable()).json()


# ---- пропуски: как у резервных правил backend ----

def test_missing_check_blocks_skip_their_factors_with_warnings():
    s = stable()
    s["checks"]["credit_bureau"] = None
    s["checks"]["fssp"] = None
    body = post(s).json()
    assert body["warnings"] == ["credit_bureau_missing", "fssp_missing"]
    assert body["missing_data"] == ["checks.credit_bureau", "checks.fssp"]
    assert {f["code"] for f in body["factors"]}.isdisjoint({"credit_history", "enforcement"})


def test_null_checks_block_means_no_sources():
    s = stable()
    s["checks"] = None
    body = post(s).json()
    assert body["warnings"] == ["credit_bureau_missing", "fns_missing", "fssp_missing"]
    assert body["missing_data"] == ["checks.egrul", "checks.fns", "checks.credit_bureau", "checks.fssp"]
    assert len(body["factors"]) == 4  # возраст, выручка к сумме, расходы, нагрузка


def test_missing_expenses_and_profit_use_ten_percent_of_revenue():
    s = stable()
    s["company"]["financials"].update(expenses_rub=None, net_profit_rub=None)
    body = post(s).json()
    assert body["warnings"] == ["expenses_missing", "net_profit_missing"]
    assert body["missing_data"] == ["company.financials.expenses_rub", "company.financials.net_profit_rub"]
    load = (100_000 + s["application"]["estimated_monthly_payment_rub"]) * 12 / (48_000_000 * 0.10)
    assert by_code(s)["debt_load"]["value"] == f"{load:.2f}"


def test_absent_optional_field_equals_null():
    a = stable()
    del a["company"]["financials"]["expenses_rub"]
    b = stable()
    b["company"]["financials"]["expenses_rub"] = None
    assert post(a).json() == post(b).json()


def test_unknown_fields_are_ignored():
    s = stable()
    s["something_new"] = {"a": 1}
    s["company"]["new_field"] = 1
    assert post(s).status_code == 200


# ---- некорректные значения: 400 в формате контракта ----

@pytest.mark.parametrize(
    "path, value, field",
    [
        (("company", "financials", "revenue_rub"), -1, "company.financials.revenue_rub"),
        (("company", "financials", "revenue_rub"), None, "company.financials.revenue_rub"),
        (("company", "financials", "expenses_rub"), -1, "company.financials.expenses_rub"),
        (("company", "financials", "monthly_debt_payments_rub"), -5, "company.financials.monthly_debt_payments_rub"),
        (("company", "financials", "monthly_debt_payments_rub"), None, "company.financials.monthly_debt_payments_rub"),
        (("company", "financials", "revenue_rub"), "много", "company.financials.revenue_rub"),
        (("company", "financials", "revenue_rub"), True, "company.financials.revenue_rub"),
        (("company", "business_age_months"), -3, "company.business_age_months"),
        (("company", "business_age_months"), None, "company.business_age_months"),
        (("application", "amount_rub"), 0, "application.amount_rub"),
        (("application", "amount_rub"), None, "application.amount_rub"),
        (("application", "estimated_monthly_payment_rub"), None, "application.estimated_monthly_payment_rub"),
        (("application", "term_months"), 0, "application.term_months"),
        (("application", "term_months"), 12.5, "application.term_months"),
        (("checks", "credit_bureau", "max_overdue_days_24m"), "5", "checks.credit_bureau.max_overdue_days_24m"),
        (("checks", "fns", "has_blocked_accounts"), None, "checks.fns.has_blocked_accounts"),
        (("request_id",), "", "request_id"),
    ],
)
def test_invalid_values_give_400_validation_error(path, value, field):
    s = stable()
    node = s
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value
    r = post(s)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "validation"
    assert field in body["details"]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_nan_and_infinity_are_rejected(token):
    raw = json.dumps(stable()).replace('"revenue_rub": 48000000', f'"revenue_rub": {token}')
    r = client.post(URL, content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "validation"


def test_missing_required_sections_and_broken_json_give_400_not_500():
    s = stable()
    del s["application"]
    assert post(s).status_code == 400
    assert client.post(URL, content="{oops", headers={"content-type": "application/json"}).status_code == 400
    assert client.post(URL, json={}).status_code == 400
