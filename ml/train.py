"""Обучение logreg-synthetic: SimpleImputer(median, индикаторы пропусков) → StandardScaler → LogisticRegression.

    python -m ml.train            # генерирует данные (seed 20261025), обучает, пишет models/ и docs/model-metrics.md

Шаги:
1. Данные из ml.generate (тот же seed → тот же набор, sha256 записывается в метрики).
2. Признаки app.features.raw_features по запросу в формате контракта: только наблюдаемые поля заявки.
   Скрытые столбцы генератора (_quality, _true_pd, _true_profit) в запрос не попадают; это проверяется.
3. Разбиение по компаниям (GroupShuffleSplit): train 70 % / validation 15 % / test 15 %, компании не пересекаются.
4. C подбирается по log loss на validation, модель обучается на train; test используется один раз, для итоговых метрик.
5. Экспорт: models/logreg-synthetic-0.1.json (всё для расчёта без sklearn), метрики, контрольные входы с ожидаемыми ответами.
"""
from __future__ import annotations

import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.features import FEATURES, FEATURE_NAMES, GROUPS, raw_features
from ml.generate import SEED, dataset_sha256, generate, to_request

ROOT = Path(__file__).resolve().parent.parent
MODEL_VERSION = "logreg-synthetic-0.1"
ARTIFACT = ROOT / "models" / f"{MODEL_VERSION}.json"
METRICS = ROOT / "models" / f"{MODEL_VERSION}.metrics.json"
CONTROL = ROOT / "data" / "conformance" / f"{MODEL_VERSION}.json"
METRICS_MD = ROOT / "docs" / "model-metrics.md"
SPLIT_SEED = 42
C_GRID = (0.003, 0.01, 0.03, 0.1, 0.3, 1.0, 3.0)
# PD → балл: линейно, как обратная к эвристике backend PD = 0,02 + (100 − балл) / 100 × 0,30.
# Балл 100 при PD ≤ 2 %, 0 при PD ≥ 32 %; границы грейдов A 80 / B 65 / C 50 / D 35 соответствуют PD 8 / 12,5 / 17 / 21,5 %.
SCORE_PARAMS = {"pd_at_score_100": 0.02, "pd_at_score_0": 0.32, "formula": "score = clamp(round_half_up(100 * (pd_at_score_0 - pd) / (pd_at_score_0 - pd_at_score_100)), 0, 100)"}
GRADES = [{"grade": "A", "min_score": 80}, {"grade": "B", "min_score": 65}, {"grade": "C", "min_score": 50},
          {"grade": "D", "min_score": 35}, {"grade": "E", "min_score": 0}]


def build_matrix(df: pd.DataFrame) -> tuple[np.ndarray, list[dict]]:
    requests = [to_request(row) for row in df.to_dict("records")]
    hidden = [c for c in df.columns if c.startswith("_")] + ["default_12m"]
    for r in requests[:200]:  # в запросе только наблюдаемые поля: ни скрытых столбцов генератора, ни исхода
        text = json.dumps(r)
        assert not any(f'"{h}"' in text for h in hidden), "скрытое поле в запросе"
    X = np.array([[np.nan if v is None else v for v in raw_features(r)] for r in requests], dtype=float)
    return X, requests


def split(df: pd.DataFrame) -> dict[str, np.ndarray]:
    groups = df["company_id"].to_numpy()
    idx = np.arange(len(df))
    rest, test = next(GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=SPLIT_SEED).split(idx, groups=groups))
    tr, va = next(GroupShuffleSplit(n_splits=1, test_size=0.15 / 0.85, random_state=SPLIT_SEED).split(rest, groups=groups[rest]))
    parts = {"train": rest[tr], "validation": rest[va], "test": test}
    sets = {k: set(groups[v]) for k, v in parts.items()}
    assert not (sets["train"] & sets["validation"] or sets["train"] & sets["test"] or sets["validation"] & sets["test"])
    return parts


def make_pipeline(c: float) -> Pipeline:
    return Pipeline([
        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
        ("scale", StandardScaler()),
        ("logreg", LogisticRegression(C=c, solver="lbfgs", max_iter=5000)),
    ])


def score_from_pd(pd_: float) -> int:
    s = 100 * (SCORE_PARAMS["pd_at_score_0"] - pd_) / (SCORE_PARAMS["pd_at_score_0"] - SCORE_PARAMS["pd_at_score_100"])
    return max(0, min(100, math.floor(s + 0.5)))


def grade_for(score: int) -> str:
    return next(g["grade"] for g in GRADES if score >= g["min_score"])


def metrics(y: np.ndarray, p: np.ndarray, base_rate: float) -> dict:
    const = np.full_like(p, base_rate)
    return {
        "n": int(len(y)), "default_rate": round(float(y.mean()), 5),
        "model": {"roc_auc": round(roc_auc_score(y, p), 4), "average_precision": round(average_precision_score(y, p), 4),
                  "log_loss": round(log_loss(y, p), 4), "brier": round(brier_score_loss(y, p), 5), "mean_pd": round(float(p.mean()), 5)},
        "constant": {"roc_auc": 0.5, "average_precision": round(float(y.mean()), 4),
                     "log_loss": round(log_loss(y, const, labels=[0, 1]), 4), "brier": round(brier_score_loss(y, const), 5),
                     "mean_pd": round(base_rate, 5)},
    }


def export_artifact(pipe: Pipeline, c: float, data_sha: str) -> dict:
    imp, sc, lr = pipe.named_steps["impute"], pipe.named_steps["scale"], pipe.named_steps["logreg"]
    indicator_features = [FEATURE_NAMES[i] for i in imp.indicator_.features_]
    columns = list(FEATURE_NAMES) + [f"missing:{n}" for n in indicator_features]
    assert len(columns) == len(sc.mean_) == lr.coef_.shape[1]
    baseline_pd = 1 / (1 + math.exp(-float(lr.intercept_[0])))
    points_per_logit = 100 / (SCORE_PARAMS["pd_at_score_0"] - SCORE_PARAMS["pd_at_score_100"]) * baseline_pd * (1 - baseline_pd)
    features = [{"name": n, "formula": f, "impute_value": float(imp.statistics_[i]), "missing_indicator": n in indicator_features}
                for i, (n, f) in enumerate(FEATURES)]
    return {
        "model_version": MODEL_VERSION,
        "kind": "logistic_regression",
        "target": "default_12m: условный дефолт в течение 12 месяцев после выдачи (синтетические данные)",
        "training": {"data": "ml/generate.py", "seed": SEED, "companies": 20_000, "dataset_sha256": data_sha, "split_seed": SPLIT_SEED,
                     "selected_C": c, "solver": "lbfgs", "penalty": "l2", "sklearn": sklearn.__version__, "python": platform.python_version()},
        "input": "запрос POST /api/v1/score в формате контракта backend (docs/scoring-contract.md)",
        "features": features,
        "missing_indicators": {"order": indicator_features,
                               "rule": "для каждого признака из order добавляется столбец missing:<имя> = 1, если значение признака null, иначе 0"},
        "columns": columns,
        "imputation": "null заменяется на impute_value (медиана train) до масштабирования",
        "scaling": {"mean": dict(zip(columns, map(float, sc.mean_))), "scale": dict(zip(columns, map(float, sc.scale_))),
                    "rule": "z = (x - mean) / scale для каждого столбца columns, включая индикаторы"},
        "coefficients": dict(zip(columns, map(float, lr.coef_[0]))),
        "intercept": float(lr.intercept_[0]),
        "pd": "pd = 1 / (1 + exp(-(intercept + sum(coefficients[c] * z[c]))))",
        "score": SCORE_PARAMS,
        "grades": GRADES,
        "explanation": {
            "groups": {k: {"title": t, "features": list(f)} for k, (t, f) in GROUPS.items()},
            "baseline_pd": baseline_pd,
            "points_per_logit": points_per_logit,
            "method": "contribution[g] = сумма coefficients[c] * z[c] по столбцам группы и их missing:-индикаторам "
                      "(вклад в логит относительно средней заявки train, у которой все z = 0); "
                      "impact[g] = clamp(round_half_away_from_zero(-points_per_logit * contribution[g]), -100, 100), "
                      "где points_per_logit = 100 / (pd_at_score_0 - pd_at_score_100) * baseline_pd * (1 - baseline_pd), "
                      "baseline_pd = sigmoid(intercept): наклон шкалы баллов у средней заявки. Это линейное приближение: "
                      "вклад не обрезается границами 0 и 100, но сумма impact не равна разнице баллов точно. "
                      "В ответе до 7 групп по убыванию |impact|, при равенстве по коду",
        },
    }


def main() -> None:
    df = generate()
    data_sha = dataset_sha256(df)
    X, requests = build_matrix(df)
    y = df["default_12m"].to_numpy()
    parts = split(df)
    Xtr, ytr = X[parts["train"]], y[parts["train"]]
    Xva, yva = X[parts["validation"]], y[parts["validation"]]

    search = []
    for c in C_GRID:
        p = make_pipeline(c).fit(Xtr, ytr).predict_proba(Xva)[:, 1]
        search.append({"C": c, "val_log_loss": round(log_loss(yva, p), 5), "val_roc_auc": round(roc_auc_score(yva, p), 4)})
    best_c = min(search, key=lambda r: (r["val_log_loss"], r["C"]))["C"]
    pipe = make_pipeline(best_c).fit(Xtr, ytr)
    base_rate = float(ytr.mean())

    result = {"model_version": MODEL_VERSION, "dataset_sha256": data_sha, "rows": int(len(df)), "companies": int(df["company_id"].nunique()),
              "split": {k: {"rows": int(len(v)), "companies": int(df["company_id"].iloc[v].nunique())} for k, v in parts.items()},
              "companies_overlap": 0, "C_search": search, "selected_C": best_c, "constant_pd": round(base_rate, 5)}
    for name in ("train", "validation", "test"):
        idx = parts[name]
        result[name] = metrics(y[idx], pipe.predict_proba(X[idx])[:, 1], base_rate)

    # Грейды на test: доля, наблюдаемая частота дефолтов, средняя PD
    test = parts["test"]
    pd_test = pipe.predict_proba(X[test])[:, 1]
    grades = np.array([grade_for(score_from_pd(p)) for p in pd_test])
    result["test_by_grade"] = [{"grade": g, "share": round(float((grades == g).mean()), 4),
                                "default_rate": round(float(y[test][grades == g].mean()), 4) if (grades == g).any() else None,
                                "mean_pd": round(float(pd_test[grades == g].mean()), 4) if (grades == g).any() else None}
                               for g in "ABCDE"]
    deciles = pd.qcut(pd_test, 10, labels=False, duplicates="drop")
    result["test_calibration_deciles"] = [{"decile": int(d) + 1, "mean_pd": round(float(pd_test[deciles == d].mean()), 4),
                                           "default_rate": round(float(y[test][deciles == d].mean()), 4), "n": int((deciles == d).sum())}
                                          for d in sorted(set(deciles))]
    result["hidden_pd_test_roc_auc"] = round(roc_auc_score(y[test], df["_true_pd"].to_numpy()[test]), 4)

    artifact = export_artifact(pipe, best_c, data_sha)
    ARTIFACT.parent.mkdir(exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    METRICS.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_control_cases(pipe, df, requests, parts)
    METRICS_MD.write_text(metrics_markdown(result), encoding="utf-8")
    t = result["test"]
    print(f"C={best_c}; test ROC-AUC {t['model']['roc_auc']} (скрытая PD {result['hidden_pd_test_roc_auc']}), AP {t['model']['average_precision']} "
          f"vs {t['constant']['average_precision']}, log loss {t['model']['log_loss']} vs {t['constant']['log_loss']}, "
          f"Brier {t['model']['brier']} vs {t['constant']['brier']}")
    print("грейды на test:", result["test_by_grade"])


def write_control_cases(pipe: Pipeline, df: pd.DataFrame, requests: list[dict], parts: dict) -> None:
    """Контрольные входы для backend: ожидаемая PD считается sklearn (predict_proba), а не кодом сервиса."""
    demo = json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]
    cases = [{"id": c["id"], "description": f"Демо-заявка: {c['title']}", "input": c["input"]} for c in demo]
    rng = np.random.default_rng(7)
    test = parts["test"]
    pd_test = pipe.predict_proba(np.array([[np.nan if v is None else v for v in raw_features(requests[i])] for i in test]))[:, 1]
    grades = np.array([grade_for(score_from_pd(p)) for p in pd_test])
    for g in "ABCDE":  # по 4 заявки каждого грейда из test
        pool = test[grades == g]
        for i in rng.choice(pool, size=min(4, len(pool)), replace=False):
            cases.append({"id": f"test-{g}-{int(df['application_id'].iloc[i])}", "description": f"Заявка из test, грейд {g}", "input": requests[i]})
    base = demo[0]["input"]
    for name, change in (("no-net-profit", ("company", "financials", "net_profit_rub", None)),
                         ("no-expenses", ("company", "financials", "expenses_rub", None)),
                         ("no-bureau", ("checks", "credit_bureau", None)), ("no-fns", ("checks", "fns", None)),
                         ("no-fssp", ("checks", "fssp", None)), ("negative-profit", ("company", "financials", "net_profit_rub", -1_000_000)),
                         ("collateral", ("application", "collateral_offered", True))):
        req = json.loads(json.dumps(base))
        node = req
        *path, key, value = change
        for p in path:
            node = node[p]
        node[key] = value
        cases.append({"id": f"edge-{name}", "description": f"«Ромашка» с изменением: {name}", "input": req})
    X = np.array([[np.nan if v is None else v for v in raw_features(c["input"])] for c in cases], dtype=float)
    pds = pipe.predict_proba(X)[:, 1]
    z = pipe.named_steps["scale"].transform(pipe.named_steps["impute"].transform(X))
    logits = pipe.named_steps["logreg"].decision_function(z)
    # Объяснение, предупреждения и missing_data считает код сервиса по только что записанному артефакту
    # (sklearn объяснений не даёт); PD и логит сверяются с sklearn выше и в tests/test_model.py.
    from app import model as service
    from app.schemas import ScoreRequest
    service.load.cache_clear()
    for c, p, lg in zip(cases, pds, logits):
        s = score_from_pd(float(p))
        resp = service.score(ScoreRequest.model_validate(c["input"]), MODEL_VERSION)
        assert resp.score == s and abs(resp.probability_of_default - float(p)) < 1e-12, c["id"]
        c["expected"] = {"probability_of_default": float(p), "logit": float(lg), "score": s, "risk_grade": grade_for(s),
                         "features": dict(zip(FEATURE_NAMES, raw_features(c["input"]))),
                         "factors": [f.model_dump() for f in resp.factors],
                         "warnings": resp.warnings, "missing_data": resp.missing_data}
    # PD → балл отдельно, вплотную к границам грейдов: так backend проверит округление без модели.
    mapping = []
    for pd_ in (0.0, 0.01, 0.02, 0.05, 0.08, 0.0815, 0.0816, 0.125, 0.1265, 0.17, 0.1715, 0.215, 0.2165, 0.30, 0.32, 0.5, 1.0):
        sc = score_from_pd(pd_)
        mapping.append({"probability_of_default": pd_, "score": sc, "risk_grade": grade_for(sc)})
    CONTROL.write_text(json.dumps({"meta": {"model_version": MODEL_VERSION,
                                            "tolerance": {"probability_of_default": 1e-9, "logit": 1e-9, "features": 1e-12},
                                            "note": "Ожидаемые PD и логит посчитаны sklearn (Pipeline.predict_proba / decision_function), балл и грейд "
                                                    "по параметрам артефакта; features — вектор до заполнения пропусков (null = пропуск); factors, "
                                                    "warnings и missing_data — ответ сервиса по артефакту. Собрано python -m ml.train."},
                                   "score_mapping": mapping, "cases": cases}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def metrics_markdown(r: dict) -> str:
    rows = []
    for part in ("train", "validation", "test"):
        m, c = r[part]["model"], r[part]["constant"]
        rows.append(f"| {part} | {r[part]['n']} | {r[part]['default_rate']:.2%} | {m['roc_auc']} / 0.5 | {m['average_precision']} / {c['average_precision']} "
                    f"| {m['log_loss']} / {c['log_loss']} | {m['brier']} / {c['brier']} |")
    grades = "\n".join(f"| {g['grade']} | {g['share']:.1%} | {g['mean_pd'] if g['mean_pd'] is not None else '—'} | "
                       f"{g['default_rate'] if g['default_rate'] is not None else '—'} |" for g in r["test_by_grade"])
    calib = "\n".join(f"| {d['decile']} | {d['n']} | {d['mean_pd']} | {d['default_rate']} |" for d in r["test_calibration_deciles"])
    search = "\n".join(f"| {s['C']} | {s['val_log_loss']} | {s['val_roc_auc']} |" for s in r["C_search"])
    return f"""# Метрики {r['model_version']}

Собрано `python -m ml.train`. Данные синтетические (`ml/generate.py`, sha256 `{r['dataset_sha256'][:16]}…`): {r['rows']} заявок,
{r['companies']} компаний. Разбиение по компаниям без пересечений: train {r['split']['train']['rows']} заявок ({r['split']['train']['companies']} компаний),
validation {r['split']['validation']['rows']} ({r['split']['validation']['companies']}), test {r['split']['test']['rows']} ({r['split']['test']['companies']}).
Постоянный прогноз — доля дефолтов в train: {r['constant_pd']:.2%}.

## Качество (модель / постоянный прогноз)

| Выборка | Заявок | Дефолтов | ROC-AUC | Average precision | Log loss | Brier |
|---|---|---|---|---|---|---|
{chr(10).join(rows)}

Потолок на этих данных: ROC-AUC скрытой вероятности генератора на test — {r['hidden_pd_test_roc_auc']}
(часть риска генератор задаёт ненаблюдаемым качеством компании и шумом, его не может знать никакая модель).

## Выбор C (validation)

| C | Log loss | ROC-AUC |
|---|---|---|
{search}

Выбран C = {r['selected_C']} (минимальный log loss на validation). Test использован один раз.

## Грейды на test

| Грейд | Доля заявок | Средняя PD | Доля дефолтов |
|---|---|---|---|
{grades}

## Калибровка на test (децили PD)

| Дециль | Заявок | Средняя PD | Доля дефолтов |
|---|---|---|---|
{calib}
"""


if __name__ == "__main__":
    main()
