import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SAMPLES = json.loads((Path(__file__).parent.parent / "data" / "samples.json").read_text(encoding="utf-8"))["samples"]
BY_ID = {s["id"]: s for s in SAMPLES}
URL = "/api/v1/score"


def stable() -> dict:
    return copy.deepcopy(BY_ID["stable-1"]["input"])


def post(payload: dict):
    return client.post(URL, json=payload)




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
    assert {f["code"]: f["impact"] for f in body["factors"]} == exp["factor_impacts"]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_same_input_same_output(sample):
    first = post(sample["input"]).content
    for _ in range(3):
        assert post(sample["input"]).content == first




@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_response_shape_follows_contract(sample):
    body = post(sample["input"]).json()
    assert body["request_id"] == sample["input"]["request_id"]
    assert body["model_version"]
    assert isinstance(body["score"], int) and 0 <= body["score"] <= 100
    assert body["risk_grade"] in "ABCDE"
    assert body["probability_of_default"] is None
    assert 3 <= len(body["factors"]) <= 7
    impacts = [abs(f["impact"]) for f in body["factors"]]
    assert impacts == sorted(impacts, reverse=True)
    for f in body["factors"]:
        assert set(f) == {"code", "title", "impact", "value", "client_explanation"}
    assert isinstance(body["warnings"], list)


@pytest.mark.parametrize("sample", [s for s in SAMPLES if not s["expected"]["missing_data"]],
                         ids=[s["id"] for s in SAMPLES if not s["expected"]["missing_data"]])
def test_score_is_base_plus_factor_impacts(sample):

    body = post(sample["input"]).json()
    assert body["score"] == max(0, min(100, 50 + sum(f["impact"] for f in body["factors"])))


@pytest.mark.parametrize("score,grade", [(100, "A"), (80, "A"), (79, "B"), (65, "B"), (64, "C"),
                                         (50, "C"), (49, "D"), (35, "D"), (34, "E"), (0, "E")])
def test_grade_table_matches_backend(score, grade):
    from app.rules import grade_for
    assert grade_for(score) == grade


def test_factor_values_match_independent_calculation():
    body = post(stable()).json()
    by = {f["code"]: f for f in body["factors"]}
    assert by["factor_expense_ratio"]["value"] == "63%"  
    s = stable()
    load = (100_000 + s["application"]["estimated_monthly_payment_rub"]) / (18_000_000 / 12)
    assert by["factor_debt_load"]["value"] == f"{int(load * 100 + 0.5)}%"


def impact(payload: dict, code: str) -> int:
    return {f["code"]: f["impact"] for f in post(payload).json()["factors"]}[code]


def test_estimated_payment_from_backend_is_used():
    
    big = stable()
    big["application"]["estimated_monthly_payment_rub"] = 5_000_000  
    assert impact(big, "factor_debt_load") == -20
    assert impact(stable(), "factor_debt_load") == 20


def test_simple_split_when_estimated_payment_is_absent():
    
    s = stable()
    del s["application"]["estimated_monthly_payment_rub"]
    s["application"]["amount_rub"] = 18_000_000
    assert impact(s, "factor_debt_load") == -20
    s["application"]["amount_rub"] = 1_200_000  
    assert impact(s, "factor_debt_load") == 20


def test_net_profit_from_request_is_used_else_derived():
    low = stable()
    low["company"]["financials"]["net_profit_rub"] = 1_000_000
    assert post(low).json()["risk_grade"] != "A"
    derived = stable()
    derived["company"]["financials"]["net_profit_rub"] = None
    assert post(derived).json()["risk_grade"] == "A"


def test_no_profit():
    s = stable()
    s["company"]["financials"]["net_profit_rub"] = -5
    by = {f["code"]: f for f in post(s).json()["factors"]}
    assert by["factor_debt_load"]["impact"] == -35 and by["factor_debt_load"]["value"] == "нет прибыли"


def test_health_and_alias():
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/score", json=stable()).json() == post(stable()).json()




def test_missing_financials_go_to_manual_review_grade_d():
    s = stable()
    s["company"]["financials"].update(expenses_rub=None, monthly_debt_payments_rub=None)
    body = post(s).json()
    assert body["risk_grade"] == "D" and body["score"] == 40
    assert body["missing_data"] == ["company.financials.expenses_rub", "company.financials.monthly_debt_payments_rub"]
    assert "insufficient_data" in body["warnings"]
    assert body["factors"][0]["code"] == "factor_insufficient_data"
    assert 3 <= len(body["factors"]) <= 7


def test_absent_field_equals_null():
    a = stable()
    del a["company"]["financials"]["expenses_rub"]
    b = stable()
    b["company"]["financials"]["expenses_rub"] = None
    assert post(a).json() == post(b).json()


def test_missing_financials_block_or_company():
    s = stable()
    s["company"]["financials"] = None
    assert len(post(s).json()["missing_data"]) == 3
    s = stable()
    del s["company"]
    body = post(s).json()
    assert body["risk_grade"] == "D" and "business_age_missing" in body["warnings"]


def test_null_check_blocks_become_warnings():
    s = stable()
    s["checks"]["credit_bureau"] = None
    s["checks"]["fssp"] = None
    w = post(s).json()["warnings"]
    assert "credit_bureau_missing" in w and "fssp_missing" in w and "egrul_missing" not in w
    s["checks"] = None
    assert len([x for x in post(s).json()["warnings"] if x.endswith("_missing") and "age" not in x]) == 4


def test_unknown_fields_are_ignored():
    s = stable()
    s["something_new"] = {"a": 1}
    s["company"]["new_field"] = 1
    assert post(s).status_code == 200




@pytest.mark.parametrize(
    "path, value, field",
    [
        (("company", "financials", "revenue_rub"), -1, "company.financials.revenue_rub"),
        (("company", "financials", "revenue_rub"), 0, "company.financials.revenue_rub"),
        (("company", "financials", "expenses_rub"), -1, "company.financials.expenses_rub"),
        (("company", "financials", "monthly_debt_payments_rub"), -5, "company.financials.monthly_debt_payments_rub"),
        (("company", "financials", "revenue_rub"), "много", "company.financials.revenue_rub"),
        (("company", "financials", "revenue_rub"), True, "company.financials.revenue_rub"),
        (("company", "business_age_months"), -3, "company.business_age_months"),
        (("application", "amount_rub"), 0, "application.amount_rub"),
        (("application", "amount_rub"), None, "application.amount_rub"),
        (("application", "term_months"), 0, "application.term_months"),
        (("application", "term_months"), 12.5, "application.term_months"),
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
