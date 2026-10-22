"""Контракт сервиса скоринга. Источник правды: docs/scoring-contract.md в репозитории backend
(GatheredSatyr53/probiinik): snake_case, рубли целыми числами, пропуск = null,
NaN и Infinity недопустимы.

Описаны только поля, которые сервис использует. Остальное (регион, ОКВЭД, история заявок и т.д.)
игнорируется, поэтому backend может расширять запрос без поломки сервиса.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Grade = Literal["A", "B", "C", "D", "E"]


class _Input(BaseModel):
   
    model_config = ConfigDict(extra="ignore", strict=True)


class Application(_Input):
    amount_rub: float = Field(gt=0, allow_inf_nan=False)
    term_months: int = Field(ge=1, le=600)
    
    estimated_monthly_payment_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Financials(_Input):
    revenue_rub: float | None = Field(default=None, gt=0, allow_inf_nan=False)  
    expenses_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False) 
    net_profit_rub: float | None = Field(default=None, allow_inf_nan=False)  
    monthly_debt_payments_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)  


class Company(_Input):
    business_age_months: int | None = Field(default=None, ge=0)
    financials: Financials | None = None


class Checks(_Input):

    egrul: dict | None = None
    fns: dict | None = None
    credit_bureau: dict | None = None
    fssp: dict | None = None


class ScoreRequest(_Input):
    request_id: str = Field(min_length=1)
    application: Application
    company: Company = Field(default_factory=Company)
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
    probability_of_default: float | None = None  
    factors: list[Factor]  
    warnings: list[str]
    missing_data: list[str]
