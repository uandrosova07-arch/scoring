import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SAMPLES = json.loads((Path(__file__).parent.parent / "data" / "samples.json").read_text(encoding="utf-8"))["samples"]

VALID = {
    "business_age_months": 36,
    "revenue_rub": 48_000_000,
    "expenses_rub": 30_000_000,
    "net_profit_rub": 18_000_000,
    "monthly_debt_payments_rub": 100_000,
    "requested_amount_rub": 1_500_000,
    "term_months": 12,
}


def test_sample_count_and_scenarios():
    assert 8 <= len(SAMPLES) <= 12
    assert len({s["scenario"] for s in SAMPLES}) == 4


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_samples_match_expected(sample):
    r = client.post("/score", json=sample["input"])
    assert r.status_code == 200
    body = r.json()
    exp = sample["expected"]
    assert body["risk_level"] == exp["risk_level"]
    assert [x["code"] for x in body["reasons"]] == exp["reason_codes"]
    assert body["missing_data"] == exp["missing_data"]
    assert body["model_version"]


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_same_input_same_output(sample):
    first = client.post("/score", json=sample["input"]).content
    for _ in range(3):
        assert client.post("/score", json=sample["input"]).content == first


def test_reason_values_match_rules():
    body = client.post("/score", json=VALID).json()
    by_code = {r["code"]: r["value"] for r in body["reasons"]}
    assert by_code["EXPENSE_RATIO_OK"] == pytest.approx(30 / 48, abs=1e-4)
    expected_load = (100_000 + 1_500_000 / 12) / (18_000_000 / 12)
    assert by_code["DEBT_LOAD_LOW"] == pytest.approx(expected_load, abs=1e-4)


def test_net_profit_from_request_is_used():
    low_profit = {**VALID, "net_profit_rub": 1_000_000}
    body = client.post("/score", json=low_profit).json()
    assert body["risk_level"] == "high"
    assert "DEBT_LOAD_HIGH" in [r["code"] for r in body["reasons"]]


def test_net_profit_null_is_derived():
    body = client.post("/score", json={**VALID, "net_profit_rub": None}).json()
    assert body["risk_level"] == "low"


def test_negative_net_profit_is_no_profit():
    body = client.post("/score", json={**VALID, "net_profit_rub": -5}).json()
    assert body["risk_level"] == "high"
    assert [r["code"] for r in body["reasons"]][-1] == "NO_PROFIT"




def test_missing_fields_are_listed_in_fixed_order():
    payload = {**VALID, "monthly_debt_payments_rub": None, "revenue_rub": None, "term_months": None}
    body = client.post("/score", json=payload).json()
    assert body["risk_level"] == "undetermined"
    assert body["missing_data"] == ["revenue_rub", "monthly_debt_payments_rub", "term_months"]
    assert [r["code"] for r in body["reasons"]] == ["INSUFFICIENT_DATA"]


def test_absent_field_equals_null():
    absent = {k: v for k, v in VALID.items() if k != "expenses_rub"}
    a = client.post("/score", json=absent).json()
    b = client.post("/score", json={**VALID, "expenses_rub": None}).json()
    assert a == b and a["missing_data"] == ["expenses_rub"]


def test_nan_is_treated_as_missing():
    raw = json.dumps(VALID).replace('"expenses_rub": 30000000', '"expenses_rub": NaN')
    r = client.post("/score", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 200
    assert r.json()["missing_data"] == ["expenses_rub"]


def test_empty_body_object_is_all_missing():
    body = client.post("/score", json={}).json()
    assert body["risk_level"] == "undetermined"
    assert len(body["missing_data"]) == 5


def test_external_block_null_and_unknown_fields_are_ignored():
    payload = {**VALID, "credit_bureau": None, "something_new": {"a": 1}}
    r = client.post("/score", json=payload)
    assert r.status_code == 200 and r.json()["risk_level"] == "low"




@pytest.mark.parametrize(
    "patch, field",
    [
        ({"revenue_rub": -1}, "revenue_rub"),
        ({"revenue_rub": 0}, "revenue_rub"),
        ({"expenses_rub": -1}, "expenses_rub"),
        ({"monthly_debt_payments_rub": -5}, "monthly_debt_payments_rub"),
        ({"requested_amount_rub": 0}, "requested_amount_rub"),
        ({"term_months": 0}, "term_months"),
        ({"term_months": 12.5}, "term_months"),
        ({"revenue_rub": "много"}, "revenue_rub"),
        ({"revenue_rub": True}, "revenue_rub"),
        ({"business_age_months": -3}, "business_age_months"),
    ],
)
def test_invalid_values_give_422_problem_details(patch, field):
    r = client.post("/score", json={**VALID, **patch})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 422
    assert field in [e["field"] for e in body["errors"]]


def test_infinity_is_rejected():
    raw = json.dumps(VALID).replace('"revenue_rub": 48000000', '"revenue_rub": Infinity')
    r = client.post("/score", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_broken_json_gives_422_not_500():
    r = client.post("/score", content="{oops", headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
