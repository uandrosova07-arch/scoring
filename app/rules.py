"""Правила оценки rules-0.3. Чистые функции: без времени, случайности и обращения в сеть.

Факторы, пороги, баллы, формулировки и обработка пропусков совпадают с резервными правилами backend
(RuleBasedScoringService.cs, rules-v1) плюс согласованное правило для расходов. Полная спецификация:
docs/calculation.md. Эталонные случаи, общие для Python и C#: data/conformance/rules-0.3.json.

Арифметика точная (Fraction): пороги сравниваются без ошибок округления, поэтому на границах ответ совпадает
с decimal-расчётом backend. Значения для карточки округляются до нужного знака половиной от нуля, как форматирование .NET.
"""
from fractions import Fraction

from app.schemas import Checks, Factor, ScoreRequest, ScoreResponse


MODEL_VERSION = "rules-0.3"

BASE_SCORE = 50
GRADES = ((80, "A"), (65, "B"), (50, "C"), (35, "D"), (0, "E"))


EXPENSE_RATIO_HIGH_ABOVE = Fraction(9, 10) 
EXPENSE_HIGH_POINTS = -10
PROFIT_SHARE_IF_UNKNOWN = Fraction(1, 10) 
NO_PROFIT_LOAD = Fraction(999, 100) 
TAX_DEBT_SMALL_BELOW = 50_000


def _frac(x: float | int) -> Fraction:
    return Fraction(repr(x)) if isinstance(x, float) else Fraction(x)


def _fmt(x: Fraction, digits: int) -> str:
    """Как форматы "0.0" и "0.00" в .NET: половина от нуля, точка как разделитель."""
    scaled = abs(x) * 10**digits
    n = int(scaled + Fraction(1, 2))
    sign = "-" if x < 0 and n else ""
    whole, frac = divmod(n, 10**digits)
    return f"{sign}{whole}.{frac:0{digits}d}" if digits else f"{sign}{whole}"


def _rub(x: Fraction) -> str:
    """Money.FormatRub: «1 400 000 ₽»."""
    return f"{int(abs(x) + Fraction(1, 2)) * (1 if x >= 0 else -1):,}".replace(",", " ") + " ₽"


def _months(months: int) -> str:
    years, rest = divmod(months, 12)
    if years == 0:
        return f"{rest} мес."
    return f"{years} г." if rest == 0 else f"{years} г. {rest} мес."


def grade_for(score: int) -> str:
    for lower, grade in GRADES:
        if score >= lower:
            return grade
    return "E"


def probability_of_default(score: int) -> float:
    
    return float(Fraction(2, 100) + Fraction(100 - score, 100) * Fraction(30, 100))


def _f(code: str, title: str, impact: int, value: str, text: str) -> Factor:
    return Factor(code=code, title=title, impact=impact, value=value, client_explanation=text)


def _business_age(months: int) -> Factor:
    impact = -20 if months < 6 else -10 if months < 12 else 0 if months < 36 else 10
    text = ("Компания работает больше трёх лет, это снижает риск." if impact >= 10
            else "Компания работает от года до трёх лет." if impact == 0
            else "Бизнес молодой, банк относится к таким заявкам осторожнее.")
    return _f("business_age", "Возраст бизнеса", impact, _months(months), text)


def _revenue_to_amount(revenue: Fraction, amount: Fraction) -> Factor:
    ratio = revenue / amount if amount > 0 else Fraction(0)
    impact = 15 if ratio >= 4 else 5 if ratio >= 2 else -5 if ratio >= 1 else -20
    text = ("Запрошенная сумма небольшая относительно годовой выручки." if impact > 0
            else "Запрошенная сумма велика относительно годовой выручки.")
    return _f("revenue_to_amount", "Выручка к сумме кредита", impact, f"{_fmt(ratio, 1)}x", text)


def _expense_ratio(expenses: Fraction, revenue: Fraction) -> Factor:
    ratio = expenses / revenue
    impact = EXPENSE_HIGH_POINTS if ratio > EXPENSE_RATIO_HIGH_ABOVE else 0
    text = ("Расходы забирают почти всю выручку, запас прочности небольшой." if impact < 0
            else "Расходы занимают обычную долю выручки.")
    return _f("expense_ratio", "Расходы к выручке", impact, _fmt(ratio, 2), text)


def _debt_load(annual_profit: Fraction, payments: Fraction) -> Factor:
    
    load = NO_PROFIT_LOAD if annual_profit <= 0 else payments * 12 / annual_profit
    impact = 10 if load < Fraction(3, 10) else 0 if load < Fraction(5, 10) else -10 if load < Fraction(7, 10) else -25
    text = ("Платежи по кредитам занимают небольшую часть прибыли." if impact > 0
            else "Платежи по кредитам занимают около половины месячной прибыли." if impact == 0
            else "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли.")
    value = "прибыль отсутствует" if load >= NO_PROFIT_LOAD else _fmt(load, 2)
    return _f("debt_load", "Долговая нагрузка", impact, value, text)


def _credit_history(b) -> Factor:
    title = "Кредитная история"
    overdue = b.max_overdue_days_24m
    if b.credit_history_months == 0 and b.active_loans == 0 and b.closed_loans == 0:
        return _f("credit_history", title, 0, "нет истории", "Кредитной истории пока нет, это не плюс и не минус.")
    if b.has_write_offs or overdue > 90:
        return _f("credit_history", title, -30, f"просрочка {overdue} дн.",
                  "В кредитной истории есть длительные просрочки или списания.")
    impact = 15 if overdue == 0 else -5 if overdue <= 30 else -15
    return _f("credit_history", title, impact, "без просрочек" if overdue == 0 else f"просрочка до {overdue} дн.",
              "Просрочек по кредитам за два года нет." if impact > 0 else "За последние два года были просрочки по кредитам.")


def _tax_debt(fns) -> Factor:
    debt = _frac(fns.tax_debt_rub)
    impact = 5 if debt <= 0 else -5 if debt < TAX_DEBT_SMALL_BELOW else -15
    if fns.has_blocked_accounts:
        impact -= 15
    text = ("Задолженности перед налоговой нет." if impact > 0
            else "Есть налоговая задолженность и заблокированные счета." if fns.has_blocked_accounts
            else "Есть задолженность перед налоговой.")
    return _f("tax_debt", "Налоговая задолженность", impact, "нет" if debt <= 0 else _rub(debt), text)


def _enforcement(fssp) -> Factor:
    n = fssp.open_proceedings
    return _f("enforcement", "Исполнительные производства", -10 if n > 0 else 0,
              "нет" if n == 0 else f"{n} на {_rub(_frac(fssp.open_amount_rub))}",
              "Открытых исполнительных производств нет." if n == 0 else "Есть открытые исполнительные производства.")


def _missing_data(req: ScoreRequest) -> list[str]:
    """Те же пути и в том же порядке, что ScoringRequest.MissingInputs в backend."""
    f, ch = req.company.financials, req.checks or Checks()
    out = []
    if f.expenses_rub is None:
        out.append("company.financials.expenses_rub")
    if f.net_profit_rub is None:
        out.append("company.financials.net_profit_rub")
    if ch.egrul is None:
        out.append("checks.egrul")
    if ch.fns is None:
        out.append("checks.fns")
    elif ch.fns.reported_revenue_rub is None:
        out.append("checks.fns.reported_revenue_rub")
    if ch.credit_bureau is None:
        out.append("checks.credit_bureau")
    if ch.fssp is None:
        out.append("checks.fssp")
    return out


def score(req: ScoreRequest) -> ScoreResponse:
    app, fin, checks = req.application, req.company.financials, req.checks or Checks()
    revenue, amount = _frac(fin.revenue_rub), _frac(app.amount_rub)
    expenses = None if fin.expenses_rub is None else _frac(fin.expenses_rub)
    factors, warnings = [_business_age(req.company.business_age_months), _revenue_to_amount(revenue, amount)], []

    
    if expenses is None:
        warnings.append("expenses_missing")
    elif revenue > 0:
        factors.append(_expense_ratio(expenses, revenue))

    
    if fin.net_profit_rub is not None:
        annual_profit = _frac(fin.net_profit_rub)
    else:
        annual_profit = revenue - expenses if expenses is not None else revenue * PROFIT_SHARE_IF_UNKNOWN
        warnings.append("net_profit_missing")
    payments = _frac(fin.monthly_debt_payments_rub) + _frac(app.estimated_monthly_payment_rub)
    factors.append(_debt_load(annual_profit, payments))

    for name, build in (("credit_bureau", _credit_history), ("fns", _tax_debt), ("fssp", _enforcement)):
        block = getattr(checks, name)
        if block is None:
            warnings.append(f"{name}_missing")
        else:
            factors.append(build(block))

    if app.collateral_offered:
        factors.append(_f("collateral", "Обеспечение", 10, "предложено", "Предложенный залог снижает риск банка."))

    total = max(0, min(100, BASE_SCORE + sum(f.impact for f in factors)))
    
    factors.sort(key=lambda f: (-abs(f.impact), f.code))
    return ScoreResponse(
        request_id=req.request_id,
        model_version=MODEL_VERSION,
        score=total,
        risk_grade=grade_for(total),
        probability_of_default=probability_of_default(total),
        factors=factors,
        warnings=warnings,
        missing_data=_missing_data(req),
    )
