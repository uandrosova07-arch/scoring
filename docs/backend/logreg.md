# logreg-synthetic-0.1 в backend

Статус: **перенос завершён**. Полное демо использует logreg-synthetic-0.1 для Python и локального расчёта backend. Дальнейшие сведения описывают устройство интеграции; актуальный запуск — в [../integration-logreg.md](../integration-logreg.md). Режим rules-0.3 остаётся отдельным совместимым вариантом.

## Что передаём

| Файл в scoring | Куда в backend | Что это |
|---|---|---|
| `models/logreg-synthetic-0.1.json` | `tests/AlfaCredit.UnitTests/Scoring/` и в выход Core/Api (`CopyToOutputDirectory`) | артефакт: признаки, пропуски, масштабирование, коэффициенты, intercept, шкала PD → балл, объяснения |
| `data/conformance/logreg-synthetic-0.1.json` | `tests/AlfaCredit.UnitTests/Scoring/conformance-logreg-synthetic-0.1.json` | 33 контрольных входа с ожидаемыми ответами, 17 пар PD → балл |
| [LogRegScoringService.logreg-synthetic-0.1.cs](LogRegScoringService.logreg-synthetic-0.1.cs) | `src/AlfaCredit.Core/Features/Scoring/LogRegScoringService.cs` | расчёт по артефакту |
| [LogRegConformanceTests.cs](LogRegConformanceTests.cs) | `tests/AlfaCredit.UnitTests/Processing/` | тест на контрольных входах |

C#-реализация перенесена и проверяется в backend вместе с общим эталоном. Файлы в этой папке — справочные копии. Для подключения также нужны выбор модели, копирование артефакта в output/publish и startup/readiness-проверки из backend-интеграции. Актуальный порядок: [../integration-logreg.md](../integration-logreg.md).

## Расчёт

1. Признаки из `ScoringRequest` по `features[*].formula`, в порядке `features`. Арифметика в `double`; `ln(1 + x)` — именно так,
   не `log1p`. `null` означает пропуск.
2. Пропуск заменяется `impute_value`. Для признаков из `missing_indicators.order` добавляется столбец `missing:<имя>`: 1 при пропуске, иначе 0.
3. `z[c] = (x[c] − scaling.mean[c]) / scaling.scale[c]` для всех 26 столбцов `columns`.
4. `logit = intercept + Σ coefficients[c] · z[c]`, `pd = 1 / (1 + e^(−logit))`.
5. `score = clamp(floor(100 · (pd_at_score_0 − pd) / (pd_at_score_0 − pd_at_score_100) + 0.5), 0, 100)`, грейд по `grades`
   (те же 80 / 65 / 50 / 35, что `RiskGrades.FromScore`).
6. Объяснения: `impact = clamp(round_away_from_zero(−points_per_logit · Σ вкладов столбцов группы и их индикаторов), −100, 100)`,
   до 7 групп по убыванию |impact|, затем по коду (ordinal).
7. `probability_of_default` = `pd` как есть, без эвристики по баллу.

Проверка `LogRegConformanceTests`: признаки (1e-9), логит и PD (1e-9), балл, грейд, факторы с текстами, `warnings`, `missing_data`.
Если PD не сходится, первым расходится вектор `features`, и по нему видно, какая формула считается иначе.

## Что учитывает интеграция

- **Новые коды факторов** `revenue_size` и `loan_term`. Если где-то есть список известных кодов (подписи в карточке менеджера,
  причины решения `factor_<code>` в `DecisionPolicy`), их нужно добавить.
- **PD в карточке менеджера.** Для logreg это выход модели, откалиброванный только на синтетике. Для rules-0.3 подпись остаётся
  «оценка по баллу».
- **Решения в демо.** У Кузнецова A вместо C: одобрение 3 млн под 19,5 % вместо 2,4 млн под 22,5 %. Для сценария меньшей суммы добавлен отдельный `client_reduced`: 3 млн ₽ → 2,4 млн ₽ под 22,5 %. Остальные демо-заявки получают то же решение. Подробно: [../model.md](../model.md#9-демо-заявки-rules-03-и-logreg-synthetic-01).
- **Контракт** не меняется: те же поля запроса и ответа, `model_version = logreg-synthetic-0.1`.

## Применённые изменения

В backend внесены выбор модели, копирование артефакта в publish, startup/readiness-проверки и общий резервный расчёт. Скрипт e2e принимает `--model logreg-synthetic-0.1` и использует отдельный профиль client_reduced. Полное демо задаёт одну модель обеим сторонам. Повторно выполнять исторические шаги переноса не нужно.
