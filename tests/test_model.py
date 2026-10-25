"""logreg-synthetic-0.1: расчёт по JSON-артефакту, контрольные входы для backend, шкала PD → балл → A–E,
объяснения самой модели, формат /api/v1/score и переключение моделей.

Тесты с sklearn/numpy/pandas (обучение, генератор) пропускаются, если их нет: сервису они не нужны (requirements-train.txt).
"""
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import model
from app.features import FEATURE_NAMES, GROUPS, raw_features
from app.main import create_app
from app.schemas import ScoreRequest

ROOT = Path(__file__).parent.parent
VERSION = "logreg-synthetic-0.1"
ART = json.loads((ROOT / "models" / f"{VERSION}.json").read_text(encoding="utf-8"))
METRICS = json.loads((ROOT / "models" / f"{VERSION}.metrics.json").read_text(encoding="utf-8"))
CONTROL = json.loads((ROOT / "data" / "conformance" / f"{VERSION}.json").read_text(encoding="utf-8"))
CASES = CONTROL["cases"]
TOL = CONTROL["meta"]["tolerance"]

client = TestClient(create_app(VERSION))


# ---- артефакт ----

def test_artifact_describes_everything_backend_needs():
    assert ART["model_version"] == VERSION
    assert [f["name"] for f in ART["features"]] == list(FEATURE_NAMES)
    assert ART["columns"] == list(FEATURE_NAMES) + [f"missing:{n}" for n in ART["missing_indicators"]["order"]]
    for c in ART["columns"]:
        assert set(ART["scaling"]) >= {"mean", "scale"} and ART["scaling"]["scale"][c] > 0
        assert math.isfinite(ART["coefficients"][c]) and math.isfinite(ART["scaling"]["mean"][c])
    assert all(math.isfinite(f["impute_value"]) and f["formula"] for f in ART["features"])
    assert {f["name"] for f in ART["features"] if f["missing_indicator"]} == set(ART["missing_indicators"]["order"])
    assert math.isfinite(ART["intercept"])
    assert ART["score"]["pd_at_score_100"] < ART["score"]["pd_at_score_0"]
    assert ART["grades"] == [{"grade": "A", "min_score": 80}, {"grade": "B", "min_score": 65}, {"grade": "C", "min_score": 50},
                             {"grade": "D", "min_score": 35}, {"grade": "E", "min_score": 0}]
    assert set(ART["explanation"]["groups"]) == set(GROUPS)
    grouped = [f for g in ART["explanation"]["groups"].values() for f in g["features"]]
    assert sorted(grouped) == sorted(FEATURE_NAMES), "каждый признак ровно в одной группе объяснения"


def test_no_hidden_or_old_score_inputs():
    """В признаках нет скрытой PD, качества компании, исхода и баллов rules-0.3."""
    text = json.dumps(ART, ensure_ascii=False)
    for banned in ("_true_pd", "_quality", "_true_profit", "rules-0.3", "risk_grade"):
        assert banned not in text
    for c in ART["columns"]:
        assert not any(w in c for w in ("_true", "_quality", "default", "score", "grade"))
    for f in ART["features"]:
        assert not any(w in f["formula"] for w in ("_true", "_quality", "default_12m", "score", "grade"))


# ---- контрольные входы: сервис = sklearn ----

@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_control_case_matches_sklearn(case):
    exp = case["expected"]
    r = model.predict(case["input"], VERSION)
    assert abs(r["logit"] - exp["logit"]) <= TOL["logit"]
    assert abs(r["pd"] - exp["probability_of_default"]) <= TOL["probability_of_default"]
    assert (r["score"], r["grade"]) == (exp["score"], exp["risk_grade"])
    got = dict(zip(FEATURE_NAMES, raw_features(case["input"])))
    for name, v in exp["features"].items():
        assert (got[name] is None) == (v is None), name
        if v is not None:
            assert abs(got[name] - v) <= TOL["features"], name


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_control_case_through_api(case):
    resp = client.post("/api/v1/score", json=case["input"])
    assert resp.status_code == 200, resp.text
    body, exp = resp.json(), case["expected"]
    assert body["model_version"] == VERSION and body["request_id"] == case["input"]["request_id"]
    # Исходная PD модели без округления, а не эвристика по баллу.
    assert abs(body["probability_of_default"] - exp["probability_of_default"]) <= TOL["probability_of_default"]
    assert (body["score"], body["risk_grade"]) == (exp["score"], exp["risk_grade"])
    assert body["factors"] == exp["factors"]
    assert (body["warnings"], body["missing_data"]) == (exp["warnings"], exp["missing_data"])


def test_control_cases_cover_all_grades_and_missing_inputs():
    assert {c["expected"]["risk_grade"] for c in CASES} == set("ABCDE")
    ids = {c["id"] for c in CASES}
    assert {"edge-no-net-profit", "edge-no-expenses", "edge-no-bureau", "edge-no-fns", "edge-no-fssp"} <= ids


def test_pd_differs_from_rules_heuristic():
    """PD не восстанавливается из балла формулой rules-0.3: это выход модели."""
    diffs = [abs(c["expected"]["probability_of_default"] - (0.02 + (100 - c["expected"]["score"]) / 100 * 0.30)) for c in CASES]
    assert max(diffs) > 0.05


# ---- PD → балл → грейд ----

@pytest.mark.parametrize("row", CONTROL["score_mapping"], ids=lambda r: str(r["probability_of_default"]))
def test_score_mapping(row):
    s = model.score_from_pd(row["probability_of_default"], ART["score"])
    assert (s, model.grade_for(s, ART["grades"])) == (row["score"], row["risk_grade"])


@pytest.mark.parametrize("score,grade", [(100, "A"), (80, "A"), (79, "B"), (65, "B"), (64, "C"), (50, "C"),
                                         (49, "D"), (35, "D"), (34, "E"), (0, "E")])
def test_grade_borders(score, grade):
    assert model.grade_for(score, ART["grades"]) == grade


def test_score_is_monotonic_in_pd():
    scores = [model.score_from_pd(i / 1000, ART["score"]) for i in range(0, 1001)]
    assert all(a >= b for a, b in zip(scores, scores[1:]))
    assert scores[0] == 100 and scores[-1] == 0


# ---- объяснения ----

def test_factors_are_model_contributions():
    case = next(c for c in CASES if c["id"] == "demo-bad")
    r = model.predict(case["input"], VERSION)
    ppl = ART["explanation"]["points_per_logit"]
    body = client.post("/api/v1/score", json=case["input"]).json()
    for f in body["factors"]:
        assert f["code"] in GROUPS and not f["code"].startswith("factor_")
        assert f["impact"] == max(-100, min(100, model._round_half_away(-ppl * r["group_contributions"][f["code"]])))
        assert f["title"] == GROUPS[f["code"]][0]
    impacts = [abs(f["impact"]) for f in body["factors"]]
    assert impacts == sorted(impacts, reverse=True) and len(body["factors"]) <= model.MAX_FACTORS
    # Негативные группы плохой заявки — налоги, история, нагрузка, ФССП.
    negative = {f["code"] for f in body["factors"] if f["impact"] < 0}
    assert {"tax_debt", "credit_history", "debt_load", "enforcement"} <= negative


def test_contributions_sum_to_logit():
    for case in CASES[:10]:
        r = model.predict(case["input"], VERSION)
        assert abs(ART["intercept"] + sum(r["group_contributions"].values()) - r["logit"]) < 1e-9


def test_collateral_lowers_pd():
    good = next(c for c in CASES if c["id"] == "demo-good")["expected"]["probability_of_default"]
    coll = next(c for c in CASES if c["id"] == "edge-collateral")["expected"]["probability_of_default"]
    assert coll < good


def test_risk_signals_raise_pd():
    base = json.loads(json.dumps(next(c for c in CASES if c["id"] == "demo-good")["input"]))
    p0 = model.predict(base, VERSION)["pd"]
    for path, value in ((("checks", "credit_bureau", "max_overdue_days_24m"), 90), (("checks", "credit_bureau", "has_write_offs"), True),
                        (("checks", "fns", "tax_debt_rub"), 800_000), (("checks", "fns", "has_blocked_accounts"), True),
                        (("checks", "fssp", "open_proceedings"), 3), (("company", "financials", "net_profit_rub"), -500_000),
                        (("application", "amount_rub"), 40_000_000)):
        req = json.loads(json.dumps(base))
        node = req
        for p in path[:-1]:
            node = node[p]
        node[path[-1]] = value
        assert model.predict(req, VERSION)["pd"] > p0, path


# ---- API и переключение ----

def test_response_format_unchanged():
    case = CASES[0]
    body = client.post("/api/v1/score", json=case["input"]).json()
    assert set(body) == {"request_id", "model_version", "score", "risk_grade", "probability_of_default", "factors", "warnings", "missing_data"}
    assert set(body["factors"][0]) == {"code", "title", "impact", "value", "client_explanation"}
    assert client.post("/score", json=case["input"]).json() == body
    bad = json.loads(json.dumps(case["input"]))
    bad["application"]["amount_rub"] = -1
    r = client.post("/api/v1/score", json=bad)
    assert r.status_code == 400 and r.json()["error"] == "validation"


def test_health_and_switch(monkeypatch):
    assert client.get("/health").json()["model_version"] == VERSION
    assert len(client.get("/health").json()["artifact_sha256"]) == 64
    monkeypatch.setenv("SCORING_MODEL", "rules-0.3")
    rules_client = TestClient(create_app())
    assert rules_client.get("/health").json()["model_version"] == "rules-0.3"
    assert rules_client.post("/api/v1/score", json=CASES[0]["input"]).json()["model_version"] == "rules-0.3"
    monkeypatch.delenv("SCORING_MODEL")
    assert TestClient(create_app()).get("/health").json()["model_version"] == VERSION
    monkeypatch.setenv("SCORING_MODEL", "unknown-9")
    with pytest.raises(RuntimeError):
        create_app()


# ---- метрики и данные ----

def test_metrics_beat_constant_and_split_is_by_company():
    assert METRICS["companies_overlap"] == 0
    assert sum(p["companies"] for p in METRICS["split"].values()) == METRICS["companies"]
    t = METRICS["test"]
    assert t["model"]["roc_auc"] > 0.75
    assert t["model"]["average_precision"] > 2 * t["constant"]["average_precision"]
    assert t["model"]["log_loss"] < t["constant"]["log_loss"] and t["model"]["brier"] < t["constant"]["brier"]
    rates = [g["default_rate"] for g in METRICS["test_by_grade"]]
    assert all(a < b for a, b in zip(rates, rates[1:])), "доля дефолтов растёт от A к E"
    assert METRICS["dataset_sha256"] == ART["training"]["dataset_sha256"]


def test_generator_is_reproducible_and_noisy():
    pytest.importorskip("pandas")
    from ml.generate import dataset_sha256, generate, to_request
    a, b = generate(500, seed=3), generate(500, seed=3)
    assert dataset_sha256(a) == dataset_sha256(b)
    assert dataset_sha256(a) != dataset_sha256(generate(500, seed=4))
    assert 0.03 < a["default_12m"].mean() < 0.2
    assert 0 < a["_true_pd"].min() and a["_true_pd"].max() < 1  # исход случайный, а не порог
    req = to_request(a.iloc[0].to_dict())
    text = json.dumps(req)
    assert "_true" not in text and "_quality" not in text and "default_12m" not in text


def test_full_dataset_matches_trained_artifact():
    pytest.importorskip("pandas")
    from ml.generate import dataset_sha256, generate
    assert dataset_sha256(generate()) == ART["training"]["dataset_sha256"]


def test_retraining_reproduces_artifact():
    pytest.importorskip("sklearn")
    import numpy as np
    from ml import train
    from ml.generate import generate
    df = generate()
    X, _ = train.build_matrix(df)
    parts = train.split(df)
    for name, idx in parts.items():
        assert len(idx) == METRICS["split"][name]["rows"]
    pipe = train.make_pipeline(ART["training"]["selected_C"]).fit(X[parts["train"]], df["default_12m"].to_numpy()[parts["train"]])
    lr = pipe.named_steps["logreg"]
    # Допуск на разные BLAS/процессоры; на той же машине совпадение до последнего знака.
    assert np.allclose(lr.coef_[0], [ART["coefficients"][c] for c in ART["columns"]], atol=1e-5)
    assert abs(lr.intercept_[0] - ART["intercept"]) < 1e-5


@pytest.mark.parametrize("case_id,code,source", [("edge-no-bureau", "credit_history", "БКИ"), ("edge-no-fns", "tax_debt", "ФНС"), ("edge-no-fssp", "enforcement", "ФССП")])
def test_missing_sources_do_not_assert_adverse_facts(case_id, code, source):
    case = next(c for c in CASES if c["id"] == case_id)
    response = model.score(ScoreRequest.model_validate(case["input"]))
    text = model.explanation(code, case["input"], -5)
    assert source in text and "не получен" in text
    assert all(f.client_explanation == text for f in response.factors if f.code == code)
    assert "Просрочки или списания" not in text and "производства повышают" not in text


def test_dataset_fingerprint_tracks_observed_data_and_labels_only():
    pytest.importorskip("pandas")
    from ml.generate import generate, dataset_sha256
    original = generate(100, seed=17)
    changed = original.copy()
    changed["_true_pd"] += 1e-14
    assert dataset_sha256(original) == dataset_sha256(changed)
    changed = original.copy()
    changed.loc[0, "default_12m"] = 1 - changed.loc[0, "default_12m"]
    assert dataset_sha256(original) != dataset_sha256(changed)
    changed = original.copy()
    changed.loc[0, "amount_rub"] += 1000
    assert dataset_sha256(original) != dataset_sha256(changed)


@pytest.mark.parametrize("broken", ["scale", "columns", "grades", "version"])
def test_invalid_artifact_fails_startup(tmp_path, monkeypatch, broken):
    data = json.loads(json.dumps(ART))
    if broken == "scale": data["scaling"]["scale"]["log_revenue"] = 0
    elif broken == "columns": data["columns"][0] = "unknown_feature"
    elif broken == "grades": data["grades"][0]["min_score"] = 75
    else: data["model_version"] = "wrong"
    (tmp_path / f"{VERSION}.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    model.load.cache_clear()
    try:
        with pytest.raises(ValueError): create_app(VERSION)
    finally:
        model.load.cache_clear()


def test_synthetic_requests_use_backend_compatible_guids():
    import uuid
    for case in CASES:
        uuid.UUID(case["input"]["request_id"])


def test_logreg_e2e_uses_separate_reduced_profile():
    import runpy
    script = runpy.run_path(str(ROOT / "scripts" / "e2e_backend.py"))
    legacy = script["scenario_data"]("demo-medium", "rules-0.3")
    learned = script["scenario_data"]("demo-medium", VERSION)
    assert legacy["backend_login"] == "client_medium" and legacy["expected"]["risk_grade"] == "C"
    assert learned["backend_login"] == "client_reduced" and learned["expected"]["risk_grade"] == "C"
    assert script["DEMO"]["demo-medium"]["backend_login"] == "client_medium"
