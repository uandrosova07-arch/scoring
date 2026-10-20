# scoring

Python-сервис оценки кредитного риска. Часть проекта AlfaCredit: backend (C#) вызывает его по
`http://scoring:8000/score`, получает уровень риска с объяснением и сам формирует кредитное решение.

Расчёт построен на воспроизводимых правилах: [docs/calculation.md](docs/calculation.md).
Реальные примеры запросов и ответов: [docs/score-examples.md](docs/score-examples.md).

## Запуск

Через Docker:

```bash
docker build -t scoring .
docker run --rm -p 8000:8000 scoring
```

Без Docker (Python 3.12+):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000
```

Проверка: `curl localhost:8000/health`. Swagger: http://localhost:8000/docs.

Настройка одна: `MODEL_VERSION` (по умолчанию `rules-0.1`), см. `.env.example`.
Порт внутри контейнера 8000, имя сервиса в общем compose: `scoring`.

## Тесты

```bash
pytest -q
```

Тесты проверяют: все примеры из `data/samples.json` дают ожидаемый исход; повторный запрос даёт тот же ответ;
пропуски (`null`, отсутствие поля, `NaN`) дают `undetermined` и список `missing_data`; некорректные значения дают `422`;
значения в причинах пересчитываются независимо.

## Контракт `POST /score`

Все поля необязательны на уровне формата: отсутствующее поле и `null` считаются пропуском.
Деньги в рублях целыми числами, период в названии поля.

```json
{
  "business_age_months": 84,
  "revenue_rub": 48000000,
  "expenses_rub": 30000000,
  "net_profit_rub": 18000000,
  "monthly_debt_payments_rub": 100000,
  "requested_amount_rub": 1500000,
  "term_months": 12
}
```

Ответ `200`:

```json
{
  "risk_level": "low",
  "reasons": [{"code": "EXPENSE_RATIO_OK", "text": "…", "value": 0.625}],
  "missing_data": [],
  "model_version": "rules-0.1"
}
```

`risk_level`: `low | medium | high | undetermined`. Лишние поля в запросе (например, `"credit_bureau": null`) игнорируются.
Некорректные значения (отрицательная выручка, срок 0, строка вместо числа) дают `422` в формате ProblemDetails
(`application/problem+json`) со списком `errors`, расчёт при этом не выполняется.

## Данные

- `data/first_three.json`: три компании для бэкендера (устойчивые финансы, высокая нагрузка, недостающие сведения).
- `data/samples.json`: 12 синтетических примеров, четыре сценария по три. `input` и `expected` лежат отдельно.

## Сборка и деплой

Схема совпадает с остальными репозиториями: проверка и сборка, образ в реестр, единый деплой из `infra`.
Описана в `.github/workflows/ci.yml`:

1. Pull request и push в `main`: установка зависимостей и `pytest`.
2. Push в `main`: сборка образа и публикация в GitHub Container Registry
   (`ghcr.io/<владелец>/<репозиторий>:<sha коммита>` и `:latest`).
3. Если заданы секрет `INFRA_DISPATCH_TOKEN` и переменная `INFRA_REPO`, отправляется `repository_dispatch`
   (`event_type: component-updated`, в `client_payload` компонент `scoring`, образ и тег) в репозиторий `infra`.
   Без них шаг пропускается, образ всё равно публикуется.

В `infra` в общем `compose.yaml` образ указывается с конкретным тегом, а обновление отдельного сервиса выглядит так:

```bash
docker compose pull scoring
docker compose up -d --no-deps scoring
```

## Открытые вопросы

- **Названия `business_age_months`, `requested_amount_rub`, `term_months`** предложены мной, бэкендер их не подтверждал.
  Остальные поля взяты из его ответа.
- **`risk_level`**: значения `low/medium/high/undetermined` предварительные. Нужно решить с backend, как они ложатся
  на его грейды и что делать с `undetermined`.
- **Пороги** не согласованы с продактом, это условия демонстрации.
- Блоки внешних проверок (`credit_bureau` и другие) пока не используются и игнорируются.
