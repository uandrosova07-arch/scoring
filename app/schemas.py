"""Контракт сервиса скоринга. Источник правды: docs/scoring-contract.md в репозитории backend
(GatheredSatyr53/probiinik): snake_case, рубли целыми числами, пропуск = null, NaN и Infinity недопустимы.

Описаны только поля, которые сервис использует. Остальное (регион, ОКВЭД, история заявок и т.д.) игнорируется,
поэтому backend может расширять запрос без поломки сервиса.

Обязательность полей повторяет типы запроса в backend (ScoringRequest.cs): что там не nullable, здесь обязательно.
null допускается только там, где backend сам может его прислать: расходы, чистая прибыль, выручка по отчётности ФНС
и блоки внешних проверок целиком.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Grade = Literal["A", "B", "C", "D", "E"]


class _Input(BaseModel):
    # strict: строка вместо числа и true/false вместо числа дают ошибку, а не молчаливое приведение.
    model_config = ConfigDict(extra="ignore", strict=True)


class Application(_Input):
    amount_rub: float = Field(gt=0, allow_inf_nan=False)
    term_months: int = Field(ge=1, le=600)
    # Аннуитет по базовой ставке продукта, считает backend (LoanMath.MonthlyPayment).
    estimated_monthly_payment_rub: float = Field(ge=0, allow_inf_nan=False)
    collateral_offered: bool = False


class Financials(_Input):
    revenue_rub: float = Field(ge=0, allow_inf_nan=False)  # за 12 мес.
    expenses_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)  # за 12 мес.
    net_profit_rub: float | None = Field(default=None, allow_inf_nan=False)  # за 12 мес., может быть < 0
    monthly_debt_payments_rub: float = Field(ge=0, allow_inf_nan=False)  # текущие платежи за месяц


class Company(_Input):
    business_age_months: int = Field(ge=0)
    financials: Financials


class Fns(_Input):
    tax_debt_rub: float = Field(allow_inf_nan=False)
    has_blocked_accounts: bool
    reported_revenue_rub: float | None = Field(default=None, allow_inf_nan=False)


class CreditBureau(_Input):
    credit_history_months: int = Field(ge=0)
    active_loans: int = Field(ge=0)
    closed_loans: int = Field(ge=0)
    max_overdue_days_24m: int = Field(ge=0)
    has_write_offs: bool


class Fssp(_Input):
    open_proceedings: int = Field(ge=0)
    open_amount_rub: float = Field(ge=0, allow_inf_nan=False)


class Checks(_Input):
    # Блок, по которому источник не ответил, приходит как null. ЕГРЮЛ в баллах не участвует (стоп-факторы у backend),
    # но его отсутствие попадает в missing_data, как у backend.
    egrul: dict | None = None
    fns: Fns | None = None
    credit_bureau: CreditBureau | None = None
    fssp: Fssp | None = None


class ScoreRequest(_Input):
    request_id: str = Field(min_length=1)
    application: Application
    company: Company
    # backend всегда присылает объект; null принимается и означает, что не ответил ни один источник.
    checks: Checks | None = None


class Factor(BaseModel):
    code: str  # без префикса: business_age, revenue_to_amount, expense_ratio, debt_load, credit_history, tax_debt, enforcement, collateral
    title: str
    impact: int  # баллы со знаком
    value: str
    client_explanation: str


class ScoreResponse(BaseModel):
    request_id: str
    model_version: str
    score: int  # 0..100, выше = ниже риск
    risk_grade: Grade
    probability_of_default: float = Field(description=(
        "Зависит от model_version. logreg-synthetic-0.1: исходная PD модели (сигмоида логита, без округления), "
        "вероятность default_12m, откалиброванная только на синтетических данных; балл выводится из неё. "
        "rules-0.3: эвристика backend 0.02 + (100 − score) / 100 × 0.30, не измеренная величина. "
        "В обоих случаях это не вероятность одобрения заявки."))
    factors: list[Factor]  # по убыванию модуля impact, при равенстве по коду
    warnings: list[str]
    # Пути полей, пришедших как null; тот же список, что backend строит сам (ScoringRequest.MissingInputs).
    missing_data: list[str]


# ---------------------------------------------------------------- предварительная подсказка (preview)
# Не кредитное решение: нет балла, грейда и PD. Пока клиент заполняет форму, любые финансовые поля могут быть пустыми.

class PreviewProduct(_Input):
    code: str | None = None
    min_amount_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # Нужна, только если не передан estimated_monthly_payment_rub: тогда платёж считается аннуитетом, как LoanMath в backend.
    base_rate_percent: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class PreviewFinancials(_Input):
    revenue_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    expenses_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    net_profit_rub: float | None = Field(default=None, allow_inf_nan=False)
    monthly_debt_payments_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class PreviewCompany(_Input):
    business_age_months: int | None = Field(default=None, ge=0)
    financials: PreviewFinancials | None = None


class PreviewRequest(_Input):
    amount_rub: float = Field(gt=0, allow_inf_nan=False)
    term_months: int = Field(ge=1, le=600)
    estimated_monthly_payment_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    product: PreviewProduct = Field(default_factory=PreviewProduct)
    company: PreviewCompany = Field(default_factory=PreviewCompany)


class AmountHint(BaseModel):
    requested_amount_rub: int
    # Сумма, до которой backend снизит заявку при одобрении с изменёнными условиями (грейд C):
    # min(запрошено, 25 % годовой выручки с банковским округлением), но не ниже минимума продукта. Как DecisionPolicy.
    reduced_amount_rub: int
    would_be_reduced: bool
    share_of_revenue: float
    explanation: str


AffordabilityLevel = Literal["Comfortable", "Moderate", "Elevated", "High", "InsufficientData"]


class Affordability(BaseModel):
    # Уровень долговой нагрузки для клиента без баллов. Полосы те же, что у фактора debt_load в rules-0.3:
    # < 0,3 Comfortable; 0,3..0,5 Moderate; 0,5..0,7 Elevated; >= 0,7 или прибыль <= 0 High.
    level: AffordabilityLevel
    # InsufficientData: все числовые поля и profit_basis = null. Прибыль как 10 % выручки preview не подставляет.
    debt_load: float | None  # (текущие платежи + новый) / месячная прибыль; null и при прибыли <= 0
    monthly_profit_rub: float | None
    monthly_payments_rub: float | None
    profit_basis: Literal["net_profit", "revenue_minus_expenses"] | None
    explanation: str


class PreviewResponse(BaseModel):
    kind: Literal["preview"] = "preview"
    is_credit_decision: Literal[False] = False
    model_version: str
    status: Literal["ready", "incomplete"]  # incomplete: не хватает данных хотя бы для одного финансового фактора
    hints: list[Factor]  # только финансовые факторы rules-0.3, по убыванию модуля вклада
    affordability: Affordability
    not_evaluated: list[str]  # факторы, которые до подачи заявки не оцениваются
    amount_hint: AmountHint | None
    missing_data: list[str]
    warnings: list[str]
    disclaimer: str
