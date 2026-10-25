# scoring

Текущая интеграция, новый профиль C и запуск одинакового External/Fallback: [docs/integration-logreg.md](docs/integration-logreg.md).

Python-сервис оценки кредитного риска. Часть проекта AlfaCredit: backend (C#) вызывает его по
`POST http://scoring:8000/api/v1/score`, получает балл, грейд A–E и объяснение, а решение по заявке принимает сам.
Если сервис недоступен, backend считает ту же выбранную модель локально по общему артефакту и помечает результат как `RulesFallback`.

В сервисе две модели, выбор — переменная `SCORING_MODEL`:
- **`logreg-synthetic-0.1`** (по умолчанию): логистическая регрессия, обученная на синтетических заявках с исходом `default_12m`.
  Возвращает исходную PD модели, балл и грейд A–E выводятся из PD, объяснения — вклады самой модели. Описание: [docs/model.md](docs/model.md),
  метрики: [docs/model-metrics.md](docs/model-metrics.md), перенос в backend: [docs/backend/logreg.md](docs/backend/logreg.md).
- **`rules-0.3`**: ручные правила, совпадающие с резервными правилами backend. Сервис и backend считают одинаково, поэтому решение
  не зависит от того, кто посчитал. Это совместимый отдельный режим; полное демо работает на `logreg-synthetic-0.1`.

Контракт взят из репозитория backend: [`docs/scoring-contract.md`](https://github.com/GatheredSatyr53/probiinik/blob/main/docs/scoring-contract.md).
Он главный: при расхождении правим этот сервис, а не наоборот.

Как считается оценка: [docs/calculation.md](docs/calculation.md).
Реальные запросы и ответы: [docs/score-examples.md](docs/score-examples.md).
Согласование с резервными C#-правилами backend и результаты сверки: [docs/grade-comparison.md](docs/grade-comparison.md); что внесено в backend: [docs/backend/README.md](docs/backend/README.md).
Подсказка для формы заявки (не решение): [docs/preview.md](docs/preview.md). Сквозные проверки стенда: [docs/stand-checks.md](docs/stand-checks.md).

## Запуск всего продукта

Для кабинета, API, PostgreSQL и ML используй [backend README](https://github.com/GatheredSatyr53/probiinik#быстрый-запуск-полного-продукта) и `scripts/start-demo.ps1` из backend. Кабинет: **http://localhost:3000**, API: **http://localhost:18080/swagger**, ML health: **http://localhost:8000/health**. Одинаковая модель Python/C# — `logreg-synthetic-0.1`. Устанавливать Python или обучающие библиотеки на хост для этого запуска не требуется.

Ниже описан отдельный запуск только сервиса ML.

## Запуск ML

Через Docker:

```bash
docker build -t scoring .
docker run --rm -p 8000:8000 scoring
```

Без Docker (Python 3.12+):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
SCORING_MODEL=rules-0.3 uvicorn app.main:app --port 8000     # ручные правила вместо logreg
docker run --rm -p 8000:8000 -e SCORING_MODEL=rules-0.3 scoring
```

Сервису не нужны sklearn и numpy: logreg считается по JSON-артефакту `models/logreg-synthetic-0.1.json`, который читается при старте.

Данные и обучение (нужен `requirements-train.txt`; повторное обучение не требуется для запуска сервиса):

```bash
pip install -r requirements-train.txt
python -m ml.generate   # синтетика в data/synthetic/ (в репозиторий не кладётся, воспроизводится по seed)
python -m ml.train      # артефакт, метрики, контрольные входы, docs/model-metrics.md
```

Проверка: `curl localhost:8000/health`. Swagger: http://localhost:8000/docs.
Контейнер слушает `0.0.0.0:8000`, имя сервиса в общем compose: `scoring`.

Дымовая проверка запущенного сервиса (в том числе контейнера): `/health`, затем по его `model_version` — пять демо-заявок (rules-0.3)
или 33 контрольных входа (logreg, вместе с PD) в `/api/v1/score`. Нужен только Python, зависимости ставить не нужно.

```bash
python scripts/smoke.py                      # http://localhost:8000
python scripts/smoke.py http://scoring:8000  # любой другой адрес
```

Код выхода 0, если всё совпало с ожидаемым, иначе 1.

Единственная переменная окружения — `SCORING_MODEL` (`logreg-synthetic-0.1` или `rules-0.3`, см. `.env.example`). Неизвестное значение
останавливает запуск. Выбранная версия приходит в поле `model_version` ответа и в `/health`.

## Проверка в общем стенде backend

Логистическая регрессия перенесена в C#, общий артефакт и 33 контрольных случая сверяются с Python. На рабочем демо проверены режимы External и RulesFallback; для уменьшенной суммы используется отдельный `client_reduced`. Подробности: [docs/integration-logreg.md](docs/integration-logreg.md).

После запуска полного демо из backend сквозная проверка выполняется из этого ML-каталога:

```bash
python scripts/e2e_backend.py --api http://localhost:18080 --scoring http://localhost:8000 --mode External --model logreg-synthetic-0.1
```

В PowerShell перед командой можно установить `$env:PYTHONIOENCODING='utf-8'`, чтобы символ ₽ корректно выводился в консоль. Это предотвращает ошибку кодировки при печати отчёта.

Скрипт `scripts/run_stand_checks.sh` и [docs/stand-checks.md](docs/stand-checks.md) сохраняются для **отдельного совместимого стенда rules-0.3**: скрипт явно выбирает старую модель, создаёт свою БД и в конце удаляет только тестовый том. Не выдавай его результат за проверку logreg. `docs/backend/compose.scoring.yml` — исторический пример для раннего backend; актуальный полный Compose находится в backend-репозитории. Рабочую базу для тестов удалять не нужно.

## Тесты

```bash
pip install -r requirements-train.txt
pytest -q
```

Полный набор включает проверки обучения. Если установить только `requirements-dev.txt`, проверки, требующие NumPy/scikit-learn, могут пропускаться.

Тесты проверяют:
- logreg-synthetic-0.1 (`tests/test_model.py`): 33 контрольных входа совпадают с sklearn (PD и логит до 1e-9, балл, грейд, факторы,
  предупреждения) и через API; шкала PD → балл и границы A–E; вклады групп в сумме дают логит; рост риска при просрочках, налогах,
  ФССП, убытке и большой сумме; в артефакте нет скрытых полей генератора и rules-0.3; формат ответа и переключение `SCORING_MODEL`;
  метрики лучше постоянного прогноза, компании в выборках не пересекаются; генератор воспроизводим; переобучение даёт те же коэффициенты
  (два последних пункта — если стоит `requirements-train.txt`, в CI стоит);
- тесты rules-0.3 ниже запускают сервис с `create_app("rules-0.3")`;
- общий эталон `data/conformance/rules-0.3.json` (демо-заявки, пропуски данных, все пороги с обеих сторон): ответ совпадает полностью,
  вместе с текстами факторов; 2 000 случайных запросов дают то же, что копия C# с правками `rules-0.3` (`tests/test_conformance.py`);
- примеры из `data/samples.json` и демо-заявки из `data/demo_companies.json`: балл, грейд, факторы, решение банка, задуманные исходы демо;
- форму ответа по контракту (3–8 факторов без префикса, по убыванию модуля вклада), PD по формуле backend, балл = 50 + сумма вкладов;
- всё, что может прислать backend, принимается, а `null` в обязательных полях и некорректные значения дают `400` (`tests/test_backend_contract.py`);
- копия расчётов backend проходит ассерты из тестов самого backend (`tests/test_backend_reference.py`); данные пересобираются тем же результатом;
- preview не выдаёт балл, грейд и PD, его подсказки совпадают с факторами оценки на тех же данных, меньшая сумма совпадает с формулой
  `DecisionPolicy` (`tests/test_preview.py`);
- `scripts/e2e_backend.py` на фейковом backend: одобрение и меньшая сумма доходят до выдачи, отказ не пускает к договору, скрипт падает
  при неверном режиме, версии `rules-v1` в откате и договоре для отказа (`tests/test_e2e_script.py`).

## Контракт

`POST /api/v1/score`, JSON, `snake_case`, рубли целыми числами, пропуск = `null`. `NaN` и `Infinity` недопустимы.
Сервис читает только нужные поля, остальное игнорирует, поэтому backend может расширять запрос.

Используемые поля запроса:

| Поле | Зачем |
|---|---|
| `request_id` | возвращается в ответе без изменений |
| `application.amount_rub`, `application.term_months` | параметры заявки (обязательны) |
| `application.estimated_monthly_payment_rub` | новый платёж, его считает backend (обязателен) |
| `application.collateral_offered` | признак залога; +10 относится только к rules-0.3 |
| `company.business_age_months` | возраст бизнеса (обязателен) |
| `company.financials.revenue_rub`, `monthly_debt_payments_rub` | выручка за 12 месяцев и текущие платежи за месяц (обязательны) |
| `company.financials.expenses_rub`, `net_profit_rub` | за 12 месяцев, могут быть `null` |
| `checks.credit_bureau`, `checks.fns`, `checks.fssp` | кредитная история, налоги, исполнительные производства; блок может быть `null` |
| `checks.egrul` | данные регистрации; учёт зависит от выбранной модели и её признаков |

Обязательность повторяет типы запроса в backend: что в C# не nullable, здесь обязательно. Правила расчёта: [docs/calculation.md](docs/calculation.md).

Ответ `200`: `request_id`, `model_version` (`logreg-synthetic-0.1` или `rules-0.3`), `score` (0..100), `risk_grade` (A..E), `probability_of_default`,
`factors[]` (`code` без префикса, `title`, `impact`, `value`, `client_explanation`), `warnings[]`, `missing_data[]`.

`probability_of_default` зависит от модели:
- logreg-synthetic-0.1 — исходная PD модели (вероятность `default_12m`), рассчитанная **по модели на синтетике**; балл выводится из неё:
  `clamp(round(100 · (0,32 − PD) / 0,30), 0, 100)`, границы грейдов прежние ([docs/model.md](docs/model.md#5-pd--балл--грейд));
- rules-0.3 — **эвристика** по баллу `0,02 + (100 − score) / 100 × 0,30`, та же формула, что у backend, не измеренная величина.

В обоих случаях это не вероятность одобрения заявки. В интерфейсе клиента её не показываем.

`POST /api/v1/preview` — подсказка для формы до подачи заявки: финансовые факторы, уровень нагрузки (Comfortable / Moderate / Elevated /
High или `InsufficientData`) и «сумму могут снизить до …» по формуле `DecisionPolicy`, без балла, грейда и PD, с признаком
`is_credit_decision: false`. Пустые поля формы не ошибка. Подробно: [docs/preview.md](docs/preview.md).
Подробный пример: [docs/score-examples.md](docs/score-examples.md).

Ошибка: `400` и `{"error": "validation", "details": {"поле": "описание"}}`.
`POST /score` работает как короткий псевдоним того же адреса.

## Данные

- `data/demo_companies.json`: пять демо-заявок на данных seed backend (`SeedData.cs`, `KnownProfiles.cs`, коммит `9f18dd4`):
  те же продукты, суммы и сроки, что в общем демо, возраст бизнеса на 23.10.2026. У каждой `input` (запрос ровно в том виде,
  как его отправляет backend), `expected` (ответ Python), `csharp_fallback` (историческая копия старых правил `rules-v1`, а не текущий logreg fallback)
  и `decision` (решение кредитной политики backend при каждом из ответов). Эти же заявки прогоняет `scripts/smoke.py`.
- `data/conformance/rules-0.3.json`: общий эталон для Python и C# (63 случая), собирает `scripts/conformance.py`.
- `data/conformance/logreg-synthetic-0.1.json`: контрольные входы logreg (33 случая и 17 пар PD → балл), собирает `ml/train.py`.
- `models/`: артефакт logreg и метрики. `data/synthetic/` не хранится: `python -m ml.generate` воспроизводит его по seed (sha256 в артефакте).
- `data/conformance/affordability-bands-rules-0.3.json`: эталон уровней нагрузки для предварительного подбора (backend и `/api/v1/preview`).
- `data/requests/*.json`: те же пять запросов отдельными файлами, готовые для `curl -d @data/requests/demo-good.json`.
- `data/samples.json`: 12 синтетических примеров для тестов, четыре сценария по три, `input` и `expected` отдельно.

В fixtures режима rules-0.3 `expected` посчитан копией C# (`scripts/backend_reference.py`). В эталоне logreg ожидаемые значения формирует sklearn при `ml/train.py`; добавлен отдельный случай `demo-reduced`.
Финансовые цифры `samples.json` синтетические.

Демо-данные собирает `scripts/demo_comparison.py` из копии seed и правил backend (`scripts/backend_reference.py`):

```bash
python scripts/demo_comparison.py build                      # пересобрать после изменений seed или правил backend
python scripts/demo_comparison.py check                      # убедиться, что файлы совпадают с пересборкой
python scripts/demo_comparison.py table http://localhost:8000  # таблица Python vs C# по живому сервису
python scripts/demo_comparison.py examples http://localhost:8000  # пересобрать docs/score-examples.md
```

## Образ в GHCR

Образ публикуется в `ghcr.io/uandrosova07-arch/scoring` с тегами `<sha коммита>`, версией модели по умолчанию (`DEFAULT_MODEL` из
`app/model.py`, сейчас `logreg-synthetic-0.1`) и `latest`. Тег `rules-0.3` остаётся на последнем образе с правилами. Новые образы
по умолчанию отвечают logreg. Полное демо backend собирает закреплённые исходники локально; отдельный совместимый backend Compose использует закреплённый старый образ rules-0.3.
**Пакет должен оставаться публичным**: общий compose backend тянет его без логина. Видимость — настройка самого пакета на GitHub,
из CI её не поменять: Packages → `scoring` → Package settings → Danger Zone → Change visibility → Public (делает владелец репозитория).
Для проверки доступности используйте закреплённый тег нужного режима. Полное демо не зависит от публикации нового образа в GHCR.

В общем compose ставится тег конкретного коммита. Менять правила — только по процессу из [docs/calculation.md](docs/calculation.md#изменение-правил):
новая версия, пересобранные эталоны здесь, копия у backend, зелёные тесты с обеих сторон, новый тег в compose.

## Сборка и деплой

В репозитории настроены проверки и публикация образа в реестр. Передача обновления в отдельный `infra` — опциональный CI-сценарий при наличии соответствующих настроек. Проверяемая локальная поставка запускается через Compose из backend.
Описана в `.github/workflows/ci.yml`:

1. Pull request и push в `main`: установка зависимостей (`requirements-train.txt`, включая sklearn для тестов обучения) и `pytest`.
2. Push в `main`: сборка образа и публикация в GitHub Container Registry
   (`ghcr.io/<владелец>/<репозиторий>:<sha коммита>`, `:<версия модели>` и `:latest`).
3. Если заданы секрет `INFRA_DISPATCH_TOKEN` и переменная `INFRA_REPO`, отправляется `repository_dispatch`
   (`event_type: component-updated`, в `client_payload` компонент `scoring`, образ и тег) в репозиторий `infra`.
   Без них шаг пропускается, образ всё равно публикуется.

Если используется отдельный `infra`, в его `compose.yaml` образ указывается с конкретным тегом, а обновление отдельного сервиса выглядит так:

```bash
docker compose pull scoring
docker compose up -d --no-deps scoring
```

## Текущее состояние и ограничения

- Предварительный подбор согласован: уровни нагрузки 30 / 50 / 70 % и `InsufficientData` без подстановки 10 % выручки. Сервисный `/api/v1/preview` — вспомогательный контракт; frontend обращается к `POST /api/financing/preview` backend.
- Backend использует московскую календарную дату для `needByDate` и возраста бизнеса. Исторические fixtures остаются привязаны к указанной в них дате `as_of`.
- Перенос logreg и резервный расчёт завершены. `client_reduced` сохраняет сценарий 2,4 млн ₽ под 22,5 %, независимо от старого профиля `client_medium`. [Описание интеграции](docs/integration-logreg.md).
- На рабочем API проверены 77 сквозных проверок External и 81 RulesFallback; 26 октября повторно пройдены 77 проверок полного демо из backend на новом томе БД. Это проверки демосценариев, не статистика качества модели.
- Модель обучена на синтетике. Метрики подтверждают конвейер, а не качество на реальных заявках. PD не является вероятностью одобрения.
