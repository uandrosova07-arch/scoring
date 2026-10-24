"""Общие эталонные случаи rules-0.3 для Python-сервиса и резервных C#-правил backend.

    python scripts/conformance.py build    # пересобрать data/conformance/rules-0.3.json
    python scripts/conformance.py check    # сравнить файл с пересборкой: код выхода 1, если отличается
    python scripts/conformance.py diff     # где текущий C# (rules-v1) ещё отличается от rules-0.3

Каждый случай: запрос в формате контракта (как его отправляет backend) и ожидаемый ответ: балл, грейд, PD, факторы
целиком (код, название, вклад, значение, пояснение), предупреждения, missing_data. Ожидаемое считается копией C#
с правками rules-0.3 (scripts/backend_reference.py, agreed=True), а не самим сервисом. Тот же файл читает C#-тест
backend (docs/backend/ScoringConformanceTests.cs): если обе стороны проходят его, на этих входах расчёты одинаковы.
Для справки у каждого случая есть и ответ текущего C# (rules-v1), чтобы было видно, что меняет согласование.
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_reference as br  # noqa: E402
import demo_comparison as dc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "data" / "conformance" / "rules-0.3.json"
BANDS_FILE = ROOT / "data" / "conformance" / "affordability-bands-rules-0.3.json"
LEVEL_BY_IMPACT = {10: "Comfortable", 0: "Moderate", -10: "Elevated", -25: "High"}


def _demo_requests() -> dict[str, dict]:
    _, requests = dc.build_all()
    return requests


def _case(group: str, cid: str, description: str, req: dict) -> dict:
    req = copy.deepcopy(req)
    # GUID, как у backend: C#-тест разбирает запрос в ScoringRequest, где RequestId типа Guid.
    req["request_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"scoring/conformance/{cid}"))
    return {"id": cid, "group": group, "description": description, "input": req}


def cases() -> list[dict]:
    demo = _demo_requests()
    good = demo["demo-good"]
    out = [_case("demo", cid, f"Демо-заявка {dc.TITLES[cid]}", req) for cid, req in demo.items()]

    def variant(cid: str, description: str, group: str = "boundary", **changes) -> None:
        req = copy.deepcopy(good)
        for path, value in changes.items():
            node = req
            *parents, leaf = path.split("__")
            for p in parents:
                node = node[p]
            node[leaf] = value
        out.append(_case(group, cid, description, req))

    fin = "company__financials__"
    # ---- нехватка данных: только то, что backend может прислать как null
    variant("missing-net-profit", "Нет чистой прибыли, расходы есть: прибыль = выручка − расходы", "missing", **{fin + "net_profit_rub": None})
    variant("missing-net-profit-and-expenses", "Нет прибыли и расходов: прибыль = 10 % выручки", "missing",
            **{fin + "net_profit_rub": None, fin + "expenses_rub": None})
    variant("missing-expenses", "Нет расходов, прибыль есть: фактора расходов нет, предупреждение", "missing", **{fin + "expenses_rub": None})
    variant("missing-bureau", "Нет ответа БКИ", "missing", checks__credit_bureau=None)
    variant("missing-fns", "Нет ответа ФНС", "missing", checks__fns=None)
    variant("missing-fssp", "Нет ответа ФССП", "missing", checks__fssp=None)
    variant("missing-egrul", "Нет ответа ЕГРЮЛ: в баллах не участвует, только missing_data", "missing", checks__egrul=None)
    variant("missing-fns-revenue", "ФНС без выручки по отчётности", "missing", checks__fns__reported_revenue_rub=None)
    variant("missing-all-checks", "Нет ни одной проверки", "missing",
            checks__egrul=None, checks__fns=None, checks__credit_bureau=None, checks__fssp=None)

    # ---- возраст бизнеса: <6 −20; 6..11 −10; 12..35 0; >=36 +10
    for m in (5, 6, 11, 12, 35, 36):
        variant(f"age-{m}", f"Возраст бизнеса {m} мес.", company__business_age_months=m)

    # ---- выручка к сумме (сумма 1,5 млн): >=4 +15; >=2 +5; >=1 −5; <1 −20. Расходы держим на 85 % выручки.
    for revenue, label in ((6_000_000, "ровно 4"), (5_999_999, "чуть меньше 4"), (3_000_000, "ровно 2"),
                           (2_999_999, "чуть меньше 2"), (1_500_000, "ровно 1"), (1_499_999, "чуть меньше 1")):
        variant(f"revenue-ratio-{revenue}", f"Выручка к сумме {label}",
                **{fin + "revenue_rub": revenue, fin + "expenses_rub": revenue * 85 // 100})

    # ---- расходы к выручке (выручка 48 млн): >0,90 −10, иначе 0
    for expenses, label in ((43_200_000, "ровно 0,90"), (43_200_001, "чуть больше 0,90"), (0, "нет расходов (0)")):
        variant(f"expense-ratio-{expenses}", f"Расходы к выручке {label}", **{fin + "expenses_rub": expenses})

    # ---- нагрузка: (платежи + 138 593) * 12 / 7 200 000; <0,3 +10; <0,5 0; <0,7 −10; >=0,7 −25
    for debt, label in ((41_406, "чуть меньше 0,3"), (41_407, "ровно 0,3"), (161_406, "чуть меньше 0,5"),
                        (161_407, "ровно 0,5"), (281_406, "чуть меньше 0,7"), (281_407, "ровно 0,7")):
        variant(f"debt-load-{debt}", f"Долговая нагрузка {label}", **{fin + "monthly_debt_payments_rub": debt})
    # Граница 0,3 при годовой прибыли, не кратной 12 (месячная прибыль бесконечной дробью)
    variant("debt-load-0.3-profit-not-multiple-of-12", "Нагрузка ровно 0,3, прибыль 8 млн (не делится на 12 без остатка)",
            **{fin + "monthly_debt_payments_rub": 61_407, fin + "net_profit_rub": 8_000_000})
    variant("profit-zero", "Чистая прибыль 0", **{fin + "net_profit_rub": 0})
    variant("profit-negative", "Чистая прибыль отрицательная", **{fin + "net_profit_rub": -1_000_000})
    variant("profit-tiny", "Прибыль есть, но нагрузка больше 9,99", **{fin + "net_profit_rub": 100_000})
    variant("profit-derived-negative", "Нет прибыли, расходы больше выручки", "missing",
            **{fin + "net_profit_rub": None, fin + "expenses_rub": 50_000_000})

    # ---- БКИ: без просрочек +15; до 30 дн. −5; 31..90 −15; >90 или списания −30; истории нет 0
    for days in (1, 30, 31, 90, 91):
        variant(f"overdue-{days}", f"Максимальная просрочка {days} дн.", checks__credit_bureau__max_overdue_days_24m=days)
    variant("write-offs", "Списания без просрочки", checks__credit_bureau__has_write_offs=True)
    variant("no-credit-history", "Кредитной истории нет",
            checks__credit_bureau__credit_history_months=0, checks__credit_bureau__active_loans=0, checks__credit_bureau__closed_loans=0)
    variant("history-only-active-loan", "История 0 мес., но есть действующий кредит",
            checks__credit_bureau__credit_history_months=0, checks__credit_bureau__closed_loans=0)

    # ---- налоги: нет +5; <50 000 −5; >=50 000 −15; блокировка ещё −15
    for debt in (1, 49_999, 50_000):
        variant(f"tax-debt-{debt}", f"Налоговая задолженность {debt} ₽", checks__fns__tax_debt_rub=debt)
    variant("tax-blocked-no-debt", "Счета заблокированы, долга нет", checks__fns__has_blocked_accounts=True)
    variant("tax-blocked-with-debt", "Долг 700 000 ₽ и блокировка",
            checks__fns__tax_debt_rub=700_000, checks__fns__has_blocked_accounts=True)

    # ---- ФССП и залог
    variant("fssp-one", "Одно исполнительное производство", checks__fssp__open_proceedings=1, checks__fssp__open_amount_rub=10_000)
    variant("collateral-clamp-100", "Залог: 95 + 10 упирается в 100", application__collateral_offered=True)

    # ---- границы грейдов: 80/75, 65/60, 50/45, 35/30 (баллы кратны 5). База «Ромашка» 95; сдвиги относительно базы:
    # БКИ: нет истории −15, просрочка до 30 −20, 31..90 −30, >90 −45; налоги: долг <50 000 −10, >=50 000 −20,
    # блокировка ещё −15; возраст 12..35 −10.
    overdue, tax, blocked = "checks__credit_bureau__max_overdue_days_24m", "checks__fns__tax_debt_rub", "checks__fns__has_blocked_accounts"
    grade_points = {
        80: {"checks__credit_bureau__credit_history_months": 0, "checks__credit_bureau__active_loans": 0,
             "checks__credit_bureau__closed_loans": 0},                       # 95 − 15
        75: {overdue: 1},                                                     # 95 − 20
        65: {overdue: 1, tax: 1},                                             # 95 − 20 − 10
        60: {overdue: 1, blocked: True},                                      # 95 − 20 − 15
        50: {overdue: 91},                                                    # 95 − 45
        45: {overdue: 31, tax: 50_000},                                       # 95 − 30 − 20
        35: {overdue: 31, tax: 50_000, "company__business_age_months": 12},  # 95 − 30 − 20 − 10
        30: {overdue: 31, tax: 50_000, blocked: True},                        # 95 − 30 − 35
    }
    for target, changes in grade_points.items():
        variant(f"grade-score-{target}", f"Граница грейда: ровно {target} баллов", **changes)
    return out


def _expected(req: dict, agreed: bool) -> dict:
    r = br.score_rules_v1(req, agreed=agreed)
    return {
        "score": r["score"],
        "risk_grade": r["risk_grade"],
        "probability_of_default": float(r["probability_of_default"]),
        "factors": r["factors"],
        "warnings": r["warnings"],
        "missing_data": r["missing_data"],
        "model_version": r["model_version"],
    }


def build_all() -> dict:
    items = []
    for c in cases():
        req = c["input"]
        agreed, v1 = _expected(req, True), _expected(req, False)
        c["expected"] = agreed
        c["current_csharp_rules_v1"] = {"score": v1["score"], "risk_grade": v1["risk_grade"],
                                         "factors": [f"{f['code']}:{f['impact']}" for f in v1["factors"]], "warnings": v1["warnings"]}
        items.append(c)
    return {
        "meta": {
            "model_version": br.AGREED_MODEL_VERSION,
            "backend_commit": br.BACKEND_COMMIT,
            "note": "Эталон для Python (tests/test_conformance.py) и C# (docs/backend/ScoringConformanceTests.cs). "
                    "expected посчитан копией C# с правками rules-0.3, а не сервисом. current_csharp_rules_v1: ответ кода "
                    "backend до правок, для справки. Собрано scripts/conformance.py build.",
            "compare": "score, risk_grade, probability_of_default, factors (code, title, impact, value, client_explanation в этом порядке), "
                       "warnings и missing_data в этом порядке",
        },
        "cases": items,
    }


def build_bands(data: dict) -> dict:
    """Эталон уровней нагрузки для предварительного подбора: те же входы, уровень по вкладу debt_load копии C# rules-0.3.

    Нет ни чистой прибыли, ни расходов: InsufficientData и null во всех числах (оценка в этом случае берёт 10 % выручки,
    preview нет). Иначе прибыль = чистая прибыль или выручка − расходы; прибыль <= 0: High, debt_load = null.
    """
    from decimal import Decimal
    rows = []
    for c in data["cases"]:
        a, fin = c["input"]["application"], c["input"]["company"]["financials"]
        net, rev, exp = fin["net_profit_rub"], fin["revenue_rub"], fin["expenses_rub"]
        payments = fin["monthly_debt_payments_rub"] + a["estimated_monthly_payment_rub"]
        row = {"id": c["id"], "revenue_rub": rev, "expenses_rub": exp, "net_profit_rub": net,
               "monthly_debt_payments_rub": fin["monthly_debt_payments_rub"], "estimated_monthly_payment_rub": a["estimated_monthly_payment_rub"]}
        if net is None and exp is None:
            row["expected"] = {"level": "InsufficientData", "debt_load": None, "monthly_profit_rub": None,
                               "monthly_payments_rub": None, "profit_basis": None}
        else:
            profit = Decimal(net) if net is not None else Decimal(rev) - Decimal(exp)
            impact = next(f["impact"] for f in c["expected"]["factors"] if f["code"] == "debt_load")
            row["expected"] = {
                "level": LEVEL_BY_IMPACT[impact],
                "debt_load": None if profit <= 0 else float(round(Decimal(payments) * 12 / profit, 4)),
                "monthly_profit_rub": float(round(profit / 12, 2)),
                "monthly_payments_rub": payments,
                "profit_basis": "net_profit" if net is not None else "revenue_minus_expenses",
            }
        rows.append(row)
    return {
        "meta": {"model_version": br.AGREED_MODEL_VERSION,
                 "bands": "debt_load < 0.3 Comfortable; 0.3 <= x < 0.5 Moderate; 0.5 <= x < 0.7 Elevated; x >= 0.7 или прибыль <= 0 High",
                 "formula": "debt_load = (monthly_debt_payments_rub + estimated_monthly_payment_rub) * 12 / годовая прибыль; "
                            "прибыль = net_profit_rub, иначе revenue_rub - expenses_rub, иначе InsufficientData",
                 "note": "Входы из data/conformance/rules-0.3.json. Уровень по вкладу debt_load копии C# rules-0.3: +10, 0, −10, −25. "
                         "Собрано scripts/conformance.py build."},
        "cases": rows,
    }


def _dump(obj) -> str:
    return json.dumps(br.to_json_value(obj), ensure_ascii=False, indent=2) + "\n"


def main(cmd: str) -> int:
    data = build_all()
    bands = build_bands(data)
    if cmd == "build":
        FILE.parent.mkdir(exist_ok=True)
        FILE.write_text(_dump(data), encoding="utf-8")
        BANDS_FILE.write_text(_dump(bands), encoding="utf-8")
        print(f"записано: {FILE.relative_to(ROOT)} ({len(data['cases'])} случаев), {BANDS_FILE.relative_to(ROOT)}")
        return 0
    if cmd == "check":
        same = FILE.exists() and FILE.read_text(encoding="utf-8") == _dump(data)
        same_bands = BANDS_FILE.exists() and BANDS_FILE.read_text(encoding="utf-8") == _dump(bands)
        print("эталоны совпадают с пересборкой" if same and same_bands else "эталон отличается от пересборки")
        return 0 if same and same_bands else 1
    if cmd == "diff":
        for c in data["cases"]:
            e, v1 = c["expected"], c["current_csharp_rules_v1"]
            factors = [f"{f['code']}:{f['impact']}" for f in e["factors"]]
            if (e["score"], e["risk_grade"]) != (v1["score"], v1["risk_grade"]):
                print(f"{c['id']:<40} rules-0.3 {e['score']}/{e['risk_grade']}  rules-v1 {v1['score']}/{v1['risk_grade']}  ГРЕЙД/БАЛЛ")
            elif factors != v1["factors"] or e["warnings"] != v1["warnings"]:
                print(f"{c['id']:<40} балл и грейд те же ({e['score']}/{e['risk_grade']}), отличаются факторы или предупреждения")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "check"))
