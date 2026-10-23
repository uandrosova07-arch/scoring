"""Справочная копия расчётов backend (C#) для сверки с Python-сервисом. Только стандартная библиотека.

Источник: репозиторий GatheredSatyr53/probiinik, коммит 9f18dd4 (23.10.2026). Перенесено построчно, без изменений логики:

| Здесь                     | В backend                                                              |
|---------------------------|------------------------------------------------------------------------|
| PRODUCTS, COMPANIES       | src/AlfaCredit.Infrastructure/Persistence/Seeding/SeedData.cs          |
| CHECKS                    | src/AlfaCredit.Infrastructure/Simulators/KnownProfiles.cs              |
| DEMO_SCENARIOS            | tests/AlfaCredit.IntegrationTests/Endpoints/PipelineEndpointsTests.cs  |
| monthly_payment           | src/AlfaCredit.Core/Common/LoanMath.cs (MonthlyPayment)                |
| build_request             | src/AlfaCredit.Core/Features/Processing/ApplicationPipeline.cs (BuildScoringRequest) |
| validate_application      | CreditApplication.Create + ApplicationPipeline.ValidationIssues         |
| score_rules_v1            | src/AlfaCredit.Core/Features/Scoring/RuleBasedScoringService.cs        |
| decide                    | src/AlfaCredit.Core/Features/Decisions/DecisionPolicy.cs               |
| demo_manager_approve      | src/AlfaCredit.Core/Features/Decisions/DemoManager.cs (DemoManagerPolicy.Approve) |

Это не замена C#: столбец «C#» в docs/grade-comparison.md подтверждается тестом в самом backend
(docs/backend/DemoSeedScoringTests.cs). Если backend изменился, обновите эту копию и пересоберите данные:
`python scripts/demo_comparison.py build`.

Деньги считаются в Decimal, как decimal в C#. Округления повторяют .NET: Math.Round по умолчанию банковское,
Money.WholeRubles до рубля от нуля, форматирование чисел от нуля.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, localcontext

BACKEND_COMMIT = "9f18dd4"
RULES_MODEL_VERSION = "rules-v1"
D = Decimal


# ---------------------------------------------------------------- seed: продукты и компании

@dataclass(frozen=True)
class Product:
    code: str
    name: str
    type: str
    min_amount: Decimal
    max_amount: Decimal
    min_term: int
    max_term: int
    base_rate: Decimal
    purposes: tuple[str, ...]
    min_business_age: int
    requires_collateral: bool
    auto_approval_limit: Decimal


# Decimal("23.0") сохраняет масштаб, как литерал 23.0m: в JSON backend пишет 23.0.
PRODUCTS = {p.code: p for p in (
    Product("working-capital", "Оборотный кредит", "WorkingCapital", D(300_000), D(10_000_000), 3, 24, D("19.5"),
            ("WorkingCapital", "Other"), 12, False, D(5_000_000)),
    Product("express", "Экспресс-кредит", "Express", D(100_000), D(3_000_000), 3, 18, D("23.0"),
            ("WorkingCapital", "Equipment", "Expansion", "Refinancing", "Other"), 6, False, D(3_000_000)),
    Product("overdraft", "Овердрафт", "Overdraft", D(100_000), D(5_000_000), 1, 12, D("21.0"),
            ("WorkingCapital",), 6, False, D(2_000_000)),
    Product("credit-line", "Кредитная линия", "CreditLine", D(500_000), D(20_000_000), 6, 36, D("18.5"),
            ("WorkingCapital", "Expansion", "Equipment"), 12, False, D(5_000_000)),
    Product("investment", "Инвестиционный кредит", "Investment", D(1_000_000), D(50_000_000), 12, 84, D("17.5"),
            ("Equipment", "Expansion", "Refinancing"), 24, True, D(3_000_000)),
)}


@dataclass(frozen=True)
class Company:
    login: str
    inn: str
    name: str
    legal_form: str
    okved: str
    registered_at: date
    tax_regime: str
    revenue: Decimal
    expenses: Decimal | None
    net_profit: Decimal | None
    monthly_debt_payments: Decimal
    employees: int

    @property
    def region_code(self) -> str:
        return self.inn[:2]

    def business_age_months(self, today: date) -> int:
        months = (today.year - self.registered_at.year) * 12 + today.month - self.registered_at.month
        if today.day < self.registered_at.day:
            months -= 1
        return max(0, months)


COMPANIES = {c.login: c for c in (
    Company("client_good", "7701234567", "ООО «Ромашка»", "Ooo", "47.11", date(2019, 3, 12), "Osno",
            D(48_000_000), D(40_800_000), D(7_200_000), D(150_000), 24),
    Company("client_medium", "503212345678", "ИП Кузнецов Сергей Павлович", "Ip", "49.41", date(2023, 6, 1), "UsnIncome",
            D(9_600_000), D(4_200_000), D(4_800_000), D(40_000), 5),
    Company("client_manual", "7812345678", "ООО «Вектор»", "Ooo", "25.11", date(2016, 9, 20), "Osno",
            D(180_000_000), D(160_000_000), D(16_000_000), D(1_200_000), 85),
    Company("client_bad", "5401234567", "ООО «Закат»", "Ooo", "56.10", date(2020, 2, 14), "UsnIncomeExpenses",
            D(14_000_000), D(13_900_000), D(100_000), D(600_000), 12),
    Company("client_new", "772812345678", "ИП Петрова Елена Викторовна", "Ip", "96.02", date(2026, 6, 15), "Patent",
            D(1_200_000), None, None, D(0), 2),
)}

# KnownProfiles.cs: ответы симуляторов ЕГРЮЛ, ФНС, БКИ, ФССП по ИНН. Поля в порядке записей C#.
CHECKS = {
    "7701234567": {
        "egrul": {"status": "Active", "head_disqualified": False, "mass_address": False},
        "fns": {"tax_debt_rub": D(0), "has_blocked_accounts": False, "reported_revenue_rub": D(47_300_000)},
        "credit_bureau": (60, 1, 3, D(1_200_000), D(150_000), 0, 0, False),
        "fssp": {"open_proceedings": 0, "open_amount_rub": D(0)},
    },
    "503212345678": {
        "egrul": {"status": "Active", "head_disqualified": False, "mass_address": False},
        "fns": {"tax_debt_rub": D(12_000), "has_blocked_accounts": False, "reported_revenue_rub": D(9_100_000)},
        "credit_bureau": (30, 1, 1, D(900_000), D(40_000), 25, 1, False),
        "fssp": {"open_proceedings": 0, "open_amount_rub": D(0)},
    },
    "7812345678": {
        "egrul": {"status": "Active", "head_disqualified": False, "mass_address": False},
        "fns": {"tax_debt_rub": D(0), "has_blocked_accounts": False, "reported_revenue_rub": D(176_000_000)},
        "credit_bureau": (96, 3, 5, D(40_000_000), D(1_200_000), 0, 0, False),
        "fssp": {"open_proceedings": 0, "open_amount_rub": D(0)},
    },
    "5401234567": {
        "egrul": {"status": "Active", "head_disqualified": False, "mass_address": True},
        "fns": {"tax_debt_rub": D(700_000), "has_blocked_accounts": True, "reported_revenue_rub": D(13_200_000)},
        "credit_bureau": (48, 4, 1, D(9_000_000), D(600_000), 95, 3, False),
        "fssp": {"open_proceedings": 2, "open_amount_rub": D(1_400_000)},
    },
    "772812345678": {
        "egrul": {"status": "Active", "head_disqualified": False, "mass_address": False},
        "fns": {"tax_debt_rub": D(0), "has_blocked_accounts": False, "reported_revenue_rub": None},
        "credit_bureau": (0, 0, 0, D(0), D(0), 0, 0, False),
        "fssp": {"open_proceedings": 0, "open_amount_rub": D(0)},
    },
}
_BUREAU_FIELDS = ("credit_history_months", "active_loans", "closed_loans", "total_debt_rub", "monthly_payments_rub",
                  "max_overdue_days_24m", "overdue_count_24m", "has_write_offs")


@dataclass(frozen=True)
class Scenario:
    id: str
    login: str
    product: str
    amount: Decimal
    term: int
    purpose: str
    # Чем это заявка должна закончиться в общем демо (SeedData.Accounts, docs/decision-logic.md, интеграционные тесты).
    agreed_outcome: str
    agreed_note: str


# Продукт, сумма и срок как в общем демо: интеграционные тесты backend и таблица «Демо-профили» в decision-logic.md.
# У client_new в демо заявка не создаётся (400: возраст бизнеса меньше минимума любого продукта). Для проверки
# нехватки данных берём самый мягкий по возрасту продукт «Экспресс» и минимальную разумную сумму: такой запрос
# backend сейчас не отправит, это проверка контракта на реальных цифрах seed.
DEMO_SCENARIOS = (
    Scenario("demo-good", "client_good", "working-capital", D(1_500_000), 12, "WorkingCapital",
             "Approved", "автоодобрение на запрошенных условиях, 19,5 %"),
    Scenario("demo-medium", "client_medium", "working-capital", D(3_000_000), 24, "WorkingCapital",
             "ApprovedWithConditions", "одобрение с изменёнными условиями: 2,4 млн под 22,5 %"),
    Scenario("demo-manual", "client_manual", "working-capital", D(8_000_000), 24, "WorkingCapital",
             "ManualReview", "ручное рассмотрение из-за суммы выше лимита 5 млн, рекомендация «одобрить»"),
    Scenario("demo-bad", "client_bad", "express", D(500_000), 6, "WorkingCapital",
             "Rejected", "отказ по стоп-факторам"),
    Scenario("demo-new", "client_new", "express", D(300_000), 12, "WorkingCapital",
             "SubmissionRejected", "400 при подаче: бизнесу 4 месяца, до скоринга не доходит"),
)


# ---------------------------------------------------------------- .NET: округление и форматирование

def net_round(value: Decimal, digits: int = 0) -> Decimal:
    """Math.Round(decimal, digits): банковское округление."""
    return value.quantize(D(1).scaleb(-digits), rounding=ROUND_HALF_EVEN)


def whole_rubles(value: Decimal | None) -> Decimal | None:
    """Money.WholeRubles: до рубля, половина от нуля."""
    return None if value is None else value.quantize(D(1), rounding=ROUND_HALF_UP)


def _fmt(value: Decimal, digits: int) -> str:
    # Пользовательский формат «0.0» / «0.00» в .NET округляет половину от нуля. Разделитель как в инвариантной культуре.
    return str(value.quantize(D(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def format_rub(value: Decimal) -> str:
    """Money.FormatRub: «1 500 000 ₽»."""
    return f"{int(value.quantize(D(1), rounding=ROUND_HALF_UP)):,}".replace(",", " ") + " ₽"


def _double_to_decimal(x: float) -> Decimal:
    # Явное приведение (decimal)double в .NET оставляет не больше 15 значащих цифр.
    return Context(prec=15, rounding=ROUND_HALF_EVEN).create_decimal(repr(x))


def monthly_payment(amount: Decimal, annual_rate_percent: Decimal, term: int) -> Decimal:
    """LoanMath.MonthlyPayment: аннуитет, копейки банковским округлением."""
    if term <= 0:
        raise ValueError("term")
    if amount <= 0:
        return D(0)
    if annual_rate_percent <= 0:
        return net_round(amount / term, 2)
    monthly_rate = float(annual_rate_percent) / 100.0 / 12.0
    factor = monthly_rate / (1 - math.pow(1 + monthly_rate, -term))
    return net_round(amount * _double_to_decimal(factor), 2)


# ---------------------------------------------------------------- заявка и запрос к скорингу

def validate_application(s: Scenario, today: date) -> dict[str, str]:
    """Ошибки при подаче (CreditApplication.Create) и на этапе Validate. Пусто: заявка дойдёт до скоринга."""
    c, p = COMPANIES[s.login], PRODUCTS[s.product]
    errors = {}
    if not p.min_amount <= s.amount <= p.max_amount:
        errors["amount"] = f"сумма вне {p.min_amount}..{p.max_amount}"
    if not p.min_term <= s.term <= p.max_term:
        errors["termMonths"] = f"срок вне {p.min_term}..{p.max_term}"
    if s.purpose not in p.purposes:
        errors["purpose"] = "цель недоступна для продукта"
    age = c.business_age_months(today)
    if age < p.min_business_age:
        errors["companyId"] = f"возраст бизнеса {age} мес., для продукта нужно не менее {p.min_business_age}"
    if errors:
        return errors  # 400 при подаче
    if c.revenue <= 0:
        errors["validate"] = "нет выручки"
    needs_statements = s.amount > 3_000_000 or p.type in ("Investment", "CreditLine")
    if needs_statements and (c.expenses is None or c.net_profit is None):
        errors["validate"] = "нужны расходы и чистая прибыль (статус «Нужны уточнения»)"
    return errors


def build_request(s: Scenario, request_id: str, application_id: str, number: str, created_at: str, today: date) -> dict:
    """ApplicationPipeline.BuildScoringRequest + сериализация HttpScoringService: snake_case, порядок полей записей C#."""
    c, p = COMPANIES[s.login], PRODUCTS[s.product]
    ch = CHECKS[c.inn]
    return {
        "request_id": request_id,
        "application": {
            "id": application_id,
            "number": number,
            "created_at": created_at,
            "amount_rub": whole_rubles(s.amount),
            "term_months": s.term,
            "purpose": s.purpose,
            "estimated_monthly_payment_rub": whole_rubles(monthly_payment(s.amount, p.base_rate, s.term)),
            "product": {
                "code": p.code,
                "type": p.type,
                "base_rate_percent": p.base_rate,
                "requires_collateral": p.requires_collateral,
                "auto_approval_limit_rub": whole_rubles(p.auto_approval_limit),
            },
            "collateral_offered": False,
        },
        "company": {
            "inn": c.inn,
            "legal_form": c.legal_form,
            "okved": c.okved,
            "region_code": c.region_code,
            "registered_at": c.registered_at.isoformat(),
            "business_age_months": c.business_age_months(today),
            "tax_regime": c.tax_regime,
            "employees_count": c.employees,
            "financials": {
                "period_months": 12,
                "revenue_rub": whole_rubles(c.revenue),
                "expenses_rub": whole_rubles(c.expenses),
                "net_profit_rub": whole_rubles(c.net_profit),
                "monthly_debt_payments_rub": whole_rubles(c.monthly_debt_payments),
            },
            "history": {"previous_applications": 0, "previous_rejections": 0, "previous_loans_disbursed": 0},
        },
        "checks": {
            "egrul": {**ch["egrul"], "registered_at": c.registered_at.isoformat(), "okved": c.okved},
            "fns": dict(ch["fns"]),
            "credit_bureau": dict(zip(_BUREAU_FIELDS, ch["credit_bureau"])),
            "fssp": dict(ch["fssp"]),
        },
    }


def to_json_value(obj):
    """Decimal в JSON как в System.Text.Json: число с тем же масштабом (23.0 остаётся 23.0, 1500000 без точки)."""
    if isinstance(obj, dict):
        return {k: to_json_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_value(v) for v in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() and obj.as_tuple().exponent >= 0 else float(obj)
    return obj


def missing_inputs(req: dict) -> list[str]:
    """ScoringRequest.MissingInputs: что backend отправил как null."""
    f, ch = req["company"]["financials"], req["checks"]
    out = []
    if f["expenses_rub"] is None:
        out.append("company.financials.expenses_rub")
    if f["net_profit_rub"] is None:
        out.append("company.financials.net_profit_rub")
    if ch.get("egrul") is None:
        out.append("checks.egrul")
    if ch.get("fns") is None:
        out.append("checks.fns")
    elif ch["fns"].get("reported_revenue_rub") is None:
        out.append("checks.fns.reported_revenue_rub")
    if ch.get("credit_bureau") is None:
        out.append("checks.credit_bureau")
    if ch.get("fssp") is None:
        out.append("checks.fssp")
    return out


# ---------------------------------------------------------------- RuleBasedScoringService (rules-v1)

def grade_for(score: int) -> str:
    return "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D" if score >= 35 else "E"


RATE_PREMIUM = {"A": D(0), "B": D("1.5"), "C": D(3), "D": D(5), "E": D(8)}


def _format_months(months: int) -> str:
    years, rest = divmod(months, 12)
    if years == 0:
        return f"{rest} мес."
    return f"{years} г." if rest == 0 else f"{years} г. {rest} мес."


def _dec(x) -> Decimal:
    return x if isinstance(x, Decimal) else D(str(x))


def score_rules_v1(req: dict) -> dict:
    """Встроенная балльная модель backend. Вход: запрос в формате контракта (как его собирает backend)."""
    with localcontext() as ctx:
        ctx.prec = 28  # точность decimal в C#
        factors, warnings = [], []
        app, company, checks = req["application"], req["company"], req["checks"] or {}
        fin = company["financials"]

        age = company["business_age_months"]
        age_impact = -20 if age < 6 else -10 if age < 12 else 0 if age < 36 else 10
        factors.append(("business_age", "Возраст бизнеса", age_impact, _format_months(age),
                        "Компания работает больше трёх лет, это снижает риск." if age_impact >= 10
                        else "Компания работает от года до трёх лет." if age_impact == 0
                        else "Бизнес молодой, банк относится к таким заявкам осторожнее."))

        amount, revenue = _dec(app["amount_rub"]), _dec(fin["revenue_rub"])
        ratio = D(0) if amount <= 0 else revenue / amount
        ratio_impact = 15 if ratio >= 4 else 5 if ratio >= 2 else -5 if ratio >= 1 else -20
        factors.append(("revenue_to_amount", "Выручка к сумме кредита", ratio_impact, f"{_fmt(ratio, 1)}x",
                        "Запрошенная сумма небольшая относительно годовой выручки." if ratio_impact > 0
                        else "Запрошенная сумма велика относительно годовой выручки."))

        if fin["net_profit_rub"] is not None:
            monthly_profit = _dec(fin["net_profit_rub"]) / 12
        else:
            monthly_profit = revenue * D("0.10") / 12
            warnings.append("net_profit_missing")
        total_payments = _dec(fin["monthly_debt_payments_rub"]) + _dec(app["estimated_monthly_payment_rub"])
        load = D("9.99") if monthly_profit <= 0 else total_payments / monthly_profit
        load_impact = 10 if load < D("0.3") else 0 if load < D("0.5") else -10 if load < D("0.7") else -25
        factors.append(("debt_load", "Долговая нагрузка", load_impact,
                        "прибыль отсутствует" if load >= D("9.99") else _fmt(load, 2),
                        "Платежи по кредитам занимают небольшую часть прибыли." if load_impact > 0
                        else "Платежи по кредитам занимают около половины месячной прибыли." if load_impact == 0
                        else "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли."))

        bureau = checks.get("credit_bureau")
        if bureau is not None:
            overdue = bureau["max_overdue_days_24m"]
            if bureau["credit_history_months"] == 0 and bureau["active_loans"] == 0 and bureau["closed_loans"] == 0:
                h = (0, "нет истории", "Кредитной истории пока нет, это не плюс и не минус.")
            elif bureau["has_write_offs"] or overdue > 90:
                h = (-30, f"просрочка {overdue} дн.", "В кредитной истории есть длительные просрочки или списания.")
            else:
                imp = 15 if overdue == 0 else -5 if overdue <= 30 else -15
                h = (imp, "без просрочек" if overdue == 0 else f"просрочка до {overdue} дн.",
                     "Просрочек по кредитам за два года нет." if imp > 0
                     else "За последние два года были просрочки по кредитам.")
            factors.append(("credit_history", "Кредитная история", *h))
        else:
            warnings.append("credit_bureau_missing")

        fns = checks.get("fns")
        if fns is not None:
            debt = _dec(fns["tax_debt_rub"])
            tax_impact = 5 if debt <= 0 else -5 if debt < 50_000 else -15
            if fns["has_blocked_accounts"]:
                tax_impact -= 15
            factors.append(("tax_debt", "Налоговая задолженность", tax_impact,
                            "нет" if debt <= 0 else format_rub(debt),
                            "Задолженности перед налоговой нет." if tax_impact > 0
                            else "Есть налоговая задолженность и заблокированные счета." if fns["has_blocked_accounts"]
                            else "Есть задолженность перед налоговой."))
        else:
            warnings.append("fns_missing")

        fssp = checks.get("fssp")
        if fssp is not None:
            n = fssp["open_proceedings"]
            factors.append(("enforcement", "Исполнительные производства", -10 if n > 0 else 0,
                            "нет" if n == 0 else f"{n} на {format_rub(_dec(fssp['open_amount_rub']))}",
                            "Открытых исполнительных производств нет." if n == 0
                            else "Есть открытые исполнительные производства."))
        else:
            warnings.append("fssp_missing")

        if app.get("collateral_offered"):
            factors.append(("collateral", "Обеспечение", 10, "предложено", "Предложенный залог снижает риск банка."))

        score = max(0, min(100, 50 + sum(f[2] for f in factors)))
        pd = net_round(D("0.02") + D(100 - score) / 100 * D("0.30"), 3)
        ordered = sorted(factors, key=lambda f: (-abs(f[2]), f[0]))
        return {
            "score": score,
            "risk_grade": grade_for(score),
            "probability_of_default": pd,
            "factors": [dict(zip(("code", "title", "impact", "value", "client_explanation"), f)) for f in ordered],
            "warnings": warnings,
            "missing_data": missing_inputs(req),
            "model_version": RULES_MODEL_VERSION,
        }


# ---------------------------------------------------------------- DecisionPolicy и демо-менеджер

POLICY = {"max_share_of_revenue": D("0.25"), "tax_debt_stop": D(500_000), "fssp_stop": D(1_000_000), "overdue_stop": 90}


def _hard_stops(checks: dict) -> list[str]:
    out = []
    if (e := checks.get("egrul")) is not None:
        if e["status"] != "Active":
            out.append("company_inactive")
        if e["head_disqualified"]:
            out.append("head_disqualified")
    if (f := checks.get("fns")) is not None:
        if _dec(f["tax_debt_rub"]) >= POLICY["tax_debt_stop"]:
            out.append("tax_debt")
        if f["has_blocked_accounts"]:
            out.append("accounts_blocked")
    if (b := checks.get("credit_bureau")) is not None and (b["max_overdue_days_24m"] > POLICY["overdue_stop"] or b["has_write_offs"]):
        out.append("overdue_90")
    if (s := checks.get("fssp")) is not None and _dec(s["open_amount_rub"]) > POLICY["fssp_stop"]:
        out.append("enforcement_debt")
    return out


def _recommended(grade: str) -> str:
    return "Approved" if grade in "AB" else "ApprovedWithConditions" if grade in "CD" else "Rejected"


def _outcome(type_, recommended=None, amount=None, term=None, rate=None, payment=None, reasons=()):
    return {"type": type_, "recommended_type": recommended, "amount": amount, "term_months": term,
            "interest_rate": rate, "monthly_payment": payment, "reasons": list(reasons)}


def decide(s: Scenario, checks: dict, grade: str, score: int, factor_codes: list[str]) -> dict:
    """DecisionPolicy.Decide. factor_codes: коды факторов в порядке ответа скоринга (в причины идут первые три)."""
    c, p = COMPANIES[s.login], PRODUCTS[s.product]
    if stops := _hard_stops(checks):
        return _outcome("Rejected", reasons=stops)
    if [k for k in ("egrul", "fns", "credit_bureau", "fssp") if checks.get(k) is None]:
        return _outcome("ManualReview", _recommended(grade), reasons=["checks_missing"])
    base = ["risk_grade", *[f"factor_{code}" for code in factor_codes[:3]]]
    if grade == "E":
        return _outcome("Rejected", reasons=["grade_e", *base])
    if grade == "D":
        return _outcome("ManualReview", "ApprovedWithConditions", reasons=["grade_d", *base])
    if p.requires_collateral:
        return _outcome("ManualReview", _recommended(grade), reasons=["collateral_required", *base])
    if s.amount > p.auto_approval_limit:
        return _outcome("ManualReview", _recommended(grade), reasons=["amount_above_auto_limit", *base])
    rate = p.base_rate + RATE_PREMIUM[grade]
    if grade in "AB":
        return _outcome("Approved", None, s.amount, s.term, rate, monthly_payment(s.amount, rate, s.term), base)
    capped = max(min(s.amount, net_round(c.revenue * POLICY["max_share_of_revenue"], 0)), p.min_amount)
    reasons = list(base)
    if capped < s.amount:
        reasons.insert(1, "amount_reduced")
    return _outcome("ApprovedWithConditions", None, capped, s.term, rate, monthly_payment(capped, rate, s.term), reasons)


def demo_manager_approve(s: Scenario, grade: str | None) -> dict:
    """DemoManagerPolicy.Approve: вариант «менеджер одобрил» (в демо 50/50 с отказом)."""
    c, p = COMPANIES[s.login], PRODUCTS[s.product]
    g = grade or "C"
    rate = p.base_rate + RATE_PREMIUM[g]
    amount, conditions = s.amount, []
    cap = max(p.min_amount, net_round(c.revenue * D("0.5"), 0))
    if amount > cap:
        amount = cap
        conditions.append("amount_reduced")
    if rate > p.base_rate:
        conditions.append("rate_premium")
    if grade is None or g == "D":
        conditions.append("bank_statement_6m")
    return {"type": "ApprovedWithConditions" if conditions else "Approved", "amount": amount, "term_months": s.term,
            "interest_rate": rate, "monthly_payment": monthly_payment(amount, rate, s.term), "conditions": conditions}
