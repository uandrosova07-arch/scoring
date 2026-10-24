"""Общий эталон rules-0.3: сервис отвечает ровно так, как резервные C#-правила с правками rules-0.3.

Тот же файл data/conformance/rules-0.3.json проверяет C#-тест backend (docs/backend/ScoringConformanceTests.cs).
Сравнивается всё, что видит backend: балл, грейд, PD, факторы целиком и в том же порядке, предупреждения, missing_data.
"""
import copy
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import MODEL_VERSION

ROOT = Path(__file__).parent.parent
DATA = json.loads((ROOT / "data" / "conformance" / "rules-0.3.json").read_text(encoding="utf-8"))
CASES = DATA["cases"]
client = TestClient(app)

sys.path.insert(0, str(ROOT / "scripts"))
import conformance  # noqa: E402


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_service_matches_shared_case(case):
    r = client.post("/api/v1/score", json=case["input"])
    assert r.status_code == 200, r.text
    body, exp = r.json(), case["expected"]
    assert body["request_id"] == case["input"]["request_id"]
    assert body["model_version"] == exp["model_version"] == MODEL_VERSION
    assert (body["score"], body["risk_grade"]) == (exp["score"], exp["risk_grade"])
    assert body["probability_of_default"] == exp["probability_of_default"]
    assert body["factors"] == exp["factors"]
    assert body["warnings"] == exp["warnings"]
    assert body["missing_data"] == exp["missing_data"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_shared_case_is_reproducible(case):
    first = client.post("/api/v1/score", json=case["input"]).content
    assert client.post("/api/v1/score", json=case["input"]).content == first


def test_cases_cover_demo_missing_data_and_boundaries():
    groups = {c["group"] for c in CASES}
    assert groups == {"demo", "missing", "boundary"}
    ids = {c["id"] for c in CASES}

    for pair in (("age-5", "age-6"), ("age-11", "age-12"), ("age-35", "age-36"),
                 ("revenue-ratio-6000000", "revenue-ratio-5999999"), ("revenue-ratio-1500000", "revenue-ratio-1499999"),
                 ("expense-ratio-43200000", "expense-ratio-43200001"), ("debt-load-41406", "debt-load-41407"),
                 ("debt-load-161406", "debt-load-161407"), ("debt-load-281406", "debt-load-281407"),
                 ("overdue-30", "overdue-31"), ("overdue-90", "overdue-91"), ("tax-debt-49999", "tax-debt-50000")):
        assert set(pair) <= ids, pair
    assert {c["expected"]["score"] for c in CASES if c["id"].startswith("grade-score")} == {80, 75, 65, 60, 50, 45, 35, 30}


def test_grade_boundaries_hit_exactly():
    by = {c["id"]: c["expected"] for c in CASES}
    for score, grade in ((80, "A"), (75, "B"), (65, "B"), (60, "C"), (50, "C"), (45, "D"), (35, "D"), (30, "E")):
        assert (by[f"grade-score-{score}"]["score"], by[f"grade-score-{score}"]["risk_grade"]) == (score, grade)


def test_shared_cases_rebuild_identically():
    assert conformance.main("check") == 0




def _random_request(rng, base: dict) -> dict:
    r = copy.deepcopy(base)
    a, c, f, ch = r["application"], r["company"], r["company"]["financials"], r["checks"]
    a["amount_rub"] = rng.randint(100_000, 20_000_000)
    a["estimated_monthly_payment_rub"] = rng.randint(0, 2_000_000)
    a["collateral_offered"] = rng.random() < 0.1
    c["business_age_months"] = rng.choice([0, 5, 6, 11, 12, 35, 36, 37, rng.randint(0, 300)])
    f["revenue_rub"] = rng.choice([rng.randint(0, 300_000_000), a["amount_rub"] * rng.choice([1, 2, 4]),
                                   a["amount_rub"] * rng.choice([1, 2, 4]) - 1])
    f["expenses_rub"] = rng.choice([None, rng.randint(0, 300_000_000), f["revenue_rub"] * 9 // 10, f["revenue_rub"] * 9 // 10 + 1])
    f["net_profit_rub"] = rng.choice([None, 0, rng.randint(-5_000_000, 60_000_000)])
    f["monthly_debt_payments_rub"] = rng.randint(0, 3_000_000)
    if f["net_profit_rub"] and f["net_profit_rub"] > 0 and rng.random() < 0.3:
        
        total = f["net_profit_rub"] * rng.choice([3, 5, 7]) // 120
        f["monthly_debt_payments_rub"] = max(total - a["estimated_monthly_payment_rub"], 0) + rng.choice([0, 1])
    ch["credit_bureau"] = rng.choice([None, {
        "credit_history_months": rng.choice([0, 12, 60]), "active_loans": rng.choice([0, 1]), "closed_loans": rng.choice([0, 2]),
        "total_debt_rub": 0, "monthly_payments_rub": 0, "max_overdue_days_24m": rng.choice([0, 1, 30, 31, 90, 91, 200]),
        "overdue_count_24m": 0, "has_write_offs": rng.random() < 0.1}])
    ch["fns"] = rng.choice([None, {"tax_debt_rub": rng.choice([0, 1, 49_999, 50_000, 700_000]),
                                   "has_blocked_accounts": rng.random() < 0.2, "reported_revenue_rub": rng.choice([None, 1_000_000])}])
    ch["fssp"] = rng.choice([None, {"open_proceedings": rng.choice([0, 0, 1, 3]), "open_amount_rub": rng.choice([0, 10_000, 2_000_000])}])
    ch["egrul"] = rng.choice([None, {"status": "Active", "head_disqualified": False, "mass_address": False}])
    return r


def test_random_inputs_match_csharp_copy():
    import random
    import backend_reference as br
    from app.rules import score
    from app.schemas import ScoreRequest

    rng = random.Random(2026)
    base = next(c["input"] for c in CASES if c["id"] == "demo-good")
    for _ in range(2000):
        req = _random_request(rng, base)
        py = score(ScoreRequest.model_validate(req)).model_dump()
        cs = br.score_rules_v1(req, agreed=True)
        assert (py["score"], py["risk_grade"], py["factors"], py["warnings"], py["missing_data"]) == \
               (cs["score"], cs["risk_grade"], cs["factors"], cs["warnings"], cs["missing_data"]), req
        assert py["probability_of_default"] == float(cs["probability_of_default"])
