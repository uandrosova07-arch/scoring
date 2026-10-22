# scoring

Python-сервис оценки кредитного риска. Часть проекта AlfaCredit: backend (C#) вызывает его по
`POST http://scoring:8000/api/v1/score`, получает балл, грейд A–E и объяснение, а решение по заявке принимает сам.
Если сервис недоступен, backend считает по встроенным правилам и помечает результат как `fallback-rules`.

Контракт взят из репозитория backend: [`docs/scoring-contract.md`](https://github.com/GatheredSatyr53/probiinik/blob/ccr-ba8e7470-7yihoa/docs/scoring-contract.md).
Он главный: при расхождении правим этот сервис, а не наоборот.

Как считается оценка: [docs/calculation.md](docs/calculation.md).
Реальные запросы и ответы: [docs/score-examples.md](docs/score-examples.md).
Сравнение с резервными C#-правилами backend (где грейды совпадают, где нет): [docs/grade-comparison.md](docs/grade-comparison.md).

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
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Проверка: `curl localhost:8000/health`. Swagger: http://localhost:8000/docs.
Контейнер слушает `0.0.0.0:8000`, имя сервиса в общем compose: `scoring`.

Дымовая проверка запущенного сервиса (в том числе контейнера): `/health` и три примера в `/api/v1/score`
с проверкой результата. Нужен только Python, зависимости ставить не нужно.

```bash
python scripts/smoke.py                      # http://localhost:8000
python scripts/smoke.py http://scoring:8000  # любой другой адрес
```

Код выхода 0, если всё совпало с ожидаемым, иначе 1.

Переменные окружения сервису не нужны. Версия правил (`rules-0.2`) зашита в код: `app/rules.py`, константа `MODEL_VERSION`.
Она приходит в поле `model_version` ответа и в `/health`.

## Тесты

```bash
pytest -q
```

Тесты проверяют: все примеры из `data/samples.json` и демонстрационные компании из `data/demo_companies.json` дают ожидаемый балл, грейд и факторы; повторный запрос даёт тот же ответ;
форма ответа соответствует контракту (3–7 факторов по убыванию модуля вклада); балл равен 50 плюс сумма вкладов;
пропуски дают грейд D и список `missing_data`; некорректные значения, `NaN` и `Infinity` дают `400`.

## Контракт

`POST /api/v1/score`, JSON, `snake_case`, рубли целыми числами, пропуск = `null`. `NaN` и `Infinity` недопустимы.
Сервис читает только нужные поля, остальное игнорирует, поэтому backend может расширять запрос.

Используемые поля запроса:

| Поле | Зачем |
|---|---|
| `request_id` | возвращается в ответе без изменений |
| `application.amount_rub`, `application.term_months` | параметры заявки (обязательны) |
| `application.estimated_monthly_payment_rub` | новый платёж, его считает backend; если нет, берётся сумма / срок |
| `company.business_age_months` | возраст бизнеса |
| `company.financials.revenue_rub`, `expenses_rub`, `net_profit_rub` | за 12 месяцев |
| `company.financials.monthly_debt_payments_rub` | текущие платежи по кредитам за месяц |
| `checks.*` | блоки внешних проверок; пока только сообщаем в `warnings`, если блок `null` |

Ответ `200`: `request_id`, `model_version`, `score` (0..100), `risk_grade` (A..E), `probability_of_default`,
`factors[]` (`code`, `title`, `impact`, `value`, `client_explanation`), `warnings[]`, а также `missing_data[]` (расширение контракта).
Подробный пример: [docs/score-examples.md](docs/score-examples.md).

Ошибка: `400` и `{"error": "validation", "details": {"поле": "описание"}}`.
`POST /score` работает как короткий псевдоним того же адреса.

## Данные

- `data/demo_companies.json`: пять демонстрационных компаний по профилям seed backend (устойчивая, с заметной нагрузкой,
  с крупной суммой, с просрочками, с недостающими данными). У каждой `input` (запрос в формате контракта) и `expected`
  (балл, грейд, факторы, предупреждения, пропуски) лежат отдельно, плюс справочный грейд резервных C#-правил.
  Эти же компании прогоняет `scripts/smoke.py`.
- `data/samples.json`: 12 синтетических примеров для тестов, четыре сценария по три, `input` и `expected` отдельно.

В обоих файлах `expected` посчитан независимым кодом, а не самим сервисом. Финансовые цифры синтетические.

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

- **Баллы и пороги** предварительные, их нужно согласовать с продактом и бэкендером как условия демонстрации.
- **Грейды Python и резервных C#-правил** совпадают на четырёх демонстрационных компаниях из пяти и расходятся на произвольных данных:
  подробности и варианты в [docs/grade-comparison.md](docs/grade-comparison.md).
- **Нехватка данных.** Контракт требует `score` и `risk_grade` всегда. Предложено: если нет выручки, расходов или платежей
  по кредитам, возвращать `score` 40 и грейд D (ручное рассмотрение), а недостающее перечислять в `missing_data`.
  Бэкендеру нужно подтвердить, что это его устраивает.
- **`probability_of_default`** всегда `null`: правила не откалиброваны на дефолтах. Backend считает его сам по формуле.
- **`missing_data`** не входит в контракт, это расширение. Backend может его игнорировать или использовать для запроса данных у клиента.
- **Версия модели.** В примере контракта `lgbm-2026.10.21`, у нас пока `rules-0.2`: это правила, а не обученная модель.
- Блоки внешних проверок (`checks`) в расчёте пока не используются: стоп-факторы по ним проверяет backend.
