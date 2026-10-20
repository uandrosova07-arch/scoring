
import os

from app.schemas import Reason, RiskLevel, ScoreRequest, ScoreResponse

MODEL_VERSION = os.getenv("MODEL_VERSION", "rules-0.1")


REQUIRED_FIELDS = (
    "revenue_rub",
    "expenses_rub",
    "monthly_debt_payments_rub",
    "requested_amount_rub",
    "term_months",
)


EXPENSE_RATIO_OK_BELOW = 0.70
EXPENSE_RATIO_MODERATE_UP_TO = 0.90

DEBT_LOAD_LOW_BELOW = 0.30
DEBT_LOAD_MEDIUM_UP_TO = 0.70

_SEVERITY = {"low": 0, "medium": 1, "high": 2}
_LEVELS = ("low", "medium", "high")


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def score(req: ScoreRequest) -> ScoreResponse:
    missing = [f for f in REQUIRED_FIELDS if getattr(req, f) is None]
    if missing:
        return ScoreResponse(
            risk_level="undetermined",
            reasons=[
                Reason(
                    code="INSUFFICIENT_DATA",
                    text="Для оценки не хватает данных: " + ", ".join(missing),
                )
            ],
            missing_data=missing,
            model_version=MODEL_VERSION,
        )

    reasons: list[Reason] = []
    severities: list[int] = []


    ratio = req.expenses_rub / req.revenue_rub
    if ratio < EXPENSE_RATIO_OK_BELOW:
        code, sev = "EXPENSE_RATIO_OK", 0
    elif ratio <= EXPENSE_RATIO_MODERATE_UP_TO:
        code, sev = "EXPENSE_RATIO_MODERATE", 1
    else:
        code, sev = "EXPENSE_RATIO_HIGH", 2
    reasons.append(
        Reason(code=code, text=f"Расходы составляют {_pct(ratio)} выручки", value=round(ratio, 4))
    )
    severities.append(sev)

    
    profit = req.net_profit_rub if req.net_profit_rub is not None else req.revenue_rub - req.expenses_rub
    if profit <= 0:
        reasons.append(
            Reason(code="NO_PROFIT", text="За последние 12 месяцев нет чистой прибыли", value=round(profit, 2))
        )
        severities.append(2)
    else:
        new_payment = req.requested_amount_rub / req.term_months  
        load = (req.monthly_debt_payments_rub + new_payment) / (profit / 12)
        if load < DEBT_LOAD_LOW_BELOW:
            code, sev = "DEBT_LOAD_LOW", 0
        elif load <= DEBT_LOAD_MEDIUM_UP_TO:
            code, sev = "DEBT_LOAD_MEDIUM", 1
        else:
            code, sev = "DEBT_LOAD_HIGH", 2
        reasons.append(
            Reason(
                code=code,
                text=f"Платежи по кредитам с учётом нового составят {_pct(load)} месячной прибыли",
                value=round(load, 4),
            )
        )
        severities.append(sev)

    level: RiskLevel = _LEVELS[max(severities)]
    return ScoreResponse(risk_level=level, reasons=reasons, missing_data=[], model_version=MODEL_VERSION)
