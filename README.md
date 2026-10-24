# scoring

Python-сервис оценки кредитного риска. Часть проекта AlfaCredit: backend (C#) вызывает его по
`POST http://scoring:8000/api/v1/score`, получает балл, грейд A–E и объяснение, а решение по заявке принимает сам.
Если сервис недоступен, backend считает по встроенным правилам и помечает результат как `RulesFallback`.
С версии `rules-0.3` сервис и встроенные правила backend считают одинаково: факторы, пороги, тексты и обработка пропусков общие,
поэтому решение по заявке не зависит от того, кто посчитал.

Контракт взят из репозитория backend: [`docs/scoring-contract.md`](https://github.com/GatheredSatyr53/probiinik/blob/ccr-ba8e7470-7yihoa/docs/scoring-contract.md).
Он главный: при расхождении правим этот сервис, а не наоборот.

Как считается оценка: [docs/calculation.md](docs/calculation.md).
Реальные запросы и ответы: [docs/score-examples.md](docs/score-examples.md).
Согласование с резервными C#-правилами backend и результаты сверки: [docs/grade-comparison.md](docs/grade-comparison.md); что сделать в backend: [docs/backend/README.md](docs/backend/README.md).

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

Дымовая проверка запущенного сервиса (в том числе контейнера): `/health` и пять демо-заявок в `/api/v1/score`
с проверкой результата. Нужен только Python, зависимости ставить не нужно.

```bash
python scripts/smoke.py                      # http://localhost:8000
python scripts/smoke.py http://scoring:8000  # любой другой адрес
```

Код выхода 0, если всё совпало с ожидаемым, иначе 1.

Переменные окружения сервису не нужны. Версия правил (`rules-0.3`) зашита в код: `app/rules.py`, константа `MODEL_VERSION`.
Она приходит в поле `model_version` ответа и в `/health`.

## Проверка в общем стенде backend

Общий `docker-compose.yml` backend поднимает свою заглушку скоринга. Чтобы backend ходил в этот сервис, рядом с ним
кладётся override `docs/backend/compose.scoring.yml` (репозитории `probiinik` и `scoring` лежат в одной папке):

```bash
cd probiinik
cp ../scoring/docs/backend/compose.scoring.yml .
docker compose down -v                                             # чистая база и seed
docker compose -f docker-compose.yml -f compose.scoring.yml up --build -d
curl localhost:8000/health                                         # model_version: rules-0.3
cd ../scoring
python scripts/smoke.py                                            # сервис отвечает как ожидается
python scripts/e2e_backend.py                                      # демо-заявки через API backend
```

`scripts/e2e_backend.py` подаёт демо-заявки от имени клиентов, ждёт решения и по карточке менеджера сверяет балл, грейд и
решение со столбцом Python или C# из `data/demo_companies.json` (столбец выбирается по `scoring.source` и `modelVersion`).
Откат на резервные правила: `docker compose stop scoring`, снова `e2e_backend.py` (ожидается `RulesFallback`), затем
`docker compose start scoring`. Только встроенные правила:
`SCORING_MODE=Rules docker compose -f docker-compose.yml -f compose.scoring.yml up -d api`.

## Тесты

```bash
pytest -q
```

Тесты проверяют:
- общий эталон `data/conformance/rules-0.3.json` (демо-заявки, пропуски данных, все пороги с обеих сторон): ответ совпадает полностью,
  вместе с текстами факторов; 2 000 случайных запросов дают то же, что копия C# с правками `rules-0.3` (`tests/test_conformance.py`);
- примеры из `data/samples.json` и демо-заявки из `data/demo_companies.json`: балл, грейд, факторы, решение банка, задуманные исходы демо;
- форму ответа по контракту (3–8 факторов без префикса, по убыванию модуля вклада), PD по формуле backend, балл = 50 + сумма вкладов;
- всё, что может прислать backend, принимается, а `null` в обязательных полях и некорректные значения дают `400` (`tests/test_backend_contract.py`);
- копия расчётов backend проходит ассерты из тестов самого backend (`tests/test_backend_reference.py`); данные пересобираются тем же результатом.

## Контракт

`POST /api/v1/score`, JSON, `snake_case`, рубли целыми числами, пропуск = `null`. `NaN` и `Infinity` недопустимы.
Сервис читает только нужные поля, остальное игнорирует, поэтому backend может расширять запрос.

Используемые поля запроса:

| Поле | Зачем |
|---|---|
| `request_id` | возвращается в ответе без изменений |
| `application.amount_rub`, `application.term_months` | параметры заявки (обязательны) |
| `application.estimated_monthly_payment_rub` | новый платёж, его считает backend (обязателен) |
| `application.collateral_offered` | предложен ли залог (+10) |
| `company.business_age_months` | возраст бизнеса (обязателен) |
| `company.financials.revenue_rub`, `monthly_debt_payments_rub` | выручка за 12 месяцев и текущие платежи за месяц (обязательны) |
| `company.financials.expenses_rub`, `net_profit_rub` | за 12 месяцев, могут быть `null` |
| `checks.credit_bureau`, `checks.fns`, `checks.fssp` | кредитная история, налоги, исполнительные производства; блок может быть `null` |
| `checks.egrul` | в баллах не участвует, только в `missing_data` |

Обязательность повторяет типы запроса в backend: что в C# не nullable, здесь обязательно. Правила расчёта: [docs/calculation.md](docs/calculation.md).

Ответ `200`: `request_id`, `model_version` (`rules-0.3`), `score` (0..100), `risk_grade` (A..E), `probability_of_default`
(формула backend), `factors[]` (`code` без префикса, `title`, `impact`, `value`, `client_explanation`), `warnings[]`, `missing_data[]`.
Подробный пример: [docs/score-examples.md](docs/score-examples.md).

Ошибка: `400` и `{"error": "validation", "details": {"поле": "описание"}}`.
`POST /score` работает как короткий псевдоним того же адреса.

## Данные

- `data/demo_companies.json`: пять демо-заявок на данных seed backend (`SeedData.cs`, `KnownProfiles.cs`, коммит `9f18dd4`):
  те же продукты, суммы и сроки, что в общем демо, возраст бизнеса на 23.10.2026. У каждой `input` (запрос ровно в том виде,
  как его отправляет backend), `expected` (ответ Python), `csharp_fallback` (резервные правила backend в текущем виде, `rules-v1`)
  и `decision` (решение кредитной политики backend при каждом из ответов). Эти же заявки прогоняет `scripts/smoke.py`.
- `data/conformance/rules-0.3.json`: общий эталон для Python и C# (63 случая), собирает `scripts/conformance.py`.
- `data/requests/*.json`: те же пять запросов отдельными файлами, готовые для `curl -d @data/requests/demo-good.json`.
- `data/samples.json`: 12 синтетических примеров для тестов, четыре сценария по три, `input` и `expected` отдельно.

Во всех файлах `expected` посчитан копией C# с правками `rules-0.3` (`scripts/backend_reference.py`), а не самим сервисом.
Финансовые цифры `samples.json` синтетические.

Демо-данные собирает `scripts/demo_comparison.py` из копии seed и правил backend (`scripts/backend_reference.py`):

```bash
python scripts/demo_comparison.py build                      # пересобрать после изменений seed или правил backend
python scripts/demo_comparison.py check                      # убедиться, что файлы совпадают с пересборкой
python scripts/demo_comparison.py table http://localhost:8000  # таблица Python vs C# по живому сервису
python scripts/demo_comparison.py examples http://localhost:8000  # пересобрать docs/score-examples.md
```

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

- **Правки `rules-0.3` в C#.** Сервис уже считает по `rules-0.3`, в резервных правилах backend их нужно внести: правило для расходов,
  прибыль без `net_profit_rub`, нагрузка одним делением, версия. До этого балл и грейд расходятся в 3 случаях эталона из 63
  (все про расходы), демо-заявки совпадают. Что и как поменять: [docs/backend/README.md](docs/backend/README.md),
  сверка: [docs/grade-comparison.md](docs/grade-comparison.md).
- **Контракт backend** (`docs/scoring-contract.md`): обновить коды факторов (без префикса), число факторов (3–8), PD, обязательные поля.
- **Заглушка backend** `scoring/app.py` повторяет `rules-v1`: заменить этим сервисом в общем compose.
- **Версия модели.** В примере контракта `lgbm-2026.10.21`, у нас `rules-0.3`: это правила, а не обученная модель.
  PD — эвристика backend, не калибровка на дефолтах.
