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
   
    egrul: dict | None = None
    fns: Fns | None = None
    credit_bureau: CreditBureau | None = None
    fssp: Fssp | None = None


class ScoreRequest(_Input):
    request_id: str = Field(min_length=1)
    application: Application
    company: Company
    
    checks: Checks | None = None


class Factor(BaseModel):
    code: str  
    title: str
    impact: int  
    value: str
    client_explanation: str


class ScoreResponse(BaseModel):
    request_id: str
    model_version: str
    score: int  
    risk_grade: Grade
    probability_of_default: float 
    factors: list[Factor]  
    warnings: list[str]
   
    missing_data: list[str]
