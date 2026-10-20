# Примеры вызова `/score`

Адрес внутри Compose: `http://scoring:8000/score`. Формат: JSON, snake_case, рубли целыми числами, пропуск = `null`.
Ответы ниже сняты с запущенного сервиса, а не написаны вручную.

## stable-1

```bash
curl -s -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{"business_age_months": 84, "revenue_rub": 48000000, "expenses_rub": 30000000, "net_profit_rub": 18000000, "monthly_debt_payments_rub": 100000, "requested_amount_rub": 1500000, "term_months": 12}'
```

```json
{
  "risk_level": "low",
  "reasons": [
    {
      "code": "EXPENSE_RATIO_OK",
      "text": "Расходы составляют 62% выручки",
      "value": 0.625
    },
    {
      "code": "DEBT_LOAD_LOW",
      "text": "Платежи по кредитам с учётом нового составят 15% месячной прибыли",
      "value": 0.15
    }
  ],
  "missing_data": [],
  "model_version": "rules-0.1"
}
```

## highload-1

```bash
curl -s -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{"business_age_months": 36, "revenue_rub": 36000000, "expenses_rub": 30000000, "net_profit_rub": 6000000, "monthly_debt_payments_rub": 250000, "requested_amount_rub": 3000000, "term_months": 12}'
```

```json
{
  "risk_level": "high",
  "reasons": [
    {
      "code": "EXPENSE_RATIO_MODERATE",
      "text": "Расходы составляют 83% выручки",
      "value": 0.8333
    },
    {
      "code": "DEBT_LOAD_HIGH",
      "text": "Платежи по кредитам с учётом нового составят 100% месячной прибыли",
      "value": 1.0
    }
  ],
  "missing_data": [],
  "model_version": "rules-0.1"
}
```

## missing-1

```bash
curl -s -X POST http://localhost:8000/score \
  -H 'Content-Type: application/json' \
  -d '{"business_age_months": 4, "revenue_rub": 3000000, "expenses_rub": null, "net_profit_rub": null, "monthly_debt_payments_rub": 0, "requested_amount_rub": 300000, "term_months": 6}'
```

```json
{
  "risk_level": "undetermined",
  "reasons": [
    {
      "code": "INSUFFICIENT_DATA",
      "text": "Для оценки не хватает данных: expenses_rub",
      "value": null
    }
  ],
  "missing_data": [
    "expenses_rub"
  ],
  "model_version": "rules-0.1"
}
```
