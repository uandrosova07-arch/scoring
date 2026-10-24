using System.Text.Json;
using AlfaCredit.Core.Domain.Scoring;
using AlfaCredit.Core.Features.Scoring;
using AlfaCredit.Infrastructure.Scoring;
using Shouldly;

namespace AlfaCredit.UnitTests.Processing;

/// <summary>
/// Общий эталон rules-0.3 с сервисом scoring: те же запросы и ожидаемые ответы проверяет tests/test_conformance.py
/// в репозитории scoring. Если обе стороны проходят его, Python и резервные правила дают одинаковый балл, грейд, PD,
/// факторы с текстами, предупреждения и missing_data, а значит и одинаковое решение кредитной политики.
/// Файл эталона: data/conformance/rules-0.3.json из репозитория scoring, копия в Scoring/conformance-rules-0.3.json рядом с тестом.
/// </summary>
public sealed class ScoringConformanceTests
{
    private static readonly Dictionary<string, JsonElement> Cases = LoadCases();

    public static TheoryData<string> CaseIds()
    {
        var data = new TheoryData<string>();
        foreach (var id in Cases.Keys)
        {
            data.Add(id);
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(CaseIds))]
    public void Fallback_rules_match_shared_case(string id)
    {
        var item = Cases[id];
        // Запрос разбирается теми же настройками JSON, которыми backend его отправляет
        var request = item.GetProperty("input").Deserialize<ScoringRequest>(HttpScoringService.JsonOptions)
            ?? throw new InvalidOperationException($"Case {id}: empty input.");
        var expected = item.GetProperty("expected");

        var outcome = RuleBasedScoringService.Score(request);

        outcome.ModelVersion.ShouldBe(expected.GetProperty("model_version").GetString(), id);
        outcome.Score.ShouldBe(expected.GetProperty("score").GetInt32(), id);
        outcome.Grade.ShouldBe(Enum.Parse<RiskGrade>(expected.GetProperty("risk_grade").GetString()!), id);
        outcome.ProbabilityOfDefault.ShouldBe(expected.GetProperty("probability_of_default").GetDecimal(), id);
        Lines(outcome.Factors.Select(f => $"{f.Code} | {f.Title} | {f.Impact} | {f.Value} | {f.ClientExplanation}"))
            .ShouldBe(Lines(expected.GetProperty("factors").EnumerateArray().Select(f =>
                $"{f.GetProperty("code").GetString()} | {f.GetProperty("title").GetString()} | {f.GetProperty("impact").GetInt32()} | "
                + $"{f.GetProperty("value").GetString()} | {f.GetProperty("client_explanation").GetString()}")), id);
        Lines(outcome.Warnings).ShouldBe(Lines(expected.GetProperty("warnings").EnumerateArray().Select(w => w.GetString()!)), id);
        Lines(outcome.MissingData).ShouldBe(Lines(expected.GetProperty("missing_data").EnumerateArray().Select(m => m.GetString()!)), id);
    }

    private static string Lines(IEnumerable<string> items) => string.Join(Environment.NewLine, items);

    private static Dictionary<string, JsonElement> LoadCases()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "Scoring", "conformance-rules-0.3.json");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        return document.RootElement.GetProperty("cases").EnumerateArray()
            .ToDictionary(c => c.GetProperty("id").GetString()!, c => c.Clone(), StringComparer.Ordinal);
    }
}
