# Предварительная подсказка (preview) для формы заявки

Статус: **согласовано с бэкендом 24.10.2026**: уровни нагрузки Comfortable / Moderate / Elevated / High по полосам 30 / 50 / 70 %,
`InsufficientData` с `null` в числовых полях, формула меньшей суммы как в `DecisionPolicy`. Сервис scoring отдаёт то же в `POST /api/v1/preview`,
общий эталон уровней: `data/conformance/affordability-bands-rules-0.3.json`.

Preview помогает клиенту, пока он заполняет форму: что повлияет на оценку и может ли банк предложить меньшую сумму.
**Это не кредитное решение.** Решение принимает только backend после подачи заявки: проверки ЕГРЮЛ, ФНС, БКИ, ФССП,
скоринг (`/api/v1/score`) и кредитная политика.

## Чем preview отличается от решения

| | Preview (`/api/v1/preview`) | Скоринг и решение (`/api/v1/score` + `DecisionPolicy`) |
|---|---|---|
| Когда | до подачи, по данным формы | после подачи, по данным компании и проверкам |
| Факторы | только финансовые: возраст, выручка к сумме, расходы, нагрузка | все факторы `rules-0.3` |
| Нагрузка | уровень Comfortable / Moderate / Elevated / High, без баллов | вклад фактора `debt_load` в балл |
| Нет ни прибыли, ни расходов | `InsufficientData`, числа `null`, просьба заполнить | прибыль = 10 % выручки, `net_profit_missing` |
| БКИ, налоги, ФССП | не оцениваются (`not_evaluated`) | оцениваются |
| Балл, грейд, PD | **нет**: без проверок частичный балл выглядел бы как решение | есть |
| Пустые поля | не ошибка: `status = incomplete` и список `missing_data` | `null` в обязательных полях: `400` |
| Сумма | подсказка «могут снизить до …» на случай одобрения с условиями | одобренная сумма в решении |
| Признак в ответе | `kind = "preview"`, `is_credit_decision = false`, `disclaimer` | `decision`, `offer` |

В интерфейсе подсказка показывается отдельно от статуса заявки и не использует слова «одобрено», «отказ», «грейд».

## Запрос

```json
{
  "amount_rub": 3000000,
  "term_months": 24,
  "estimated_monthly_payment_rub": null,
  "product": { "code": "working-capital", "min_amount_rub": 300000, "base_rate_percent": 19.5 },
  "company": {
    "business_age_months": 40,
    "financials": { "revenue_rub": 9600000, "expenses_rub": 4200000, "net_profit_rub": 4800000, "monthly_debt_payments_rub": 40000 }
  }
}
```

Обязательны только `amount_rub` (> 0) и `term_months` (1..600). Всё остальное может отсутствовать или быть `null`.
Новый платёж берётся из `estimated_monthly_payment_rub`, а если его нет, считается по `product.base_rate_percent`
так же, как `LoanMath.MonthlyPayment` в backend (аннуитет, затем до рубля). Некорректные значения (строка вместо числа,
отрицательная выручка) дают `400` в формате контракта.

## Ответ

```json
{
  "kind": "preview",
  "is_credit_decision": false,
  "model_version": "rules-0.3",
  "status": "ready",
  "hints": [
    {
      "code": "business_age",
      "title": "Возраст бизнеса",
      "impact": 10,
      "value": "3 г. 4 мес.",
      "client_explanation": "Компания работает больше трёх лет, это снижает риск."
    },
    {
      "code": "revenue_to_amount",
      "title": "Выручка к сумме кредита",
      "impact": 5,
      "value": "3.2x",
      "client_explanation": "Запрошенная сумма небольшая относительно годовой выручки."
    },
    {
      "code": "debt_load",
      "title": "Долговая нагрузка",
      "impact": 0,
      "value": "0.48",
      "client_explanation": "Платежи по кредитам занимают около половины месячной прибыли."
    },
    {
      "code": "expense_ratio",
      "title": "Расходы к выручке",
      "impact": 0,
      "value": "0.44",
      "client_explanation": "Расходы занимают обычную долю выручки."
    }
  ],
  "affordability": {
    "level": "Moderate",
    "debt_load": 0.4799,
    "monthly_profit_rub": 400000.0,
    "monthly_payments_rub": 191956.0,
    "profit_basis": "net_profit",
    "explanation": "Платежи по кредитам с учётом нового займа занимают заметную часть прибыли."
  },
  "not_evaluated": [
    "credit_history",
    "tax_debt",
    "enforcement"
  ],
  "amount_hint": {
    "requested_amount_rub": 3000000,
    "reduced_amount_rub": 2400000,
    "would_be_reduced": true,
    "share_of_revenue": 0.25,
    "explanation": "Если банк одобрит заявку с изменёнными условиями, сумму могут снизить до 2 400 000 ₽: это 25 % годовой выручки (не ниже минимума продукта)."
  },
  "missing_data": [],
  "warnings": [],
  "disclaimer": "Предварительная подсказка по введённым данным, а не решение банка. ..."
}
```

`hints` считаются теми же функциями, что факторы в `/api/v1/score`: на тех же данных вклады, значения и тексты совпадают
(проверено на всех демо-заявках, `tests/test_preview.py`). `impact` можно не показывать клиенту; это подсказка, а не баллы решения.

## Уровни нагрузки

Уровень — это полоса того же отношения, что фактор `debt_load` в `rules-0.3`, без баллов:

```
debt_load = (текущие платежи по кредитам + новый платёж) × 12 / годовая прибыль
```

| Уровень | Нагрузка | Вклад в оценке (клиенту не показывается) |
|---|---|---|
| `Comfortable` | < 0,3 | +10 |
| `Moderate` | ≥ 0,3 и < 0,5 | 0 |
| `Elevated` | ≥ 0,5 и < 0,7 | −10 |
| `High` | ≥ 0,7, или прибыль ≤ 0 (`debt_load = null`) | −25 |
| `InsufficientData` | прибыль неизвестна или нет платежей | — |

Нижняя граница включается: ровно 0,3 — уже `Moderate`, ровно 0,7 — уже `High`. Прибыль: `net_profit_rub`, иначе выручка − расходы
(`profit_basis = revenue_minus_expenses`). Полосы в коде заданы один раз (`DEBT_LOAD_BANDS` в `app/rules.py`) и общие для оценки и preview.
Эталон для обеих сторон — `data/conformance/affordability-bands-rules-0.3.json` (63 случая: все границы, прибыль 0 и отрицательная, пропуски).

## Поведение при отсутствующих финансовых данных

| Нет в форме | Что в ответе |
|---|---|
| ничего, кроме суммы и срока | `status = incomplete`, `hints = []`, `affordability.level = InsufficientData`, `amount_hint = null`, в `missing_data` все поля |
| `business_age_months` | нет подсказки по возрасту |
| `revenue_rub` | нет подсказок «выручка к сумме» и «расходы», нет `amount_hint`; нагрузка только при указанной чистой прибыли |
| `expenses_rub` | нет подсказки по расходам, `warnings: expenses_missing` |
| `net_profit_rub`, расходы есть | прибыль = выручка − расходы, `profit_basis = revenue_minus_expenses`, `warnings: net_profit_missing` |
| `net_profit_rub` и `expenses_rub` | `InsufficientData`: `debt_load`, `monthly_profit_rub`, `monthly_payments_rub`, `profit_basis` = `null`, подсказки по нагрузке нет |
| `monthly_debt_payments_rub` или платёж и ставка | `InsufficientData` |

### Расхождение с оценкой при подаче (подтверждено ML)

Если в заявке так и не будет ни прибыли, ни расходов, скоринг считает нагрузку по прибыли = 10 % выручки и пишет
`expenses_missing`, `net_profit_missing` (так записано в `rules-0.3`, одинаково в Python и C#). Preview в этом случае уровень не показывает.
**ML это расхождение устраивает**: preview не должен показывать клиенту оценку, построенную на допущении, а оценка при подаче должна
быть всегда. Условие — текст `InsufficientData` честно говорит, что будет при подаче («банк примет прибыль равной 10 % выручки»),
чтобы клиент мог заполнить прибыль или расходы. Это расхождение закреплено тестом
(`test_no_profit_and_no_expenses_give_insufficient_data_not_ten_percent`); при любых известных данных уровень preview совпадает
с вкладом `debt_load` в оценке (`test_level_matches_debt_load_impact_on_all_shared_cases`).

## Формула меньшей суммы

Та же, что в `DecisionPolicy` при одобрении с изменёнными условиями (грейд C):

```
reduced_amount = max( min(запрошенная сумма, Math.Round(годовая выручка × 0.25, 0)), минимальная сумма продукта )
```

`Math.Round` в C# по умолчанию банковский (половина к чётному), preview округляет так же: при выручке 10 ₽ предел 2 ₽, при 14 ₽ — 4 ₽.
Совпадение с политикой проверено на 500 случайных выручках, суммах и минимумах продукта и на заявке «Кузнецова»: подсказка 2 400 000 ₽,
решение банка 2 400 000 ₽. `would_be_reduced = false` значит, что при одобрении с условиями сумма не изменится.

Подсказка не обещает, что сумма будет снижена или одобрена: при грейде A или B банк одобряет запрошенную сумму, при D заявку смотрит
менеджер (демо-менеджер ограничивает 50 % выручки), при E отказ. Поэтому текст всегда условный: «если банк одобрит с изменёнными условиями».

## Что согласовано с бэкендом

1. Backend показывает предварительный подбор сам (`affordability`: уровень, `monthlyProfit` и `profitBasis` nullable, `notCompared`,
   `suggestedAmount`, `calculationDate`; типы TS в документе backend). Уровни и `InsufficientData` совпадают с `/api/v1/preview`
   этого сервиса; сверка по общему эталону уровней.
2. Полосы 30 / 50 / 70 % — это пороги `rules-0.3`. У backend они два параметра конфига и эталон; менять только по процессу
   изменения правил ([calculation.md](calculation.md#изменение-правил)): новая версия, оба эталона пересобраны, зелёные тесты с обеих сторон.
3. Доля 25 % для меньшей суммы — `DecisionPolicyOptions.MaxAmountShareOfRevenue`; меняется так же.
4. Пригодность продукта (минимальный возраст бизнеса, границы суммы и срока) проверяет backend при подаче, preview её не дублирует.
