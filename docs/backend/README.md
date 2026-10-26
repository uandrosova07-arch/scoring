# Интеграция скоринга с backend

В полном демо используется **logreg-synthetic-0.1**, перенесённая в C#. Python и локальный резервный расчёт загружают одинаковый JSON. Источник fallback называется `RulesFallback` для совместимости API; это не переключение logreg на старые правила.

Актуальная инструкция: [../integration-logreg.md](../integration-logreg.md). Запуск всего продукта — в [главном README](https://github.com/NaOpite/alfacredit#запуск). При запуске не нужно повторно копировать справочные файлы из этой папки в backend.

## Файлы logreg

- [LogRegScoringService.logreg-synthetic-0.1.cs](LogRegScoringService.logreg-synthetic-0.1.cs) — справочная C#-реализация. Рабочая версия живёт в backend.
- [LogRegConformanceTests.cs](LogRegConformanceTests.cs) — справочный тест. Общий эталон содержит 33 случая и 17 пар PD → балл.
- [logreg.md](logreg.md) — устройство и состояние переноса.

Артефакт в ML: `models/logreg-synthetic-0.1.json`; в backend: `src/AlfaCredit.Core/Scoring/logreg-synthetic-0.1.json`. SHA256: `4783344256642d0da396a3312a134311f57e6a131ea72770cbde78c4baf18777`.

## Режим совместимости rules-0.3

Правила перенесены 24 октября и остаются отдельным явным выбором модели. Справочные копии: [RuleBasedScoringService.rules-0.3.cs](RuleBasedScoringService.rules-0.3.cs), [ScoringConformanceTests.cs](ScoringConformanceTests.cs), [DemoSeedScoringTests.cs](DemoSeedScoringTests.cs).

Для rules-0.3 общий эталон содержит 63 случая: `data/conformance/rules-0.3.json`. [Сверка](../grade-comparison.md) и [расчёт](../calculation.md). Исторический `compose.scoring.yml` в этой папке не нужен для запуска текущего полного демо.

## Что проверять при изменении модели

Обновлять версию и эталон согласованно в ML и backend, проверять совпадение артефакта, PD/балла/грейда и факторов, а затем полный процесс External и RulesFallback. Результаты logreg нельзя подменять результатами shell-стенда `run_stand_checks.sh`, который явно выбирает rules-0.3.

Клиенту показываются решение и причины; внутренние баллы, грейды A–E и PD доступны сотруднику через защищённый API. Внешние проверки, ответ менеджера, код и выдача — симуляции.
