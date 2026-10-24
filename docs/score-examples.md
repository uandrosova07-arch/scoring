# Примеры вызова `POST /api/v1/score`

Контракт: `docs/scoring-contract.md` в репозитории backend. Адрес внутри Compose: `http://scoring:8000/api/v1/score`.
Запросы собраны из seed backend (коммит `9f18dd4`) ровно в том виде, как их отправляет backend, возраст бизнеса на 2026-10-23. Ответы сняты с запущенного сервиса `rules-0.3` командой `python scripts/demo_comparison.py examples http://localhost:8000`, а не написаны вручную.
Готовые файлы запросов: `data/requests/*.json` (`curl -d @data/requests/demo-good.json ...`). Сравнение с резервными C#-правилами и итоговые решения: [grade-comparison.md](grade-comparison.md).

## ООО «Ромашка»: стабильный бизнес (`demo-good`): 95 баллов, грейд A

Заявка: working-capital, 1 500 000 ₽ на 12 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-good.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000901",
  "model_version": "rules-0.3",
  "score": 95,
  "risk_grade": "A",
  "probability_of_default": 0.035,
  "factors": [
    {
      "code": "credit_history",
      "title": "Кредитная история",
      "impact": 15,
      "value": "без просрочек",
      "client_explanation": "Просрочек по кредитам за два года нет."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 15,
      "value": "32.0x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "7 г. 7 мес.",
      "client_explanation": "Компания работает больше трёх лет, это снижает риск."
    },
    {
      "code": "tax_debt",
      "title": "Налоговая задолженность",
      "impact": 5,
      "value": "нет",
      "client_explanation": "Задолженности перед налоговой нет."
    },
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": 0,
      "value": "0.48",
      "client_explanation": "Платежи по кредитам занимают около половины месячной прибыли."
    },
    {
      "code": "enforcement",
      "title": "Исполнительные производства",
      "impact": 0,
      "value": "нет",
      "client_explanation": "Открытых исполнительных производств нет."
    },
    {
      "code": "expense_ratio",
      "title": "Расходы к выручке",
      "impact": 0,
      "value": "0.85",
      "client_explanation": "Расходы занимают обычную долю выручки."
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ИП Кузнецов: заметная долговая нагрузка (`demo-medium`): 55 баллов, грейд C

Заявка: working-capital, 3 000 000 ₽ на 24 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-medium.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000902",
  "model_version": "rules-0.3",
  "score": 55,
  "risk_grade": "C",
  "probability_of_default": 0.155,
  "factors": [
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "3 г. 4 мес.",
      "client_explanation": "Компания работает больше трёх лет, это снижает риск."
    },
    {
      "code": "credit_history",
      "title": "Кредитная история",
      "impact": -5,
      "value": "просрочка до 25 дн.",
      "client_explanation": "За последние два года были просрочки по кредитам."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 5,
      "value": "3.2x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "tax_debt",
      "title": "Налоговая задолженность",
      "impact": -5,
      "value": "12 000 ₽",
      "client_explanation": "Есть задолженность перед налоговой."
    },
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": 0,
      "value": "0.48",
      "client_explanation": "Платежи по кредитам занимают около половины месячной прибыли."
    },
    {
      "code": "enforcement",
      "title": "Исполнительные производства",
      "impact": 0,
      "value": "нет",
      "client_explanation": "Открытых исполнительных производств нет."
    },
    {
      "code": "expense_ratio",
      "title": "Расходы к выручке",
      "impact": 0,
      "value": "0.44",
      "client_explanation": "Расходы занимают обычную долю выручки."
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ООО «Вектор»: крупная сумма (`demo-manual`): 70 баллов, грейд B

Заявка: working-capital, 8 000 000 ₽ на 24 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-manual.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000903",
  "model_version": "rules-0.3",
  "score": 70,
  "risk_grade": "B",
  "probability_of_default": 0.11,
  "factors": [
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": -25,
      "value": "1.20",
      "client_explanation": "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли."
    },
    {
      "code": "credit_history",
      "title": "Кредитная история",
      "impact": 15,
      "value": "без просрочек",
      "client_explanation": "Просрочек по кредитам за два года нет."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 15,
      "value": "22.5x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "10 г. 1 мес.",
      "client_explanation": "Компания работает больше трёх лет, это снижает риск."
    },
    {
      "code": "tax_debt",
      "title": "Налоговая задолженность",
      "impact": 5,
      "value": "нет",
      "client_explanation": "Задолженности перед налоговой нет."
    },
    {
      "code": "enforcement",
      "title": "Исполнительные производства",
      "impact": 0,
      "value": "нет",
      "client_explanation": "Открытых исполнительных производств нет."
    },
    {
      "code": "expense_ratio",
      "title": "Расходы к выручке",
      "impact": 0,
      "value": "0.89",
      "client_explanation": "Расходы занимают обычную долю выручки."
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ООО «Закат»: просрочки и налоговая задолженность (`demo-bad`): 0 баллов, грейд E

Заявка: express, 500 000 ₽ на 6 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-bad.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000904",
  "model_version": "rules-0.3",
  "score": 0,
  "risk_grade": "E",
  "probability_of_default": 0.32,
  "factors": [
    {
      "code": "credit_history",
      "title": "Кредитная история",
      "impact": -30,
      "value": "просрочка 95 дн.",
      "client_explanation": "В кредитной истории есть длительные просрочки или списания."
    },
    {
      "code": "tax_debt",
      "title": "Налоговая задолженность",
      "impact": -30,
      "value": "700 000 ₽",
      "client_explanation": "Есть налоговая задолженность и заблокированные счета."
    },
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": -25,
      "value": "прибыль отсутствует",
      "client_explanation": "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 15,
      "value": "28.0x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "6 г. 8 мес.",
      "client_explanation": "Компания работает больше трёх лет, это снижает риск."
    },
    {
      "code": "enforcement",
      "title": "Исполнительные производства",
      "impact": -10,
      "value": "2 на 1 400 000 ₽",
      "client_explanation": "Есть открытые исполнительные производства."
    },
    {
      "code": "expense_ratio",
      "title": "Расходы к выручке",
      "impact": -10,
      "value": "0.99",
      "client_explanation": "Расходы забирают почти всю выручку, запас прочности небольшой."
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ИП Петрова: бизнесу 4 месяца, нет расходов и прибыли (`demo-new`): 25 баллов, грейд E

Заявка: express, 300 000 ₽ на 12 мес.
В демо такой запрос не отправляется: возраст бизнеса 4 мес., для продукта нужно не менее 6.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-new.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000905",
  "model_version": "rules-0.3",
  "score": 25,
  "risk_grade": "E",
  "probability_of_default": 0.245,
  "factors": [
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": -25,
      "value": "2.82",
      "client_explanation": "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли."
    },
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": -20,
      "value": "4 мес.",
      "client_explanation": "Бизнес молодой, банк относится к таким заявкам осторожнее."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 15,
      "value": "4.0x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "tax_debt",
      "title": "Налоговая задолженность",
      "impact": 5,
      "value": "нет",
      "client_explanation": "Задолженности перед налоговой нет."
    },
    {
      "code": "credit_history",
      "title": "Кредитная история",
      "impact": 0,
      "value": "нет истории",
      "client_explanation": "Кредитной истории пока нет, это не плюс и не минус."
    },
    {
      "code": "enforcement",
      "title": "Исполнительные производства",
      "impact": 0,
      "value": "нет",
      "client_explanation": "Открытых исполнительных производств нет."
    }
  ],
  "warnings": [
    "expenses_missing",
    "net_profit_missing"
  ],
  "missing_data": [
    "company.financials.expenses_rub",
    "company.financials.net_profit_rub",
    "checks.fns.reported_revenue_rub"
  ]
}
```

## Некорректный запрос

Сумма строкой и нулевой срок в запросе `demo-good`. Сервис не приводит типы молча и отвечает `400`; backend на `4xx` не повторяет запрос и сразу считает резервными C#-правилами (`RulesFallback`).

Ответ `400`:

```json
{
  "error": "validation",
  "details": {
    "application.amount_rub": "Input should be a valid number",
    "application.term_months": "Input should be greater than or equal to 1"
  }
}
```
