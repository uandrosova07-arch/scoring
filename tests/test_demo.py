import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules import MODEL_VERSION

client = TestClient(app)
ROOT = Path(__file__).parent.parent
DEMO = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
IDS = [c["id"] for c in DEMO]


def test_agreed_demo_grades():
    # Грейды демонстрационных компаний, согласованные с бэкендером. Менять только вместе с ним.
    assert {c["id"]: c["expected"]["risk_grade"] for c in DEMO} == {
        "demo-good": "A", "demo-medium": "C", "demo-manual": "B", "demo-bad": "E", "demo-incomplete": "D",
    }


@pytest.mark.parametrize("company", DEMO, ids=IDS)
def test_demo_company_matches_expected(company):
    r = client.post("/api/v1/score", json=company["input"])
    assert r.status_code == 200
    body, exp = r.json(), company["expected"]
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
    for path in (ROOT / "data").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "risk_level" not in text and '"reasons"' not in text and "reason_codes" not in text, path.name
