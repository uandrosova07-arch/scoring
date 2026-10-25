// logreg-synthetic-0.1 для резервного расчёта backend: тот же расчёт, что app/model.py в сервисе scoring, по тому же JSON-артефакту
// (models/logreg-synthetic-0.1.json → копия в backend). Набросок для переноса: в окружении ML нет .NET, файл НЕ компилировался.
// Проверка — LogRegConformanceTests.cs на контрольных входах data/conformance/logreg-synthetic-0.1.json.
//
// Типы запроса и ответа взяты из RuleBasedScoringService (rules-0.3): ScoringRequest, ScoringOutcome, ScoringFactor, RiskGrades.
// Вся арифметика в double (как в Python), decimal только на входе и в ScoringOutcome.ProbabilityOfDefault.

using System.Globalization;
using System.Text.Json;
using AlfaCredit.Core.Abstractions;
using AlfaCredit.Core.Domain.Scoring;

namespace AlfaCredit.Core.Features.Scoring;

public sealed class LogRegScoringService : IScoringService
{
    public const string ModelVersion = "logreg-synthetic-0.1";
    private const int MaxFactors = 7;
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
    private static readonly NumberFormatInfo Rub = new() { NumberGroupSeparator = " ", NumberDecimalSeparator = "." };

    // Порядок признаков = порядок столбцов модели. Должен совпадать с artifact.features[*].name (проверяется в Load).
    private static readonly string[] FeatureNames =
    {
        "log_revenue", "expense_ratio", "profit_margin", "debt_load", "amount_to_revenue", "log_amount", "term_months",
        "log_business_age", "overdue_months", "write_offs", "no_credit_history", "log_tax_debt", "blocked_accounts",
        "enforcement_count", "log_enforcement_amount", "collateral",
    };

    private static readonly Dictionary<string, (string Positive, string Negative)> Texts = new(StringComparer.Ordinal)
    {
        ["revenue_size"] = ("Крупная выручка снижает расчётный риск.", "Небольшая выручка повышает расчётный риск."),
        ["revenue_to_amount"] = ("Сумма кредита умеренная относительно выручки и масштаба бизнеса.", "Сумма кредита велика относительно выручки."),
        ["loan_term"] = ("Срок кредита снижает расчётный риск.", "Срок кредита повышает расчётный риск."),
        ["expense_ratio"] = ("Доля расходов в выручке снижает расчётный риск.", "Расходы занимают большую долю выручки, это повышает риск."),
        ["debt_load"] = ("Платежи по кредитам невелики относительно прибыли.", "Платежи по кредитам велики относительно прибыли или прибыли нет."),
        ["business_age"] = ("Бизнес работает давно, это снижает риск.", "Бизнес молодой, это повышает риск."),
        ["credit_history"] = ("Кредитная история без серьёзных просрочек снижает риск.", "Просрочки или списания в кредитной истории повышают риск."),
        ["tax_debt"] = ("Налоговой задолженности и блокировок нет.", "Налоговая задолженность или блокировка счетов повышают риск."),
        ["enforcement"] = ("Исполнительных производств нет.", "Исполнительные производства повышают риск."),
        ["collateral"] = ("Предложенный залог снижает риск.", "Без залога риск выше, чем с залогом."),
    };
    private const string Neutral = "Почти не влияет на оценку.";

    private static readonly Lazy<Artifact> Model = new(() =>
        Artifact.Load(Path.Combine(AppContext.BaseDirectory, "Scoring", $"{ModelVersion}.json")));

    public Task<ScoringOutcome> ScoreAsync(ScoringRequest request, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(Score(request));
    }

    public static ScoringOutcome Score(ScoringRequest request) => Score(request, Model.Value);

    public static ScoringOutcome Score(ScoringRequest request, Artifact art)
    {
        var p = Predict(request, art);
        var factors = new List<ScoringFactor>();
        foreach (var (code, group) in art.Groups)
        {
            var impact = Math.Clamp(RoundHalfAwayFromZero(-art.PointsPerLogit * p.GroupContributions[code]), -100, 100);
            var (positive, negative) = Texts[code];
            factors.Add(new ScoringFactor(code, group.Title, impact, Describe(code, request),
                impact > 0 ? positive : impact < 0 ? negative : Neutral));
        }

        var ordered = factors.OrderByDescending(f => Math.Abs(f.Impact)).ThenBy(f => f.Code, StringComparer.Ordinal).Take(MaxFactors).ToArray();
        return new ScoringOutcome(p.Score, RiskGrades.FromScore(p.Score), (decimal)p.Pd, ordered, Warnings(request),
            request.MissingInputs(), ModelVersion, ScoringSource.Rules);
    }

    public sealed record Prediction(double Logit, double Pd, int Score, IReadOnlyDictionary<string, double> GroupContributions);

    public static Prediction Predict(ScoringRequest request, Artifact art)
    {
        var raw = RawFeatures(request);
        var values = new Dictionary<string, double>(StringComparer.Ordinal);
        for (var i = 0; i < FeatureNames.Length; i++)
        {
            values[FeatureNames[i]] = raw[i] ?? art.ImputeValue[FeatureNames[i]];   // пропуск = медиана train
        }

        foreach (var name in art.MissingIndicatorOrder)
        {
            values[$"missing:{name}"] = raw[Array.IndexOf(FeatureNames, name)] is null ? 1.0 : 0.0;
        }

        var contrib = new Dictionary<string, double>(StringComparer.Ordinal);
        var logit = art.Intercept;
        foreach (var c in art.Columns)
        {
            var z = (values[c] - art.Mean[c]) / art.Scale[c];
            contrib[c] = art.Coefficients[c] * z;
            logit += contrib[c];
        }

        var pd = Sigmoid(logit);
        var groups = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var (code, group) in art.Groups)
        {
            var sum = 0.0;
            foreach (var f in group.Features)
            {
                sum += contrib[f];
                if (contrib.TryGetValue($"missing:{f}", out var m))
                {
                    sum += m;
                }
            }

            groups[code] = sum;
        }

        return new Prediction(logit, pd, ScoreFromPd(pd, art), groups);
    }

    // score = clamp(floor(100 * (pd_at_score_0 − pd) / (pd_at_score_0 − pd_at_score_100) + 0.5), 0, 100); тот же порядок операций, что в Python
    public static int ScoreFromPd(double pd, Artifact art)
    {
        var s = Math.Floor(100 * (art.PdAtScore0 - pd) / (art.PdAtScore0 - art.PdAtScore100) + 0.5);
        return (int)Math.Clamp(s, 0, 100);
    }

    private static double Sigmoid(double x)
    {
        if (x >= 0)
        {
            return 1 / (1 + Math.Exp(-x));
        }

        var e = Math.Exp(x);
        return e / (1 + e);
    }

    private static int RoundHalfAwayFromZero(double x) => (int)Math.Round(x, MidpointRounding.AwayFromZero);

    private static double Clip(double x, double lo, double hi) => x < lo ? lo : x > hi ? hi : x;

    // Формулы — artifact.features[*].formula. Везде ln(1 + x), а не log1p: так считает обучение.
    public static double?[] RawFeatures(ScoringRequest request)
    {
        var app = request.Application;
        var fin = request.Company.Financials;
        var checks = request.Checks;
        var revenue = (double)fin.RevenueRub;
        double? expenses = fin.ExpensesRub is { } e ? (double)e : null;
        double? net = fin.NetProfitRub is { } n ? (double)n : null;
        var payments = (double)fin.MonthlyDebtPaymentsRub + (double)app.EstimatedMonthlyPaymentRub;
        var amount = (double)app.AmountRub;

        double? debtLoad = net is null ? null : net <= 0 ? 5.0 : Clip(payments * 12 / net.Value, 0, 5);
        var bureau = checks.CreditBureau;
        var fns = checks.Fns;
        var fssp = checks.Fssp;

        return new double?[]
        {
            Math.Log(1 + revenue),
            expenses is null || revenue == 0 ? null : Clip(expenses.Value / revenue, 0, 2),
            net is null || revenue == 0 ? null : Clip(net.Value / revenue, -1, 1),
            debtLoad,
            revenue == 0 ? 5.0 : Clip(amount / revenue, 0, 5),
            Math.Log(amount),
            app.TermMonths,
            Math.Log(1 + (double)request.Company.BusinessAgeMonths),
            bureau is null ? null : Math.Min((double)bureau.MaxOverdueDays24M, 360.0) / 30,
            bureau is null ? null : bureau.HasWriteOffs ? 1.0 : 0.0,
            bureau is null ? null : bureau.CreditHistoryMonths == 0 && bureau.ActiveLoans == 0 && bureau.ClosedLoans == 0 ? 1.0 : 0.0,
            fns is null ? null : Math.Log(1 + Math.Max((double)fns.TaxDebtRub, 0)),
            fns is null ? null : fns.HasBlockedAccounts ? 1.0 : 0.0,
            fssp is null ? null : Math.Min(fssp.OpenProceedings, 10),
            fssp is null ? null : Math.Log(1 + (double)fssp.OpenAmountRub),
            app.CollateralOffered ? 1.0 : 0.0,
        };
    }

    private static List<string> Warnings(ScoringRequest request)
    {
        var fin = request.Company.Financials;
        var checks = request.Checks;
        var w = new List<string>();
        if (fin.ExpensesRub is null) w.Add("expenses_missing");
        if (fin.NetProfitRub is null) w.Add("net_profit_missing");
        if (checks.CreditBureau is null) w.Add("credit_bureau_missing");
        if (checks.Fns is null) w.Add("fns_missing");
        if (checks.Fssp is null) w.Add("fssp_missing");
        return w;
    }

    // Значение для карточки менеджера: исходные данные заявки (app/features.py, describe). Числа в инвариантной культуре.
    private static string Describe(string code, ScoringRequest request)
    {
        var app = request.Application;
        var fin = request.Company.Financials;
        var checks = request.Checks;
        var rev = (double)fin.RevenueRub;
        switch (code)
        {
            case "revenue_size":
                return (rev / 1e6).ToString("0.0", Inv) + " млн ₽ в год";
            case "revenue_to_amount":
                return rev == 0 ? "выручки нет" : "сумма " + ((double)app.AmountRub / rev).ToString("0.00", Inv) + " годовой выручки";
            case "loan_term":
                return $"{app.TermMonths} мес.";
            case "expense_ratio":
                return fin.ExpensesRub is not { } ex || rev == 0 ? "нет данных" : ((double)ex / rev).ToString("0.00", Inv);
            case "debt_load":
                if (fin.NetProfitRub is not { } net) return "нет данных о прибыли";
                if (net <= 0) return "прибыли нет";
                var pay = (double)fin.MonthlyDebtPaymentsRub + (double)app.EstimatedMonthlyPaymentRub;
                return (pay * 12 / (double)net).ToString("0.00", Inv);
            case "business_age":
                return $"{request.Company.BusinessAgeMonths} мес.";
            case "credit_history":
                if (checks.CreditBureau is not { } b) return "нет ответа БКИ";
                if (b.HasWriteOffs) return "есть списания";
                if (b.CreditHistoryMonths == 0 && b.ActiveLoans == 0 && b.ClosedLoans == 0) return "нет истории";
                return b.MaxOverdueDays24M == 0 ? "без просрочек" : $"просрочка до {b.MaxOverdueDays24M} дн.";
            case "tax_debt":
                if (checks.Fns is not { } f) return "нет ответа ФНС";
                var debt = f.TaxDebtRub <= 0 ? "нет долга" : "долг " + ((double)f.TaxDebtRub).ToString("#,0", Rub) + " ₽";
                return debt + (f.HasBlockedAccounts ? ", счета заблокированы" : "");
            case "enforcement":
                if (checks.Fssp is not { } s) return "нет ответа ФССП";
                return s.OpenProceedings == 0 ? "нет" : $"{s.OpenProceedings} на " + ((double)s.OpenAmountRub).ToString("#,0", Rub) + " ₽";
            case "collateral":
                return app.CollateralOffered ? "предложено" : "нет";
            default:
                throw new KeyNotFoundException(code);
        }
    }

    public sealed record Group(string Title, string[] Features);

    public sealed class Artifact
    {
        public required string[] Columns { get; init; }
        public required string[] MissingIndicatorOrder { get; init; }
        public required Dictionary<string, double> ImputeValue { get; init; }
        public required Dictionary<string, double> Mean { get; init; }
        public required Dictionary<string, double> Scale { get; init; }
        public required Dictionary<string, double> Coefficients { get; init; }
        public required double Intercept { get; init; }
        public required double PdAtScore100 { get; init; }
        public required double PdAtScore0 { get; init; }
        public required double PointsPerLogit { get; init; }
        public required List<(string Code, Group Group)> Groups { get; init; }   // порядок как в JSON

        public static Artifact Load(string path)
        {
            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            var r = doc.RootElement;
            if (r.GetProperty("model_version").GetString() != ModelVersion)
            {
                throw new InvalidOperationException($"{path}: другая версия модели.");
            }

            var features = r.GetProperty("features").EnumerateArray().ToArray();
            if (!features.Select(f => f.GetProperty("name").GetString()).SequenceEqual(FeatureNames))
            {
                throw new InvalidOperationException($"{path}: порядок признаков не совпадает с кодом.");
            }

            static Dictionary<string, double> Map(JsonElement e) =>
                e.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetDouble(), StringComparer.Ordinal);

            var explanation = r.GetProperty("explanation");
            return new Artifact
            {
                Columns = r.GetProperty("columns").EnumerateArray().Select(c => c.GetString()!).ToArray(),
                MissingIndicatorOrder = r.GetProperty("missing_indicators").GetProperty("order").EnumerateArray().Select(c => c.GetString()!).ToArray(),
                ImputeValue = features.ToDictionary(f => f.GetProperty("name").GetString()!, f => f.GetProperty("impute_value").GetDouble(), StringComparer.Ordinal),
                Mean = Map(r.GetProperty("scaling").GetProperty("mean")),
                Scale = Map(r.GetProperty("scaling").GetProperty("scale")),
                Coefficients = Map(r.GetProperty("coefficients")),
                Intercept = r.GetProperty("intercept").GetDouble(),
                PdAtScore100 = r.GetProperty("score").GetProperty("pd_at_score_100").GetDouble(),
                PdAtScore0 = r.GetProperty("score").GetProperty("pd_at_score_0").GetDouble(),
                PointsPerLogit = explanation.GetProperty("points_per_logit").GetDouble(),
                Groups = explanation.GetProperty("groups").EnumerateObject()
                    .Select(g => (g.Name, new Group(g.Value.GetProperty("title").GetString()!,
                        g.Value.GetProperty("features").EnumerateArray().Select(f => f.GetString()!).ToArray())))
                    .ToList(),
            };
        }
    }
}
