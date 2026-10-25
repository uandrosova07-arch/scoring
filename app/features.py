"""Признаки модели logreg-synthetic: из запроса /api/v1/score (формат контракта backend) в вектор чисел.

Один и тот же код используют обучение (ml/train.py) и сервис (app/model.py), поэтому преобразования не расходятся.
Порядок и описание признаков экспортируются в JSON-артефакт для backend (models/*.json, поле features): там же формулы
в виде, удобном для C#. None означает пропуск: его заполняет медиана обучающей выборки, а для признаков с пропусками
в обучении добавляется индикатор пропуска (SimpleImputer(strategy="median", add_indicator=True)).

В признаки не входят: баллы и грейды rules-0.3, скрытая вероятность генератора, латентное качество компании и любые
события после даты заявки. Используются только поля запроса, которые backend присылает на момент подачи.
"""
from __future__ import annotations

import math

# (имя, формула для backend). Порядок важен: это порядок столбцов модели.
FEATURES: tuple[tuple[str, str], ...] = (
    ("log_revenue", "ln(1 + revenue_rub)"),
    ("expense_ratio", "expenses_rub / revenue_rub, обрезать до [0, 2]; null, если expenses_rub = null или revenue_rub = 0"),
    ("profit_margin", "net_profit_rub / revenue_rub, обрезать до [-1, 1]; null, если net_profit_rub = null или revenue_rub = 0"),
    ("debt_load", "(monthly_debt_payments_rub + estimated_monthly_payment_rub) * 12 / net_profit_rub, обрезать до [0, 5]; "
                  "5, если net_profit_rub <= 0; null, если net_profit_rub = null"),
    ("amount_to_revenue", "amount_rub / revenue_rub, обрезать до [0, 5]; 5, если revenue_rub = 0"),
    ("log_amount", "ln(amount_rub)"),
    ("term_months", "term_months"),
    ("log_business_age", "ln(1 + business_age_months)"),
    ("overdue_months", "min(max_overdue_days_24m, 360) / 30; null, если checks.credit_bureau = null"),
    ("write_offs", "1, если has_write_offs, иначе 0; null, если checks.credit_bureau = null"),
    ("no_credit_history", "1, если credit_history_months = 0 и active_loans = 0 и closed_loans = 0, иначе 0; null, если checks.credit_bureau = null"),
    ("log_tax_debt", "ln(1 + max(tax_debt_rub, 0)); null, если checks.fns = null"),
    ("blocked_accounts", "1, если has_blocked_accounts, иначе 0; null, если checks.fns = null"),
    ("enforcement_count", "min(open_proceedings, 10); null, если checks.fssp = null"),
    ("log_enforcement_amount", "ln(1 + open_amount_rub); null, если checks.fssp = null"),
    ("collateral", "1, если application.collateral_offered, иначе 0"),
)
FEATURE_NAMES = tuple(name for name, _ in FEATURES)

# Группы для объяснения: вклад группы = сумма вкладов её признаков и их индикаторов пропуска.
GROUPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue_size": ("Размер выручки", ("log_revenue",)),
    "revenue_to_amount": ("Сумма кредита к выручке", ("amount_to_revenue", "log_amount")),
    "loan_term": ("Срок кредита", ("term_months",)),
    "expense_ratio": ("Расходы к выручке", ("expense_ratio",)),
    "debt_load": ("Долговая нагрузка и прибыльность", ("debt_load", "profit_margin")),
    "business_age": ("Возраст бизнеса", ("log_business_age",)),
    "credit_history": ("Кредитная история", ("overdue_months", "write_offs", "no_credit_history")),
    "tax_debt": ("Налоговая задолженность", ("log_tax_debt", "blocked_accounts")),
    "enforcement": ("Исполнительные производства", ("enforcement_count", "log_enforcement_amount")),
    "collateral": ("Обеспечение", ("collateral",)),
}


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _get(d: dict | None, key: str):
    return None if d is None else d.get(key)


def raw_features(req: dict) -> list[float | None]:
    """Вектор признаков (без заполнения пропусков) из запроса в формате контракта (словарь, как пришёл в JSON)."""
    app, company = req["application"], req["company"]
    fin = company["financials"]
    checks = req.get("checks") or {}
    revenue = float(fin["revenue_rub"])
    expenses, net = fin.get("expenses_rub"), fin.get("net_profit_rub")
    payments = float(fin["monthly_debt_payments_rub"]) + float(app["estimated_monthly_payment_rub"])
    amount = float(app["amount_rub"])

    if net is None:
        debt_load = None
    elif net <= 0:
        debt_load = 5.0
    else:
        debt_load = _clip(payments * 12 / float(net), 0.0, 5.0)

    bureau, fns, fssp = checks.get("credit_bureau"), checks.get("fns"), checks.get("fssp")
    no_history = None if bureau is None else float(
        bureau["credit_history_months"] == 0 and bureau["active_loans"] == 0 and bureau["closed_loans"] == 0)

    return [
        math.log(1 + revenue),
        None if expenses is None or revenue == 0 else _clip(float(expenses) / revenue, 0.0, 2.0),
        None if net is None or revenue == 0 else _clip(float(net) / revenue, -1.0, 1.0),
        debt_load,
        5.0 if revenue == 0 else _clip(amount / revenue, 0.0, 5.0),
        math.log(amount),
        float(app["term_months"]),
        math.log(1 + float(company["business_age_months"])),
        None if bureau is None else min(float(bureau["max_overdue_days_24m"]), 360.0) / 30,
        None if bureau is None else float(bool(bureau["has_write_offs"])),
        no_history,
        None if fns is None else math.log(1 + max(float(fns["tax_debt_rub"]), 0.0)),
        None if fns is None else float(bool(fns["has_blocked_accounts"])),
        None if fssp is None else float(min(int(fssp["open_proceedings"]), 10)),
        None if fssp is None else math.log(1 + float(fssp["open_amount_rub"])),
        float(bool(app.get("collateral_offered", False))),
    ]


def describe(name: str, req: dict) -> str:
    """Значение группы для карточки менеджера: исходные данные заявки, а не преобразованные признаки."""
    app, fin = req["application"], req["company"]["financials"]
    checks = req.get("checks") or {}
    rev = fin["revenue_rub"]
    if name == "revenue_size":
        return f"{rev / 1e6:.1f} млн ₽ в год"
    if name == "revenue_to_amount":
        return "выручки нет" if not rev else f"сумма {app['amount_rub'] / rev:.2f} годовой выручки"
    if name == "loan_term":
        return f"{app['term_months']} мес."
    if name == "expense_ratio":
        return "нет данных" if fin.get("expenses_rub") is None or not rev else f"{fin['expenses_rub'] / rev:.2f}"
    if name == "debt_load":
        net = fin.get("net_profit_rub")
        if net is None:
            return "нет данных о прибыли"
        if net <= 0:
            return "прибыли нет"
        pay = fin["monthly_debt_payments_rub"] + app["estimated_monthly_payment_rub"]
        return f"{pay * 12 / net:.2f}"
    if name == "business_age":
        return f"{req['company']['business_age_months']} мес."
    if name == "credit_history":
        b = checks.get("credit_bureau")
        if b is None:
            return "нет ответа БКИ"
        if b["has_write_offs"]:
            return "есть списания"
        if b["credit_history_months"] == 0 and b["active_loans"] == 0 and b["closed_loans"] == 0:
            return "нет истории"
        return "без просрочек" if b["max_overdue_days_24m"] == 0 else f"просрочка до {b['max_overdue_days_24m']} дн."
    if name == "tax_debt":
        f = checks.get("fns")
        if f is None:
            return "нет ответа ФНС"
        debt = "нет долга" if f["tax_debt_rub"] <= 0 else f"долг {f['tax_debt_rub']:,.0f} ₽".replace(",", " ")
        return debt + (", счета заблокированы" if f["has_blocked_accounts"] else "")
    if name == "enforcement":
        f = checks.get("fssp")
        if f is None:
            return "нет ответа ФССП"
        return "нет" if f["open_proceedings"] == 0 else f"{f['open_proceedings']} на {f['open_amount_rub']:,.0f} ₽".replace(",", " ")
    if name == "collateral":
        return "предложено" if app.get("collateral_offered") else "нет"
    raise KeyError(name)
