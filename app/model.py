"""Расчёт logreg-synthetic по JSON-артефакту (models/*.json), без sklearn и numpy.

Те же шаги, что у sklearn Pipeline при обучении и у backend по тому же артефакту:
признаки (app/features.py) → пропуск = медиана train, индикаторы пропусков → z = (x − mean) / scale →
логит = intercept + Σ coef·z → PD = сигмоида → балл (линейно по PD) → грейд A–E.

Объяснение — вклады самой модели, а не ручные баллы rules-0.3: для группы признаков вклад в логит
Σ coef·z (относительно средней заявки train, у которой все z = 0), переведённый в баллы наклоном шкалы у средней заявки:
impact = −points_per_logit · вклад (линейное приближение, не обрезается границами балла 0 и 100).
"""
from __future__ import annotations

import json
import hashlib
import math
from functools import lru_cache
from pathlib import Path

from app.features import FEATURE_NAMES, describe, raw_features
from app.schemas import Factor, ScoreRequest, ScoreResponse

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL = "logreg-synthetic-0.1"
MAX_FACTORS = 7

TEXTS = {
    "revenue_size": ("Крупная выручка снижает расчётный риск.", "Небольшая выручка повышает расчётный риск."),
    "revenue_to_amount": ("Сумма кредита умеренная относительно выручки и масштаба бизнеса.", "Сумма кредита велика относительно выручки."),
    "loan_term": ("Срок кредита снижает расчётный риск.", "Срок кредита повышает расчётный риск."),
    "expense_ratio": ("Доля расходов в выручке снижает расчётный риск.", "Расходы занимают большую долю выручки, это повышает риск."),
    "debt_load": ("Платежи по кредитам невелики относительно прибыли.", "Платежи по кредитам велики относительно прибыли или прибыли нет."),
    "business_age": ("Бизнес работает давно, это снижает риск.", "Бизнес молодой, это повышает риск."),
    "credit_history": ("Показатели кредитной истории снижают расчётный риск относительно обучающей выборки.", "Показатели кредитной истории повышают расчётный риск относительно обучающей выборки."),
    "tax_debt": ("Показатели налоговых обязательств снижают расчётный риск относительно обучающей выборки.", "Показатели налоговых обязательств повышают расчётный риск относительно обучающей выборки."),
    "enforcement": ("Показатели исполнительных производств снижают расчётный риск относительно обучающей выборки.", "Показатели исполнительных производств повышают расчётный риск относительно обучающей выборки."),
    "collateral": ("Предложенный залог снижает риск.", "Без залога риск выше, чем с залогом."),
}
NEUTRAL = "Почти не влияет на оценку."


def validate_artifact(artifact: dict, version: str) -> None:
    if artifact.get("model_version") != version:
        raise ValueError("Artifact model version does not match the selected model")
    features = artifact["features"]
    if [f["name"] for f in features] != list(FEATURE_NAMES):
        raise ValueError("Artifact feature order does not match runtime")
    missing = artifact["missing_indicators"]["order"]
    columns = list(FEATURE_NAMES) + [f"missing:{name}" for name in missing]
    if len(set(missing)) != len(missing) or not set(missing) <= set(FEATURE_NAMES) or artifact["columns"] != columns:
        raise ValueError("Invalid artifact columns or missing indicators")
    for feature in features:
        if not math.isfinite(feature["impute_value"]):
            raise ValueError("Invalid imputation value")
    for name in columns:
        values = [artifact["coefficients"][name], artifact["scaling"]["mean"][name], artifact["scaling"]["scale"][name]]
        if not all(math.isfinite(value) for value in values) or values[2] <= 0:
            raise ValueError(f"Invalid scoring parameters: {name}")
    mapping = artifact["score"]
    if not 0 <= mapping["pd_at_score_100"] < mapping["pd_at_score_0"] <= 1:
        raise ValueError("Invalid PD/score mapping")
    if not math.isfinite(artifact["intercept"]) or not math.isfinite(artifact["explanation"]["points_per_logit"]) or artifact["explanation"]["points_per_logit"] <= 0:
        raise ValueError("Invalid model intercept or explanation scale")
    groups = artifact["explanation"]["groups"]
    if set(groups) != set(TEXTS) or sorted(f for g in groups.values() for f in g["features"]) != sorted(FEATURE_NAMES):
        raise ValueError("Invalid explanation groups")
    expected = [{"grade": g, "min_score": value} for g, value in zip("ABCDE", (80, 65, 50, 35, 0))]
    if artifact["grades"] != expected:
        raise ValueError("Artifact grades differ from the credit policy")


@lru_cache(maxsize=4)
def load(model_version: str = DEFAULT_MODEL) -> dict:
    payload = (MODELS_DIR / f"{model_version}.json").read_bytes()
    artifact = json.loads(payload)
    validate_artifact(artifact, model_version)
    artifact["_artifact_sha256"] = hashlib.sha256(payload).hexdigest()
    return artifact


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _round_half_away(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def score_from_pd(pd_: float, params: dict) -> int:
    hi, lo = params["pd_at_score_0"], params["pd_at_score_100"]
    return max(0, min(100, math.floor(100 * (hi - pd_) / (hi - lo) + 0.5)))


def grade_for(score: int, grades: list[dict]) -> str:
    return next(g["grade"] for g in grades if score >= g["min_score"])


def columns_z(req: dict, art: dict) -> dict[str, float]:
    """Масштабированные значения всех столбцов модели (признаки и индикаторы пропусков)."""
    raw = dict(zip(FEATURE_NAMES, raw_features(req)))
    values = {}
    for f in art["features"]:
        v = raw[f["name"]]
        values[f["name"]] = f["impute_value"] if v is None else v
    for name in art["missing_indicators"]["order"]:
        values[f"missing:{name}"] = 1.0 if raw[name] is None else 0.0
    mean, scale = art["scaling"]["mean"], art["scaling"]["scale"]
    return {c: (values[c] - mean[c]) / scale[c] for c in art["columns"]}


def predict(req: dict, model_version: str = DEFAULT_MODEL) -> dict:
    art = load(model_version)
    z = columns_z(req, art)
    coef = art["coefficients"]
    contrib = {c: coef[c] * z[c] for c in art["columns"]}
    logit = art["intercept"] + sum(contrib[c] for c in art["columns"])
    pd_ = _sigmoid(logit)
    score = score_from_pd(pd_, art["score"])
    groups = {}
    for code, g in art["explanation"]["groups"].items():
        cols = [c for f in g["features"] for c in (f, f"missing:{f}") if c in contrib]
        groups[code] = sum(contrib[c] for c in cols)
    return {"logit": logit, "pd": pd_, "score": score, "grade": grade_for(score, art["grades"]), "group_contributions": groups}


def _missing_data(req: dict) -> list[str]:
    f, ch = req["company"]["financials"], req.get("checks") or {}
    out = [p for p, v in (("company.financials.expenses_rub", f.get("expenses_rub")), ("company.financials.net_profit_rub", f.get("net_profit_rub"))) if v is None]
    if ch.get("egrul") is None:
        out.append("checks.egrul")
    if ch.get("fns") is None:
        out.append("checks.fns")
    elif ch["fns"].get("reported_revenue_rub") is None:
        out.append("checks.fns.reported_revenue_rub")
    out += [f"checks.{n}" for n in ("credit_bureau", "fssp") if ch.get(n) is None]
    return out


def _warnings(req: dict) -> list[str]:
    f, ch = req["company"]["financials"], req.get("checks") or {}
    out = []
    if f.get("expenses_rub") is None:
        out.append("expenses_missing")
    if f.get("net_profit_rub") is None:
        out.append("net_profit_missing")
    out += [f"{n}_missing" for n in ("credit_bureau", "fns", "fssp") if ch.get(n) is None]
    return out


def explanation(code: str, req: dict, impact: int) -> str:
    checks = req.get("checks") or {}
    missing = {
        "credit_history": ("credit_bureau", "БКИ"),
        "tax_debt": ("fns", "ФНС"),
        "enforcement": ("fssp", "ФССП"),
    }
    if code in missing:
        source, title = missing[code]
        if checks.get(source) is None:
            return f"Ответ {title} не получен: модель использует статистическое заполнение и признак отсутствующих данных."
    fin = req["company"]["financials"]
    if code == "debt_load" and fin.get("net_profit_rub") is None:
        return "Данные о прибыли отсутствуют: модель использует статистическое заполнение и признак пропуска."
    if code == "expense_ratio" and (fin.get("expenses_rub") is None or fin["revenue_rub"] == 0):
        return "Долю расходов определить нельзя: модель использует статистическое заполнение и признак пропуска."
    positive, negative = TEXTS[code]
    return positive if impact > 0 else negative if impact < 0 else NEUTRAL


def score(req: ScoreRequest, model_version: str = DEFAULT_MODEL) -> ScoreResponse:
    data = req.model_dump()
    art = load(model_version)
    r = predict(data, model_version)
    factors = []
    ppl = art["explanation"]["points_per_logit"]
    for code, c in r["group_contributions"].items():
        impact = max(-100, min(100, _round_half_away(-ppl * c)))
        text = explanation(code, data, impact)
        factors.append(Factor(code=code, title=art["explanation"]["groups"][code]["title"], impact=impact,
                              value=describe(code, data), client_explanation=text))
    factors.sort(key=lambda f: (-abs(f.impact), f.code))
    return ScoreResponse(
        request_id=req.request_id,
        model_version=model_version,
        score=r["score"],
        risk_grade=r["grade"],
        probability_of_default=r["pd"],
        factors=factors[:MAX_FACTORS],
        warnings=_warnings(data),
        missing_data=_missing_data(data),
    )
