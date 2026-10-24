// Предлагаемая версия src/AlfaCredit.Core/Features/Scoring/RuleBasedScoringService.cs для rules-0.3.
// Отличия от rules-v1 (коммит 9f18dd4) отмечены «rules-0.3». Всё остальное: факторы, пороги, баллы, тексты, порядок
// предупреждений и сортировка не менялись. Сервис scoring считает ровно так же; общий эталон:
// data/conformance/rules-0.3.json и тест ScoringConformanceTests.cs.

using System.Globalization;
using AlfaCredit.Core.Abstractions;
using AlfaCredit.Core.Common;
using AlfaCredit.Core.Domain.Scoring;

namespace AlfaCredit.Core.Features.Scoring;

/// <summary>
/// Встроенная балльная модель. Явная и объяснимая: каждому фактору соответствует вклад в баллы и фраза для клиента.
/// Описана в docs/decision-logic.md. Используется сама по себе или как запасной вариант при недоступности внешнего сервиса.
/// Те же правила (rules-0.3) реализует сервис scoring, поэтому результат не зависит от того, кто посчитал.
/// </summary>
public sealed class RuleBasedScoringService : IScoringService
{
    // rules-0.3: общая версия с сервисом scoring вместо rules-v1
    public const string ModelVersion = "rules-0.3";
    public const int BaseScore = 50;

    public Task<ScoringOutcome> ScoreAsync(ScoringRequest request, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(request);
        return Task.FromResult(Score(request));
    }

    public static ScoringOutcome Score(ScoringRequest request)
    {
        var factors = new List<ScoringFactor>();
        var warnings = new List<string>();
        var company = request.Company;
        var financials = company.Financials;
        var checks = request.Checks;

        // 1. Возраст бизнеса
        var age = company.BusinessAgeMonths;
        var ageImpact = age switch { < 6 => -20, < 12 => -10, < 36 => 0, _ => 10 };
        factors.Add(new ScoringFactor(
            "business_age",
            "Возраст бизнеса",
            ageImpact,
            FormatMonths(age),
            ageImpact >= 10 ? "Компания работает больше трёх лет, это снижает риск."
            : ageImpact == 0 ? "Компания работает от года до трёх лет."
            : "Бизнес молодой, банк относится к таким заявкам осторожнее."));

        // 2. Выручка к запрошенной сумме
        var ratio = request.Application.AmountRub <= 0 ? 0m : financials.RevenueRub / request.Application.AmountRub;
        var ratioImpact = ratio switch { >= 4 => 15, >= 2 => 5, >= 1 => -5, _ => -20 };
        factors.Add(new ScoringFactor(
            "revenue_to_amount",
            "Выручка к сумме кредита",
            ratioImpact,
            Invariant($"{ratio:0.0}x"), // rules-0.3: значения в инвариантной культуре, как в сервисе scoring
            ratioImpact > 0 ? "Запрошенная сумма небольшая относительно годовой выручки."
            : "Запрошенная сумма велика относительно годовой выручки."));

        // 3. rules-0.3: расходы к выручке. Только штраф: прибыль, а значит и нагрузка, уже учитывает расходы.
        if (financials.ExpensesRub is { } expenses)
        {
            if (financials.RevenueRub > 0)
            {
                var expenseRatio = expenses / financials.RevenueRub;
                var expenseImpact = expenseRatio > 0.9m ? -10 : 0;
                factors.Add(new ScoringFactor(
                    "expense_ratio",
                    "Расходы к выручке",
                    expenseImpact,
                    Invariant($"{expenseRatio:0.00}"),
                    expenseImpact < 0 ? "Расходы забирают почти всю выручку, запас прочности небольшой."
                    : "Расходы занимают обычную долю выручки."));
            }
        }
        else
        {
            warnings.Add("expenses_missing");
        }

        // 4. Долговая нагрузка: платежи по кредитам к месячной прибыли
        decimal annualProfit;
        if (financials.NetProfitRub is { } netProfit)
        {
            annualProfit = netProfit;
        }
        else
        {
            // rules-0.3: если расходы известны, прибыль = выручка − расходы; 10 % выручки только когда нет и расходов
            annualProfit = financials.ExpensesRub is { } knownExpenses ? financials.RevenueRub - knownExpenses : financials.RevenueRub * 0.10m;
            warnings.Add("net_profit_missing");
        }

        var totalPayments = financials.MonthlyDebtPaymentsRub + request.Application.EstimatedMonthlyPaymentRub;
        // rules-0.3: одно деление (платежи * 12 / годовая прибыль) вместо деления на месячную прибыль: на границах
        // 0.3, 0.5 и 0.7 результат точный и не зависит от округления 28-го знака decimal
        var load = annualProfit <= 0 ? 9.99m : totalPayments * 12m / annualProfit;
        var loadImpact = load switch { < 0.3m => 10, < 0.5m => 0, < 0.7m => -10, _ => -25 };
        factors.Add(new ScoringFactor(
            "debt_load",
            "Долговая нагрузка",
            loadImpact,
            load >= 9.99m ? "прибыль отсутствует" : Invariant($"{load:0.00}"),
            loadImpact > 0 ? "Платежи по кредитам занимают небольшую часть прибыли."
            : loadImpact == 0 ? "Платежи по кредитам занимают около половины месячной прибыли."
            : "Платежи по кредитам с учётом нового займа занимают слишком большую часть прибыли."));

        // 5. Кредитная история
        if (checks.CreditBureau is { } bureau)
        {
            int historyImpact;
            string historyValue;
            string historyExplanation;
            if (bureau.CreditHistoryMonths == 0 && bureau.ActiveLoans == 0 && bureau.ClosedLoans == 0)
            {
                historyImpact = 0;
                historyValue = "нет истории";
                historyExplanation = "Кредитной истории пока нет, это не плюс и не минус.";
            }
            else if (bureau.HasWriteOffs || bureau.MaxOverdueDays24M > 90)
            {
                historyImpact = -30;
                historyValue = $"просрочка {bureau.MaxOverdueDays24M} дн.";
                historyExplanation = "В кредитной истории есть длительные просрочки или списания.";
            }
            else
            {
                historyImpact = bureau.MaxOverdueDays24M switch { 0 => 15, <= 30 => -5, _ => -15 };
                historyValue = bureau.MaxOverdueDays24M == 0 ? "без просрочек" : $"просрочка до {bureau.MaxOverdueDays24M} дн.";
                historyExplanation = historyImpact > 0
                    ? "Просрочек по кредитам за два года нет."
                    : "За последние два года были просрочки по кредитам.";
            }

            factors.Add(new ScoringFactor("credit_history", "Кредитная история", historyImpact, historyValue, historyExplanation));
        }
        else
        {
            warnings.Add("credit_bureau_missing");
        }

        // 6. Налоговая задолженность и блокировки
        if (checks.Fns is { } fns)
        {
            var taxImpact = fns.TaxDebtRub switch { <= 0 => 5, < 50_000 => -5, _ => -15 };
            if (fns.HasBlockedAccounts)
            {
                taxImpact -= 15;
            }

            factors.Add(new ScoringFactor(
                "tax_debt",
                "Налоговая задолженность",
                taxImpact,
                fns.TaxDebtRub <= 0 ? "нет" : Money.FormatRub(fns.TaxDebtRub),
                taxImpact > 0 ? "Задолженности перед налоговой нет."
                : fns.HasBlockedAccounts ? "Есть налоговая задолженность и заблокированные счета."
                : "Есть задолженность перед налоговой."));
        }
        else
        {
            warnings.Add("fns_missing");
        }

        // 7. Исполнительные производства
        if (checks.Fssp is { } fssp)
        {
            var fsspImpact = fssp.OpenProceedings > 0 ? -10 : 0;
            factors.Add(new ScoringFactor(
                "enforcement",
                "Исполнительные производства",
                fsspImpact,
                fssp.OpenProceedings == 0 ? "нет" : $"{fssp.OpenProceedings} на {Money.FormatRub(fssp.OpenAmountRub)}",
                fsspImpact == 0 ? "Открытых исполнительных производств нет." : "Есть открытые исполнительные производства."));
        }
        else
        {
            warnings.Add("fssp_missing");
        }

        // 8. Залог
        if (request.Application.CollateralOffered)
        {
            factors.Add(new ScoringFactor("collateral", "Обеспечение", 10, "предложено", "Предложенный залог снижает риск банка."));
        }

        var score = Math.Clamp(BaseScore + factors.Sum(f => f.Impact), 0, 100);
        var grade = RiskGrades.FromScore(score);
        var probabilityOfDefault = Math.Round(0.02m + (100 - score) / 100m * 0.30m, 3);

        // rules-0.3: порядковое сравнение кодов, как в Python (на текущих кодах совпадает с прежним, но не зависит от культуры)
        var ordered = factors.OrderByDescending(f => Math.Abs(f.Impact)).ThenBy(f => f.Code, StringComparer.Ordinal).ToArray();
        return new ScoringOutcome(score, grade, probabilityOfDefault, ordered, warnings, request.MissingInputs(), ModelVersion, ScoringSource.Rules);
    }

    private static string Invariant(FormattableString text) => text.ToString(CultureInfo.InvariantCulture);

    private static string FormatMonths(int months)
    {
        var years = months / 12;
        var rest = months % 12;
        return years switch
        {
            0 => $"{rest} мес.",
            _ when rest == 0 => $"{years} г.",
            _ => $"{years} г. {rest} мес.",
        };
    }
}
