# rules-0.3 на стороне backend

> Обученная модель `logreg-synthetic-0.1` и предложение по её переносу в резервный расчёт: [logreg.md](logreg.md).
> Ниже — rules-0.3, по которым backend считает сейчас.

Статус: **внесено в backend** (по данным бэкендера, 24.10.2026): правки `RuleBasedScoringService`, общий эталон
в тестах backend, заглушка scoring в общем compose заменена сервисом. Ниже — что именно было изменено, для справки и для проверки.
Подтверждение в полном процессе: [../stand-checks.md](../stand-checks.md) (`scripts/run_stand_checks.sh`, режим RulesFallback должен
показывать `modelVersion = rules-0.3`). Спецификация: [../calculation.md](../calculation.md), результаты сверки: [../grade-comparison.md](../grade-comparison.md).

## 1. Правки кода

`src/AlfaCredit.Core/Features/Scoring/RuleBasedScoringService.cs` заменить на [RuleBasedScoringService.rules-0.3.cs](RuleBasedScoringService.rules-0.3.cs).
Отличия от `rules-v1` помечены в файле комментарием `rules-0.3`:

| Что | Было (`rules-v1`) | Стало (`rules-0.3`) |
|---|---|---|
| Версия | `rules-v1` | `rules-0.3`, как у сервиса |
| Фактор `expense_ratio` | нет | расходы / выручка > 0,90: −10, иначе 0; нет расходов: фактора нет, предупреждение `expenses_missing` |
| Чистая прибыль не пришла | 10 % выручки | выручка − расходы; 10 % выручки, только если нет и расходов |
| Формула нагрузки | `платежи / (прибыль / 12)` | `платежи * 12 / прибыль`: одно деление, без округления на границах |
| Значения факторов | текущая культура (`0,48` на ru-RU) | инвариантная культура (`0.48`) |
| Сортировка факторов при равном вкладе | `ThenBy(Code)` с культурой | `ThenBy(Code, StringComparer.Ordinal)` |

`DecisionPolicy`, `HttpScoringService`, контракт запроса и ответа без изменений.

## 2. Тесты

1. Скопировать эталон: `data/conformance/rules-0.3.json` → `tests/AlfaCredit.UnitTests/Scoring/conformance-rules-0.3.json`
   и подключить его в `tests/AlfaCredit.UnitTests/AlfaCredit.UnitTests.csproj`:

   ```xml
   <ItemGroup>
     <None Include="Scoring/conformance-rules-0.3.json" CopyToOutputDirectory="PreserveNewest" />
   </ItemGroup>
   ```

2. Добавить [ScoringConformanceTests.cs](ScoringConformanceTests.cs) и заменить `DemoSeedScoringTests.cs` версией из этой папки
   (в `tests/AlfaCredit.UnitTests/Processing/`).
3. В `tests/AlfaCredit.IntegrationTests/Endpoints/ExternalScoringTests.cs` (строка 78) ожидать `RuleBasedScoringService.ModelVersion`
   вместо строки `"rules-v1"`.
4. Запуск без установленного .NET:

   ```bash
   docker run --rm -v "$PWD":/src -w /src mcr.microsoft.com/dotnet/sdk:10.0 dotnet test tests/AlfaCredit.UnitTests
   ```

Остальные тесты backend на эти правки не завязаны: «Ромашка» остаётся 95 / A, «Кузнецов» 55 / C, «Закат» E,
у «Вектора» рекомендация «одобрить».

## 3. Документация backend

- `docs/scoring-contract.md`: коды факторов без префикса (`business_age`, `revenue_to_amount`, `expense_ratio`, `debt_load`,
  `credit_history`, `tax_debt`, `enforcement`, `collateral`); факторов 3–8 (восьмой только при залоге);
  `probability_of_default` сервис возвращает по той же формуле, что backend; обязательные поля (не `null`): `revenue_rub`,
  `monthly_debt_payments_rub`, `business_age_months`, `estimated_monthly_payment_rub`; при откате `model_version = rules-0.3`.
- `docs/decision-logic.md`: в таблицу балльной модели добавить строку «Расходы к выручке» и правило прибыли без `net_profit_rub`.
- Заглушка `scoring/app.py` (`stub-rules-1.0`) заменена сервисом scoring. `compose.scoring.yml` нужен только для старых версий backend.
- `probability_of_default` в карточке менеджера подписывать как «оценка по баллу»: это эвристика `0,02 + (100 − балл) / 100 × 0,30`,
  а не измеренная вероятность и не вероятность одобрения.

## 4. Предварительный подбор (согласовано 24.10.2026)

Backend показывает клиенту уровни нагрузки Comfortable / Moderate / Elevated / High по полосам 30 / 50 / 70 % и `InsufficientData`
с `null` в числовых полях, когда прибыль неизвестна. Сервис scoring отдаёт то же в `POST /api/v1/preview` ([../preview.md](../preview.md)).
Общий эталон уровней: `data/conformance/affordability-bands-rules-0.3.json` — положить рядом с `conformance-rules-0.3.json`
и сверять уровень, `debt_load`, месячную прибыль, платежи и `profitBasis`. Расхождение с оценкой при подаче (там прибыль = 10 % выручки)
ML подтвердил как допустимое.

## 5. Как не разойтись дальше

Договорённость 24.10.2026: предупредить заранее → новая версия → эталоны пересобирает scoring → копия у backend → зелёные тесты
с обеих сторон → новый тег образа в compose (`:<sha>`). Подробно: [../calculation.md](../calculation.md#изменение-правил).
