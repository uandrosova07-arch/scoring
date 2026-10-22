# Примеры вызова `POST /api/v1/score`

Контракт: `docs/scoring-contract.md` в репозитории backend. Адрес внутри Compose: `http://scoring:8000/api/v1/score`.
Запросы и ответы ниже сняты с запущенного сервиса версии `rules-0.2`, а не написаны вручную. Тот же запрос всегда даёт тот же ответ.
Все пять демонстрационных компаний с ожидаемыми результатами: `data/demo_companies.json`. Сравнение с резервными C#-правилами: [grade-comparison.md](grade-comparison.md). Короткий адрес `POST /score` работает как псевдоним.

## Низкий риск: ООО «Ромашка», устойчивые финансы (грейд A)

```bash
curl -s -X POST http://localhost:8000/api/v1/score \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "request_id": "00000000-0000-4000-8000-000000000901",
  "application": {
    "id": "demo-good",
    "number": "AC-000901",
    "created_at": "2026-10-20T13:17:20Z",
    "amount_rub": 1500000,
    "term_months": 12,
    "purpose": "WorkingCapital",
    "estimated_monthly_payment_rub": 137520,
    "product": {
      "code": "working-capital",
      "type": "WorkingCapital",
      "base_rate_percent": 18.0,
      "requires_collateral": false,
      "auto_approval_limit_rub": 5000000
    },
    "collateral_offered": false
  },
  "company": {
    "inn": "7701234567",
    "legal_form": "Ooo",
    "okved": "47.11",
    "region_code": "77",
    "registered_at": "2019-10-12",
    "business_age_months": 84,
    "tax_regime": "UsnIncome",
    "employees_count": 12,
    "financials": {
      "period_months": 12,
      "revenue_rub": 48000000,
      "expenses_rub": 30000000,
      "net_profit_rub": 18000000,
      "monthly_debt_payments_rub": 100000
    },
    "history": {
      "previous_applications": 0,
      "previous_rejections": 0,
      "previous_loans_disbursed": 0
    }
  },
  "checks": {
    "egrul": {
      "status": "Active",
      "head_disqualified": false,
      "mass_address": false
    },
    "fns": {
      "tax_debt_rub": 0,
      "has_blocked_accounts": false,
      "reported_revenue_rub": 48000000
    },
    "credit_bureau": {
      "credit_history_months": 60,
      "active_loans": 1,
      "closed_loans": 4,
      "total_debt_rub": 0,
      "monthly_payments_rub": 100000,
      "max_overdue_days_24m": 0,
      "overdue_count_24m": 0,
      "has_write_offs": false
    },
    "fssp": {
      "open_proceedings": 0,
      "open_amount_rub": 0
    }
  }
}
JSON
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000901",
  "model_version": "rules-0.2",
  "score": 95,
  "risk_grade": "A",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": 20,
      "value": "16%",
      "client_explanation": "Платежи по кредитам, включая новый, составляют небольшую часть прибыли"
    },
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": 15,
      "value": "63%",
      "client_explanation": "Расходы заметно ниже выручки"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "84 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## Высокий риск: ООО «Закат», просрочки и налоговый долг (грейд E)

```bash
curl -s -X POST http://localhost:8000/api/v1/score \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "request_id": "00000000-0000-4000-8000-000000000904",
  "application": {
    "id": "demo-bad",
    "number": "AC-000904",
    "created_at": "2026-10-20T13:17:20Z",
    "amount_rub": 1000000,
    "term_months": 12,
    "purpose": "WorkingCapital",
    "estimated_monthly_payment_rub": 91680,
    "product": {
      "code": "working-capital",
      "type": "WorkingCapital",
      "base_rate_percent": 18.0,
      "requires_collateral": false,
      "auto_approval_limit_rub": 5000000
    },
    "collateral_offered": false
  },
  "company": {
    "inn": "5401234567",
    "legal_form": "Ooo",
    "okved": "47.11",
    "region_code": "54",
    "registered_at": "2020-10-12",
    "business_age_months": 72,
    "tax_regime": "UsnIncome",
    "employees_count": 12,
    "financials": {
      "period_months": 12,
      "revenue_rub": 30000000,
      "expenses_rub": 28500000,
      "net_profit_rub": 1500000,
      "monthly_debt_payments_rub": 80000
    },
    "history": {
      "previous_applications": 0,
      "previous_rejections": 0,
      "previous_loans_disbursed": 0
    }
  },
  "checks": {
    "egrul": {
      "status": "Active",
      "head_disqualified": false,
      "mass_address": false
    },
    "fns": {
      "tax_debt_rub": 600000,
      "has_blocked_accounts": false,
      "reported_revenue_rub": 30000000
    },
    "credit_bureau": {
      "credit_history_months": 60,
      "active_loans": 1,
      "closed_loans": 4,
      "total_debt_rub": 0,
      "monthly_payments_rub": 80000,
      "max_overdue_days_24m": 120,
      "overdue_count_24m": 3,
      "has_write_offs": false
    },
    "fssp": {
      "open_proceedings": 2,
      "open_amount_rub": 400000
    }
  }
}
JSON
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000904",
  "model_version": "rules-0.2",
  "score": 20,
  "risk_grade": "E",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": -20,
      "value": "95%",
      "client_explanation": "Расходы почти равны выручке или превышают её"
    },
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": -20,
      "value": "137%",
      "client_explanation": "Платежи по кредитам, включая новый, занимают слишком большую часть прибыли"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "72 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## Нехватка данных: ООО «Север», нет расходов, платежей и ответа БКИ (грейд D, ручное рассмотрение)

```bash
curl -s -X POST http://localhost:8000/api/v1/score \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "request_id": "00000000-0000-4000-8000-000000000905",
  "application": {
    "id": "demo-incomplete",
    "number": "AC-000905",
    "created_at": "2026-10-20T13:17:20Z",
    "amount_rub": 1000000,
    "term_months": 12,
    "purpose": "WorkingCapital",
    "estimated_monthly_payment_rub": 91680,
    "product": {
      "code": "working-capital",
      "type": "WorkingCapital",
      "base_rate_percent": 18.0,
      "requires_collateral": false,
      "auto_approval_limit_rub": 5000000
    },
    "collateral_offered": false
  },
  "company": {
    "inn": "7707654321",
    "legal_form": "Ooo",
    "okved": "47.11",
    "region_code": "77",
    "registered_at": "2024-10-12",
    "business_age_months": 24,
    "tax_regime": "UsnIncome",
    "employees_count": 12,
    "financials": {
      "period_months": 12,
      "revenue_rub": 18000000,
      "expenses_rub": null,
      "net_profit_rub": null,
      "monthly_debt_payments_rub": null
    },
    "history": {
      "previous_applications": 0,
      "previous_rejections": 0,
      "previous_loans_disbursed": 0
    }
  },
  "checks": {
    "egrul": {
      "status": "Active",
      "head_disqualified": false,
      "mass_address": false
    },
    "fns": {
      "tax_debt_rub": 0,
      "has_blocked_accounts": false,
      "reported_revenue_rub": 18000000
    },
    "credit_bureau": null,
    "fssp": {
      "open_proceedings": 0,
      "open_amount_rub": 0
    }
  }
}
JSON
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000905",
  "model_version": "rules-0.2",
  "score": 40,
  "risk_grade": "D",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_insufficient_data",
      "title": "Достаточность данных",
      "impact": -10,
      "value": "не хватает: expenses_rub, monthly_debt_payments_rub",
      "client_explanation": "Для автоматической оценки не хватает данных, заявку рассмотрит менеджер"
    },
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": 0,
      "value": "нет данных",
      "client_explanation": "Не хватает данных о выручке или расходах"
    },
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": 0,
      "value": "нет данных",
      "client_explanation": "Пока не хватает данных, поэтому этот показатель в оценке не учтён"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 0,
      "value": "24 мес.",
      "client_explanation": "Пока не хватает данных, поэтому возраст бизнеса в оценке не учтён"
    }
  ],
  "warnings": [
    "insufficient_data",
    "credit_bureau_missing"
  ],
  "missing_data": [
    "company.financials.expenses_rub",
    "company.financials.monthly_debt_payments_rub"
  ]
}
```

## Некорректный запрос

Ответ `400`:

```json
{
  "error": "validation",
  "details": {
    "application.amount_rub": "Input should be greater than 0",
    "application.term_months": "Input should be a valid integer"
  }
}
```
