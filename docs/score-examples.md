# Примеры вызова `POST /api/v1/score`

Контракт: `docs/scoring-contract.md` в репозитории backend. Адрес внутри Compose: `http://scoring:8000/api/v1/score`.
Запросы собраны из seed backend (коммит `9f18dd4`) ровно в том виде, как их отправляет backend, возраст бизнеса на 2026-10-23. Ответы сняты с запущенного сервиса `rules-0.2` командой `python scripts/demo_comparison.py examples http://localhost:8000`, а не написаны вручную.
Готовые файлы запросов: `data/requests/*.json` (`curl -d @data/requests/demo-good.json ...`). Сравнение с резервными C#-правилами и итоговые решения: [grade-comparison.md](grade-comparison.md).

## ООО «Ромашка»: стабильный бизнес (`demo-good`): 55 баллов, грейд C

Заявка: working-capital, 1 500 000 ₽ на 12 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-good.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000901",
  "model_version": "rules-0.2",
  "score": 55,
  "risk_grade": "C",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "91 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    },
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": -5,
      "value": "85%",
      "client_explanation": "Расходы занимают значительную часть выручки"
    },
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": 0,
      "value": "48%",
      "client_explanation": "Платежи по кредитам, включая новый, составляют заметную часть прибыли"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ИП Кузнецов: заметная долговая нагрузка (`demo-medium`): 75 баллов, грейд B

Заявка: working-capital, 3 000 000 ₽ на 24 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-medium.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000902",
  "model_version": "rules-0.2",
  "score": 75,
  "risk_grade": "B",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": 15,
      "value": "44%",
      "client_explanation": "Расходы заметно ниже выручки"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "40 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    },
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": 0,
      "value": "48%",
      "client_explanation": "Платежи по кредитам, включая новый, составляют заметную часть прибыли"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ООО «Вектор»: крупная сумма (`demo-manual`): 35 баллов, грейд D

Заявка: working-capital, 8 000 000 ₽ на 24 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-manual.json
```

Ответ `200`:

```json
{
  "request_id": "00000000-0000-4000-8000-000000000903",
  "model_version": "rules-0.2",
  "score": 35,
  "risk_grade": "D",
  "probability_of_default": null,
  "factors": [
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": -20,
      "value": "120%",
      "client_explanation": "Платежи по кредитам, включая новый, занимают слишком большую часть прибыли"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "121 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    },
    {
      "code": "factor_expense_ratio",
      "title": "Расходы к выручке",
      "impact": -5,
      "value": "89%",
      "client_explanation": "Расходы занимают значительную часть выручки"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ООО «Закат»: просрочки и налоговая задолженность (`demo-bad`): 20 баллов, грейд E

Заявка: express, 500 000 ₽ на 6 мес.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-bad.json
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
      "value": "99%",
      "client_explanation": "Расходы почти равны выручке или превышают её"
    },
    {
      "code": "factor_debt_load",
      "title": "Кредитная нагрузка",
      "impact": -20,
      "value": "8268%",
      "client_explanation": "Платежи по кредитам, включая новый, занимают слишком большую часть прибыли"
    },
    {
      "code": "factor_business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "80 мес.",
      "client_explanation": "Бизнесу больше трёх лет, это снижает риск"
    }
  ],
  "warnings": [],
  "missing_data": []
}
```

## ИП Петрова: бизнесу 4 месяца, нет расходов и прибыли (`demo-new`): 40 баллов, грейд D

Заявка: express, 300 000 ₽ на 12 мес.
В демо такой запрос не отправляется: возраст бизнеса 4 мес., для продукта нужно не менее 6.

```bash
curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/demo-new.json
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
      "value": "не хватает: expenses_rub",
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
      "value": "4 мес.",
      "client_explanation": "Пока не хватает данных, поэтому возраст бизнеса в оценке не учтён"
    }
  ],
  "warnings": [
    "insufficient_data"
  ],
  "missing_data": [
    "company.financials.expenses_rub"
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
