"""Предварительная подсказка для формы заявки (POST /api/v1/preview). Не кредитное решение.

Что отличает preview от /api/v1/score:
- считаются только финансовые факторы rules-0.3 (возраст, выручка к сумме, расходы, нагрузка) теми же функциями,
  что и в оценке, поэтому пороги и тексты совпадают;
- БКИ, ФНС и ФССП до подачи заявки неизвестны, поэтому балла, грейда и PD нет: частичный балл выглядел бы как решение;
- пустые финансовые поля не ошибка, а статус incomplete с перечнем недостающего (клиент ещё заполняет форму);
- нагрузка показывается уровнем (Comfortable / Moderate / Elevated / High) по полосам rules-0.3; если прибыль неизвестна
  (нет ни чистой прибыли, ни расходов), уровень InsufficientData: в отличие от оценки, 10 % выручки preview не подставляет;
- подсказка о меньшей сумме считается по формуле DecisionPolicy для одобрения с изменёнными условиями.
Спецификация: docs/preview.md.
"""
import math
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal
from fractions import Fraction

from app.rules import (
    MODEL_VERSION,
    _business_age,
    _debt_load,
    _expense_ratio,
    _frac,
    _revenue_to_amount,
    debt_load_band,
    debt_load_ratio,
)
from app.schemas import Affordability, AmountHint, PreviewRequest, PreviewResponse

# DecisionPolicyOptions.MaxAmountShareOfRevenue в backend: при грейде C сумма не больше 25 % годовой выручки.
REDUCED_AMOUNT_SHARE = Fraction(1, 4)
NOT_EVALUATED = ["credit_history", "tax_debt", "enforcement"]
DISCLAIMER = (
    "Предварительная подсказка по введённым данным, а не решение банка. Кредитная история, налоговая задолженность "
    "и исполнительные производства проверяются после подачи заявки, поэтому итог может отличаться."
)


LEVEL_TEXT = {
    "Comfortable": "Платежи по кредитам с учётом нового займа занимают небольшую часть прибыли.",
    "Moderate": "Платежи по кредитам с учётом нового займа занимают заметную часть прибыли.",
    "Elevated": "Платежи по кредитам с учётом нового займа занимают больше половины прибыли.",
    "High": "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли или прибыли нет.",
}
INSUFFICIENT_TEXT = (
    "Чтобы оценить нагрузку, укажите чистую прибыль или расходы за 12 месяцев, а также текущие платежи по кредитам. "
    "Если прибыли и расходов не будет и в заявке, банк примет прибыль равной 10 % выручки."
)


def _affordability(annual_profit: Fraction, payments: Fraction, basis: str | None) -> Affordability:
    _, level = debt_load_band(debt_load_ratio(annual_profit, payments))
    load = None if annual_profit <= 0 else float(round(payments * 12 / annual_profit, 4))
    return Affordability(level=level, debt_load=load, monthly_profit_rub=float(round(annual_profit / 12, 2)),
                         monthly_payments_rub=float(payments), profit_basis=basis, explanation=LEVEL_TEXT[level])


def _rub(x: Fraction) -> str:
    return f"{int(x):,}".replace(",", " ") + " ₽"


def annuity_payment(amount: Fraction, rate_percent: Fraction, term: int) -> Fraction:
    """LoanMath.MonthlyPayment + Money.WholeRubles в backend: аннуитет, копейки банковским округлением, затем до рубля."""
    if rate_percent <= 0:
        kopecks = Decimal(amount.numerator) / Decimal(amount.denominator) / term
    else:
        r = float(rate_percent) / 100.0 / 12.0
        factor = Context(prec=15, rounding=ROUND_HALF_EVEN).create_decimal(repr(r / (1 - math.pow(1 + r, -term))))
        kopecks = Decimal(amount.numerator) / Decimal(amount.denominator) * factor
    kopecks = kopecks.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return Fraction(int(kopecks.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def reduced_amount(requested: Fraction, revenue: Fraction, min_amount: Fraction) -> Fraction:
    """DecisionPolicy, грейд C: Math.Max(Math.Min(сумма, Math.Round(выручка * 0.25, 0)), минимум продукта)."""
    cap = Decimal(revenue.numerator * REDUCED_AMOUNT_SHARE.numerator) / Decimal(revenue.denominator * REDUCED_AMOUNT_SHARE.denominator)
    cap = Fraction(int(cap.quantize(Decimal(1), rounding=ROUND_HALF_EVEN)))
    return max(min(requested, cap), min_amount)


def preview(req: PreviewRequest) -> PreviewResponse:
    fin = req.company.financials
    revenue = None if fin is None or fin.revenue_rub is None else _frac(fin.revenue_rub)
    expenses = None if fin is None or fin.expenses_rub is None else _frac(fin.expenses_rub)
    net = None if fin is None or fin.net_profit_rub is None else _frac(fin.net_profit_rub)
    debt = None if fin is None or fin.monthly_debt_payments_rub is None else _frac(fin.monthly_debt_payments_rub)
    amount = _frac(req.amount_rub)
    missing, warnings, hints = [], [], []

    if req.company.business_age_months is None:
        missing.append("company.business_age_months")
    else:
        hints.append(_business_age(req.company.business_age_months))

    if revenue is None:
        missing.append("company.financials.revenue_rub")
    else:
        hints.append(_revenue_to_amount(revenue, amount))

    if expenses is None:
        missing.append("company.financials.expenses_rub")
        warnings.append("expenses_missing")
    elif revenue is not None and revenue > 0:
        hints.append(_expense_ratio(expenses, revenue))

    # Прибыль: из формы; иначе выручка − расходы. Третьего шага оценки (10 % выручки) в preview нет: показывать клиенту
    # уровень нагрузки по допущению нельзя, вместо него InsufficientData и подсказка заполнить прибыль или расходы.
    if net is not None:
        annual_profit, basis = net, "net_profit"
    else:
        missing.append("company.financials.net_profit_rub")
        if revenue is not None and expenses is not None:
            annual_profit, basis = revenue - expenses, "revenue_minus_expenses"
            warnings.append("net_profit_missing")
        else:
            annual_profit, basis = None, None

    if req.estimated_monthly_payment_rub is not None:
        payment = _frac(req.estimated_monthly_payment_rub)
    elif req.product.base_rate_percent is not None:
        payment = annuity_payment(amount, _frac(req.product.base_rate_percent), req.term_months)
    else:
        payment = None
        missing.append("estimated_monthly_payment_rub")
    if debt is None:
        missing.append("company.financials.monthly_debt_payments_rub")
    if annual_profit is not None and payment is not None and debt is not None:
        hints.append(_debt_load(annual_profit, debt + payment))
        affordability = _affordability(annual_profit, debt + payment, basis)
    else:
        affordability = Affordability(
            level="InsufficientData", debt_load=None, monthly_profit_rub=None, monthly_payments_rub=None, profit_basis=None,
            explanation=INSUFFICIENT_TEXT)

    amount_hint = None
    if revenue is not None:
        min_amount = _frac(req.product.min_amount_rub) if req.product.min_amount_rub is not None else Fraction(0)
        reduced = reduced_amount(amount, revenue, min_amount)
        would = reduced < amount
        text = (f"Если банк одобрит заявку с изменёнными условиями, сумму могут снизить до {_rub(reduced)}: "
                "это 25 % годовой выручки (не ниже минимума продукта)." if would else
                "Если банк одобрит заявку с изменёнными условиями, сумма не изменится: она не больше 25 % годовой выручки.")
        amount_hint = AmountHint(requested_amount_rub=int(amount), reduced_amount_rub=int(reduced), would_be_reduced=would,
                                 share_of_revenue=float(REDUCED_AMOUNT_SHARE), explanation=text)

    # «Готово», когда посчитаны возраст, выручка к сумме и нагрузка; без расходов фактора расходов нет и в оценке, это не помеха.
    ready = {"business_age", "revenue_to_amount", "debt_load"} <= {h.code for h in hints}
    hints.sort(key=lambda f: (-abs(f.impact), f.code))
    return PreviewResponse(
        model_version=MODEL_VERSION,
        status="ready" if ready else "incomplete",
        hints=hints,
        affordability=affordability,
        not_evaluated=NOT_EVALUATED,
        amount_hint=amount_hint,
        missing_data=missing,
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )
