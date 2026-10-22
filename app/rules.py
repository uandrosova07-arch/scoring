"""Воспроизводимые правила оценки. Чистые функции: без времени, случайности и обращения в сеть.

Балл стартует с 50 и меняется на вклад каждого фактора, затем ограничивается диапазоном 0..100.
Грейды совпадают с таблицей backend (A 80..100, B 65..79, C 50..64, D 35..49, E 0..34).
Пороги и баллы предварительные: их нужно согласовать с продактом и бэкендером как условия демонстрации.
"""
from app.schemas import Factor, Financials, ScoreRequest, ScoreResponse


MODEL_VERSION = "rules-0.2"

BASE_SCORE = 50
GRADES = ((80, "A"), (65, "B"), (50, "C"), (35, "D"), (0, "E"))


REQUIRED_FINANCIALS = ("revenue_rub", "expenses_rub", "monthly_debt_payments_rub")

INSUFFICIENT_SCORE = 40


EXPENSE_RATIO_OK_BELOW, EXPENSE_RATIO_MODERATE_UP_TO = 0.70, 0.90
EXPENSE_POINTS = {"ok": 15, "moderate": -5, "high": -20}


DEBT_LOAD_LOW_BELOW, DEBT_LOAD_MEDIUM_UP_TO = 0.30, 0.70
DEBT_POINTS = {"low": 20, "medium": 0, "high": -20}
NO_PROFIT_POINTS = -35


AGE_POINTS = ((6, -20), (12, -10), (36 + 1, 0))  
AGE_POINTS_OLDER = 10  

NO_DATA = "нет данных"
_ORDER = {"factor_expense_ratio": 0, "factor_debt_load": 1, "factor_business_age": 2, "factor_insufficient_data": 3}


def _pct(x: float) -> str:
    return f"{int(x * 100 + 0.5)}%"  


def grade_for(score: int) -> str:
    for lower, grade in GRADES:
        if score >= lower:
            return grade
    return "E"


def _age_factor(months: int | None, informational: bool) -> Factor:
    title = "Возраст бизнеса"
    if months is None:
        return Factor(code="factor_business_age", title=title, impact=0, value=NO_DATA,
                      client_explanation="Возраст бизнеса не передан, в оценке он не учтён")
    impact = AGE_POINTS_OLDER
    for upper, points in AGE_POINTS:
        if months < upper:
            impact = points
            break
    if informational:
        text = "Пока не хватает данных, поэтому возраст бизнеса в оценке не учтён"
        impact = 0
    elif impact > 0:
        text = "Бизнесу больше трёх лет, это снижает риск"
    elif impact == 0:
        text = "Возраст бизнеса в обычных пределах"
    else:
        text = "Бизнес работает недолго, это повышает риск"
    return Factor(code="factor_business_age", title=title, impact=impact, value=f"{months} мес.",
                  client_explanation=text)


def _expense_factor(f: Financials, informational: bool) -> tuple[Factor, float | None]:
    title = "Расходы к выручке"
    if f.revenue_rub is None or f.expenses_rub is None:
        return Factor(code="factor_expense_ratio", title=title, impact=0, value=NO_DATA,
                      client_explanation="Не хватает данных о выручке или расходах"), None
    ratio = f.expenses_rub / f.revenue_rub
    level = "ok" if ratio < EXPENSE_RATIO_OK_BELOW else "moderate" if ratio <= EXPENSE_RATIO_MODERATE_UP_TO else "high"
    text = {
        "ok": "Расходы заметно ниже выручки",
        "moderate": "Расходы занимают значительную часть выручки",
        "high": "Расходы почти равны выручке или превышают её",
    }[level]
    if informational:
        return Factor(code="factor_expense_ratio", title=title, impact=0, value=_pct(ratio),
                      client_explanation="Пока не хватает данных, поэтому этот показатель в оценке не учтён"), ratio
    return Factor(code="factor_expense_ratio", title=title, impact=EXPENSE_POINTS[level], value=_pct(ratio),
                  client_explanation=text), ratio


def _debt_factor(req: ScoreRequest, f: Financials) -> Factor:
    title = "Кредитная нагрузка"
    profit = f.net_profit_rub if f.net_profit_rub is not None else f.revenue_rub - f.expenses_rub
    if profit <= 0:
        return Factor(code="factor_debt_load", title=title, impact=NO_PROFIT_POINTS, value="нет прибыли",
                      client_explanation="За последние 12 месяцев нет чистой прибыли")
    new_payment = (
        req.application.estimated_monthly_payment_rub
        if req.application.estimated_monthly_payment_rub is not None
        else req.application.amount_rub / req.application.term_months
    )
    load = (f.monthly_debt_payments_rub + new_payment) / (profit / 12)
    level = "low" if load < DEBT_LOAD_LOW_BELOW else "medium" if load <= DEBT_LOAD_MEDIUM_UP_TO else "high"
    text = {
        "low": "Платежи по кредитам, включая новый, составляют небольшую часть прибыли",
        "medium": "Платежи по кредитам, включая новый, составляют заметную часть прибыли",
        "high": "Платежи по кредитам, включая новый, занимают слишком большую часть прибыли",
    }[level]
    return Factor(code="factor_debt_load", title=title, impact=DEBT_POINTS[level], value=_pct(load),
                  client_explanation=text)


def _warnings(req: ScoreRequest, insufficient: bool) -> list[str]:
    out = []
    if insufficient:
        out.append("insufficient_data")
    if req.company.business_age_months is None:
        out.append("business_age_missing")
    checks = req.checks
    for name in ("egrul", "fns", "credit_bureau", "fssp"):
        if checks is None or getattr(checks, name) is None:
            out.append(f"{name}_missing")
    return out


def score(req: ScoreRequest) -> ScoreResponse:
    f = req.company.financials or Financials()
    missing = [f"company.financials.{n}" for n in REQUIRED_FINANCIALS if getattr(f, n) is None]
    insufficient = bool(missing)

    expense, _ = _expense_factor(f, informational=insufficient)
    age = _age_factor(req.company.business_age_months, informational=insufficient)
    factors = [expense, age]
    if insufficient:
        factors.append(Factor(
            code="factor_insufficient_data", title="Достаточность данных", impact=INSUFFICIENT_SCORE - BASE_SCORE,
            value="не хватает: " + ", ".join(m.rsplit(".", 1)[1] for m in missing),
            client_explanation="Для автоматической оценки не хватает данных, заявку рассмотрит менеджер",
        ))
        factors.append(Factor(code="factor_debt_load", title="Кредитная нагрузка", impact=0, value=NO_DATA,
                              client_explanation="Пока не хватает данных, поэтому этот показатель в оценке не учтён"))
        total = INSUFFICIENT_SCORE
    else:
        factors.append(_debt_factor(req, f))
        total = max(0, min(100, BASE_SCORE + sum(x.impact for x in factors)))

    
    factors.sort(key=lambda x: (-abs(x.impact), _ORDER[x.code]))
    return ScoreResponse(
        request_id=req.request_id,
        model_version=MODEL_VERSION,
        score=total,
        risk_grade=grade_for(total),
        probability_of_default=None,
        factors=factors,
        warnings=_warnings(req, insufficient),
        missing_data=missing,
    )
