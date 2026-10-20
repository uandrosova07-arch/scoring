"""Контракт /score. Названия полей и единицы согласованы с бэкендером:
snake_case, рубли целыми числами, период в названии поля, пропуск = null.
"""
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskLevel = Literal["low", "medium", "high", "undetermined"]


class ScoreRequest(BaseModel):
    
    model_config = ConfigDict(extra="ignore", strict=True)

    
    business_age_months: int | None = Field(default=None, ge=0)
    requested_amount_rub: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    term_months: int | None = Field(default=None, ge=1, le=600)


    revenue_rub: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    expenses_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    net_profit_rub: float | None = Field(default=None, allow_inf_nan=False) 
    monthly_debt_payments_rub: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def nan_is_missing(cls, value):
        if isinstance(value, float) and math.isnan(value):
            return None
        return value


class Reason(BaseModel):
    code: str
    text: str
    value: float | None = None


class ScoreResponse(BaseModel):
    risk_level: RiskLevel
    reasons: list[Reason]
    missing_data: list[str]
    model_version: str
