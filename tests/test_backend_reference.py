"""Копия расчётов backend (scripts/backend_reference.py) воспроизводит ассерты из тестов самого backend.

Если какой-то тест здесь упал после обновления копии, значит перенос разошёлся с C#, и столбцу «C#»
в docs/grade-comparison.md доверять нельзя, пока не исправите. Номера строк по коммиту 9f18dd4.
"""
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backend_reference as br  # noqa: E402

# TestData.Now в backend: 20.10.2026. TestData.Company и TestData.Product совпадают с seed «Ромашки» и «Оборотного кредита».
TEST_NOW = date(2026, 10, 20)
ROMASHKA = br.Scenario("t", "client_good", "working-capital", D(1_500_000), 12, "WorkingCapital", "", "")


def _request(s=ROMASHKA):
    return br.build_request(s, "r", "a", "AC-000001", "2026-10-20T10:00:00Z", TEST_NOW)


def _bad_checks():
    ch = br.CHECKS["5401234567"]
    return {"egrul": ch["egrul"], "fns": ch["fns"], "credit_bureau": dict(zip(br._BUREAU_FIELDS, ch["credit_bureau"])), "fssp": ch["fssp"]}


def test_annuity_matches_loan_math_tests():
    # LoanMathTests.Schedule_interest_matches_the_known_annuity, HttpScoringServiceTests.Request_follows_the_contract
    payment = br.monthly_payment(D(1_500_000), D("19.5"), 12)
    assert payment == D("138593.06")
    assert br.whole_rubles(payment) == 138_593


def test_request_matches_contract_serialization_test():
    # HttpScoringServiceTests.Request_follows_the_contract: 91 месяц, деньги целыми, 19.5 как есть
    req = br.to_json_value(_request())
    assert req["application"]["estimated_monthly_payment_rub"] == 138_593
    assert req["company"]["business_age_months"] == 91
    assert req["company"]["region_code"] == "77"
    assert req["application"]["product"]["base_rate_percent"] == 19.5
    assert req["checks"]["credit_bureau"]["max_overdue_days_24m"] == 0


def test_stable_company_gets_grade_a():
    # RuleBasedScoringServiceTests.Stable_company_with_clean_checks_gets_grade_A
    out = br.score_rules_v1(_request())
    assert (out["score"], out["risk_grade"], out["warnings"]) == (95, "A", [])
    assert "credit_history" in [f["code"] for f in out["factors"]]


def test_high_debt_load_and_overdues_drop_the_grade():
    # RuleBasedScoringServiceTests.High_debt_load_and_overdues_drop_the_grade
    s = replace(ROMASHKA, amount=D(2_000_000))
    req = _request(s)
    req["company"]["financials"].update(revenue_rub=D(14_000_000), net_profit_rub=D(100_000), monthly_debt_payments_rub=D(600_000))
    req["checks"] = _bad_checks()
    out = br.score_rules_v1(req)
    impacts = {f["code"]: f["impact"] for f in out["factors"]}
    assert out["risk_grade"] == "E"
    assert impacts["credit_history"] == -30 and impacts["tax_debt"] == -30


def test_missing_sources_produce_warnings():
    # RuleBasedScoringServiceTests.Missing_sources_produce_warnings_instead_of_failing
    req = _request(replace(ROMASHKA, amount=D(1_000_000)))
    req["company"]["financials"]["net_profit_rub"] = None
    req["checks"] = {"egrul": None, "fns": None, "credit_bureau": None, "fssp": None}
    assert sorted(br.score_rules_v1(req)["warnings"]) == sorted(["net_profit_missing", "credit_bureau_missing", "fns_missing", "fssp_missing"])


def test_policy_hard_stops_reject_regardless_of_score():
    # DecisionPolicyTests.Hard_stops_reject_regardless_of_score
    out = br.decide(ROMASHKA, _bad_checks(), "A", 90, ["debt_load"])
    assert out["type"] == "Rejected"
    assert sorted(out["reasons"]) == sorted(["tax_debt", "accounts_blocked", "overdue_90", "enforcement_debt"])


def test_policy_grade_a_within_limit_is_approved():
    # DecisionPolicyTests.Grade_A_within_auto_limit_is_approved_as_requested
    out = br.decide(ROMASHKA, _request()["checks"], "A", 95, ["debt_load"])
    assert (out["type"], out["amount"], out["term_months"], out["interest_rate"]) == ("Approved", 1_500_000, 12, D("19.5"))
    assert 138_000 <= out["monthly_payment"] <= 139_500


def test_policy_grade_c_reduces_amount():
    # DecisionPolicyTests.Grade_C_is_approved_with_reduced_amount_and_rate_premium; PipelineEndpointsTests.Medium_client_...
    s = replace(ROMASHKA, login="client_medium", amount=D(3_000_000), term=24)
    out = br.decide(s, _request()["checks"], "C", 55, ["debt_load"])
    assert (out["type"], out["amount"], out["interest_rate"]) == ("ApprovedWithConditions", 2_400_000, D("22.5"))
    assert "amount_reduced" in out["reasons"]


def test_demo_profiles_table_from_decision_logic():
    # docs/decision-logic.md, «Демо-профили»: Ромашка 95/A, Кузнецов 55/C, Вектор 70/B. Сценарии демо из PipelineEndpointsTests.
    got = {}
    for s in br.DEMO_SCENARIOS[:3]:
        out = br.score_rules_v1(_request(s))
        got[s.login] = (out["score"], out["risk_grade"])
    assert got == {"client_good": (95, "A"), "client_medium": (55, "C"), "client_manual": (70, "B")}


def test_demo_manager_approval_for_vector():
    # DemoManagerTests: 8 млн при выручке 180 млн не режется, ставка 19,5 + 1,5 за грейд B
    s = br.DEMO_SCENARIOS[2]
    out = br.demo_manager_approve(s, "B")
    assert (out["amount"], out["interest_rate"]) == (8_000_000, D(21))
