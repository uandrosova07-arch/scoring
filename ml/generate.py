"""Воспроизводимый генератор синтетических компаний и заявок с исходом default_12m.

    python -m ml.generate                       # data/synthetic/applications.csv.gz, seed 20261025, 20 000 компаний
    python -m ml.generate --seed 7 --companies 2000 --out /tmp/small.csv.gz

Устройство:
- у компании есть ненаблюдаемое качество q ~ N(0, 1). Оно сдвигает финансы, кредитную историю, налоги и ФССП,
  поэтому признаки с ним коррелируют, но полностью его не раскрывают;
- у компании 1–4 заявки (в среднем ~1,5, всего ~30 000): разные суммы, сроки, ставки, залог;
- скрытая PD заявки = сигмоида от «истинных» величин (нагрузка по настоящей прибыли с порогом около 0,6, тонкая маржа,
  молодость, просрочки, списания, налоги и блокировки, ФССП, сумма к выручке, залог) + вклад q + шум N(0; 0,4).
  Свободный член подбирается так, чтобы средняя PD была ≈ 9 %;
- default_12m ~ Bernoulli(скрытая PD): исход случайный, одинаковые признаки могут дать разные исходы;
- наблюдаемые данные с пропусками: прибыль не указана у ~12 % (у молодых чаще), расходы у ~8 %, ответов БКИ, ФНС, ФССП
  нет у ~3 % каждого; отчётная прибыль отличается от настоящей на шум.

Скрытые столбцы начинаются с подчёркивания (_quality, _true_pd, _true_profit) и в признаки не попадают
(ml/train.py проверяет это явно). Событий после даты заявки в данных нет: все поля описывают состояние на момент подачи.
Баллы и грейды rules-0.3 в генераторе не используются.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "synthetic" / "applications.csv.gz"
SEED = 20261025
TARGET_MEAN_PD = 0.09


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _annuity(amount, rate_percent, term):
    r = rate_percent / 100 / 12
    return np.round(amount * r / (1 - (1 + r) ** (-term)))


def generate(companies: int = 20_000, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = companies
    q = rng.normal(0, 1, n)

    young = rng.random(n) < 0.2
    age = np.where(young, rng.uniform(0, 24, n), np.exp(rng.normal(np.log(60), 0.7, n)))
    age = np.clip(np.floor(age), 0, 360).astype(int)

    revenue = np.exp(rng.normal(np.log(25e6) + 0.3 * q + 0.25 * np.log((age + 1) / 60), 1.0, n))
    revenue = np.round(np.clip(revenue, 1e6, 2e9), -3)
    expense_ratio = np.clip(0.82 - 0.06 * q + rng.normal(0, 0.08, n), 0.4, 1.3)
    pre_tax = revenue * (1 - expense_ratio)
    true_profit = np.where(pre_tax > 0, pre_tax * 0.8, pre_tax)

    has_loans = rng.random(n) < 0.6
    debt_share = np.clip(np.exp(rng.normal(np.log(0.025) - 0.2 * q, 0.6, n)), 0, 0.5)
    monthly_debt = np.where(has_loans, np.round(revenue / 12 * debt_share, -2), 0)

    # БКИ
    never_borrowed = rng.random(n) < np.where(age < 12, 0.35, 0.05)
    history = np.where(never_borrowed & ~has_loans, 0, np.minimum(age, rng.integers(6, 121, n)))
    active = np.where(has_loans, 1 + rng.poisson(1.0, n), 0)
    closed = np.where(history > 0, rng.poisson(1.5, n), 0)
    had_overdue = (rng.random(n) < _sigmoid(-1.6 - 0.9 * q)) & (history > 0)
    overdue = np.where(had_overdue, np.clip(np.round(np.exp(rng.normal(np.log(20) - 0.4 * q, 1.0, n))), 1, 360), 0).astype(int)
    write_offs = (rng.random(n) < _sigmoid(-4.5 - 1.0 * q)) & (history > 0)

    # ФНС и ФССП
    has_tax_debt = rng.random(n) < _sigmoid(-1.8 - 0.8 * q)
    tax_debt = np.where(has_tax_debt, np.round(np.exp(rng.normal(np.log(40_000) - 0.3 * q, 1.3, n)), -2), 0)
    blocked = rng.random(n) < _sigmoid(-4.0 - 0.8 * q + 1.5 * (tax_debt >= 100_000))
    fssp_count = np.minimum(rng.poisson(np.exp(-2.3 - 0.8 * q)), 20)
    fssp_amount = np.where(fssp_count > 0, np.round(fssp_count * np.exp(rng.normal(np.log(150_000), 1.0, n)), -2), 0)

    # Наблюдаемая финансовая отчётность с пропусками и шумом
    reported_profit = np.round(true_profit * (1 + rng.normal(0, 0.1, n)), -3)
    profit_missing = rng.random(n) < np.where(age < 12, 0.25, 0.10)
    expenses_missing = rng.random(n) < 0.08
    bureau_missing, fns_missing, fssp_missing = (rng.random(n) < 0.03 for _ in range(3))

    company = pd.DataFrame({
        "company_id": np.arange(n), "business_age_months": age, "revenue_rub": revenue,
        "expenses_rub": np.where(expenses_missing, np.nan, np.round(revenue * expense_ratio, -3)),
        "net_profit_rub": np.where(profit_missing, np.nan, reported_profit),
        "monthly_debt_payments_rub": monthly_debt,
        "bureau_present": ~bureau_missing, "credit_history_months": history, "active_loans": active, "closed_loans": closed,
        "max_overdue_days_24m": overdue, "has_write_offs": write_offs,
        "fns_present": ~fns_missing, "tax_debt_rub": tax_debt, "has_blocked_accounts": blocked,
        "fssp_present": ~fssp_missing, "open_proceedings": fssp_count, "open_amount_rub": fssp_amount,
        "_quality": q, "_true_profit": true_profit,
    })

    # Заявки: 1–4 на компанию
    per_company = np.minimum(1 + rng.poisson(0.5, n), 4)
    apps = company.loc[np.repeat(company.index, per_company)].reset_index(drop=True)
    m = len(apps)
    apps.insert(0, "application_id", np.arange(m))
    amount = apps["revenue_rub"].to_numpy() * np.exp(rng.normal(np.log(0.07), 0.6, m))
    apps["amount_rub"] = np.round(np.clip(amount, 100_000, 50_000_000), -3)
    apps["term_months"] = rng.choice([6, 12, 18, 24, 36, 48, 60], m, p=[0.08, 0.30, 0.12, 0.25, 0.15, 0.05, 0.05])
    apps["base_rate_percent"] = np.round(rng.uniform(17, 24, m), 1)
    apps["estimated_monthly_payment_rub"] = _annuity(apps["amount_rub"], apps["base_rate_percent"], apps["term_months"])
    apps["collateral_offered"] = rng.random(m) < 0.08 + 0.25 * (apps["amount_rub"] > 5_000_000)

    # Скрытая PD по «истинным» величинам
    tp = apps["_true_profit"].to_numpy()
    payments = apps["monthly_debt_payments_rub"].to_numpy() + apps["estimated_monthly_payment_rub"].to_numpy()
    load = np.where(tp > 0, payments * 12 / np.maximum(tp, 1), 4.0)
    load = np.clip(load, 0, 4)
    er = (revenue * expense_ratio / revenue)[apps["company_id"]]
    a = apps
    logit = (
        -0.55 * a["_quality"]
        + 1.3 * _sigmoid((load - 0.6) / 0.15) + 0.2 * load
        + 6.0 * np.clip(er - 0.9, 0, 0.3)
        + 0.6 * (a["business_age_months"] < 12) + 0.3 * (a["business_age_months"] < 24)
        + 0.015 * np.minimum(a["max_overdue_days_24m"], 180) + 1.5 * a["has_write_offs"]
        + 0.5 * (a["tax_debt_rub"] > 0) + 0.4 * (a["tax_debt_rub"] >= 500_000) + 0.9 * a["has_blocked_accounts"]
        + 0.35 * np.minimum(a["open_proceedings"], 3)
        + 0.4 * np.clip(a["amount_rub"] / a["revenue_rub"], 0, 3)
        - 0.4 * a["collateral_offered"]
        + rng.normal(0, 0.4, m)
    ).to_numpy()
    lo, hi = -10.0, 5.0
    for _ in range(60):  # свободный член: средняя PD = TARGET_MEAN_PD
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _sigmoid(logit + mid).mean() < TARGET_MEAN_PD else (lo, mid)
    true_pd = _sigmoid(logit + (lo + hi) / 2)
    apps["_true_pd"] = true_pd
    apps["default_12m"] = (rng.random(m) < true_pd).astype(int)
    return apps


def to_request(row) -> dict:
    """Строка генератора → запрос /api/v1/score в формате контракта (только наблюдаемые поля)."""
    def num(x):
        return None if pd.isna(x) else float(x)
    return {
        "request_id": f"synthetic-{int(row['application_id'])}",
        "application": {"amount_rub": float(row["amount_rub"]), "term_months": int(row["term_months"]),
                        "estimated_monthly_payment_rub": float(row["estimated_monthly_payment_rub"]),
                        "collateral_offered": bool(row["collateral_offered"])},
        "company": {"business_age_months": int(row["business_age_months"]), "financials": {
            "revenue_rub": float(row["revenue_rub"]), "expenses_rub": num(row["expenses_rub"]),
            "net_profit_rub": num(row["net_profit_rub"]), "monthly_debt_payments_rub": float(row["monthly_debt_payments_rub"])}},
        "checks": {
            "egrul": {"status": "Active"},
            "credit_bureau": {"credit_history_months": int(row["credit_history_months"]), "active_loans": int(row["active_loans"]),
                              "closed_loans": int(row["closed_loans"]), "max_overdue_days_24m": int(row["max_overdue_days_24m"]),
                              "has_write_offs": bool(row["has_write_offs"])} if row["bureau_present"] else None,
            "fns": {"tax_debt_rub": float(row["tax_debt_rub"]), "has_blocked_accounts": bool(row["has_blocked_accounts"]),
                    "reported_revenue_rub": None} if row["fns_present"] else None,
            "fssp": {"open_proceedings": int(row["open_proceedings"]), "open_amount_rub": float(row["open_amount_rub"])}
            if row["fssp_present"] else None,
        },
    }


def dataset_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--companies", type=int, default=20_000)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()
    df = generate(args.companies, args.seed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"{len(df)} заявок, {df['company_id'].nunique()} компаний, дефолтов {df['default_12m'].mean():.3%}, "
          f"средняя скрытая PD {df['_true_pd'].mean():.3%}, sha256 {dataset_sha256(df)[:16]}… → {args.out}")


if __name__ == "__main__":
    main()
