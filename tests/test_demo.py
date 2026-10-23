import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import MODEL_VERSION

client = TestClient(app)
ROOT = Path(__file__).parent.parent
DEMO = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
IDS = [c["id"] for c in DEMO]

sys.path.insert(0, str(ROOT / "scripts"))
import demo_comparison  # noqa: E402


def test_demo_grades_on_backend_seed():
    # Фактические грейды на данных seed backend (коммит 9f18dd4, возраст бизнеса на 23.10.2026). Пороги rules-0.2 не менялись.
    # Python и резервные C#-правила расходятся у трёх компаний из пяти: docs/grade-comparison.md. Менять только вместе с бэкендером.
    assert {c["id"]: c["expected"]["risk_grade"] for c in DEMO} == {
        "demo-good": "C", "demo-medium": "B", "demo-manual": "D", "demo-bad": "E", "demo-new": "D",
    }
    assert {c["id"]: c["csharp_fallback"]["risk_grade"] for c in DEMO} == {
        "demo-good": "A", "demo-medium": "C", "demo-manual": "B", "demo-bad": "E", "demo-new": "E",
    }


@pytest.mark.parametrize("company", DEMO, ids=IDS)
def test_demo_company_matches_expected(company):
    r = client.post("/api/v1/score", json=company["input"])
    assert r.status_code == 200
    body, exp = r.json(), company["expected"]
    assert body["request_id"] == company["input"]["request_id"]
    assert body["score"] == exp["score"]
    assert body["risk_grade"] == exp["risk_grade"]
    assert body["missing_data"] == exp["missing_data"]
    assert body["warnings"] == exp["warnings"]
    # Факторы: те же коды, вклады и значения, в том же порядке.
    assert [{"code": f["code"], "impact": f["impact"], "value": f["value"]} for f in body["factors"]] == exp["factors"]


@pytest.mark.parametrize("company", DEMO, ids=IDS)
def test_demo_company_is_reproducible(company):
    first = client.post("/api/v1/score", json=company["input"]).content
    assert client.post("/api/v1/score", json=company["input"]).content == first


@pytest.mark.parametrize("company", DEMO, ids=IDS)
def test_request_file_is_the_demo_input(company):
    # data/requests/*.json отдаются команде как готовые примеры: это ровно input из demo_companies.json.
    path = ROOT / "data" / "requests" / f"{company['id']}.json"
    assert json.loads(path.read_text(encoding="utf-8")) == company["input"]


def test_demo_data_rebuilds_identically():
    # Запросы, ожидаемые ответы и столбец C# пересобираются из seed тем же результатом: данные воспроизводимы.
    assert demo_comparison.check() == 0


# ---- итог для клиента ----

# Ожидаемые исходы общего демо (SeedData.Accounts, docs/decision-logic.md backend) не меняем без согласования.
# Пока Python на данных seed даёт другие грейды, тест помечен xfail(strict): как только расчёт или данные согласуют
# и исход совпадёт, тест начнёт проходить и упадёт как XPASS. Тогда пометку надо снять.
_DIVERGES = {
    "demo-good": "Python C вместо A: одобрение с условиями под 22,5 % вместо автоодобрения под 19,5 %",
    "demo-medium": "Python B вместо C: одобрение 3 млн под 21 % вместо 2,4 млн под 22,5 %",
    "demo-manual": "Python D вместо B: менеджер получает рекомендацию «с условиями» вместо «одобрить»",
}


def _agreed_outcome_holds(company) -> bool:
    agreed = company["scenario"]["agreed_outcome"]
    auto = company["decision"]["python"]["auto"]
    if agreed == "ManualReview":
        # Сценарий «Вектор»: менеджер из-за суммы, рекомендация «одобрить» (интеграционный тест backend).
        return auto["type"] == "ManualReview" and auto["recommended_type"] == "Approved"
    return auto["type"] == agreed


@pytest.mark.parametrize("company", [
    pytest.param(c, marks=pytest.mark.xfail(strict=True, reason=_DIVERGES[c["id"]])) if c["id"] in _DIVERGES else c
    for c in DEMO if c["scenario"]["agreed_outcome"] != "SubmissionRejected"
], ids=lambda c: c["id"])
def test_python_keeps_agreed_demo_outcome(company):
    assert _agreed_outcome_holds(company)


@pytest.mark.parametrize("company", [c for c in DEMO if c["scenario"]["agreed_outcome"] != "SubmissionRejected"], ids=lambda c: c["id"])
def test_csharp_fallback_keeps_agreed_demo_outcome(company):
    # Резервные правила backend дают ровно сценарий демо: на них исходы и были задуманы.
    agreed = company["scenario"]["agreed_outcome"]
    auto = company["decision"]["csharp_fallback"]["auto"]
    if agreed == "ManualReview":
        assert (auto["type"], auto["recommended_type"]) == ("ManualReview", "Approved")
    else:
        assert auto["type"] == agreed


def test_new_company_does_not_reach_scoring_in_demo():
    new = next(c for c in DEMO if c["id"] == "demo-new")
    assert "companyId" in new["scenario"]["backend_validation_errors"]
    assert all(not c["scenario"]["backend_validation_errors"] for c in DEMO if c["id"] != "demo-new")


# ---- версия правил ----

def test_rules_version_is_pinned():
    assert MODEL_VERSION == "rules-0.2"
    body = client.post("/api/v1/score", json=DEMO[0]["input"]).json()
    assert body["model_version"] == "rules-0.2"
    assert client.get("/health").json()["model_version"] == "rules-0.2"


def test_env_example_does_not_contradict_code():
    # Если в .env.example задана версия, она обязана совпадать с версией в коде.
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("MODEL_VERSION="):
            assert line.split("=", 1)[1] == MODEL_VERSION


def test_no_old_format_left_in_data():
    # Старый формат (risk_level, reasons, плоский запрос) из примеров убран.
    for path in [*(ROOT / "data").glob("*.json"), *(ROOT / "data" / "requests").glob("*.json")]:
        text = path.read_text(encoding="utf-8")
        assert "risk_level" not in text and '"reasons"' not in text and "reason_codes" not in text, path.name
