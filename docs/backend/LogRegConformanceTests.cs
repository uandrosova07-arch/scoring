using System.Text.Json;
using AlfaCredit.Core.Domain.Scoring;
using AlfaCredit.Core.Features.Scoring;
using AlfaCredit.Infrastructure.Scoring;
using Shouldly;

namespace AlfaCredit.UnitTests.Processing;

/// <summary>
/// Контрольные входы logreg-synthetic-0.1 (data/conformance/logreg-synthetic-0.1.json из репозитория scoring, копия в
/// Scoring/conformance-logreg-synthetic-0.1.json). Ожидаемые PD и логит посчитаны sklearn при обучении; тот же файл проверяет
/// tests/test_model.py в scoring. Артефакт модели: Scoring/logreg-synthetic-0.1.json (копия models/logreg-synthetic-0.1.json).
/// Набросок: в окружении ML нет .NET, файл не компилировался.
/// </summary>
public sealed class LogRegConformanceTests
{
    private static readonly JsonElement Root = Load("conformance-logreg-synthetic-0.1.json");
    private static readonly LogRegScoringService.Artifact Art =
        LogRegScoringService.Artifact.Load(Path.Combine(AppContext.BaseDirectory, "Scoring", "logreg-synthetic-0.1.json"));
    private static readonly double PdTolerance = Root.GetProperty("meta").GetProperty("tolerance").GetProperty("probability_of_default").GetDouble();

    public static TheoryData<string> CaseIds()
    {
        var data = new TheoryData<string>();
        foreach (var c in Root.GetProperty("cases").EnumerateArray())
        {
            data.Add(c.GetProperty("id").GetString()!);
        }

        return data;
    }

    [Theory]
    [MemberData(nameof(CaseIds))]
    public void Model_matches_control_case(string id)
    {
        var item = Root.GetProperty("cases").EnumerateArray().Single(c => c.GetProperty("id").GetString() == id);
        var request = item.GetProperty("input").Deserialize<ScoringRequest>(HttpScoringService.JsonOptions)
            ?? throw new InvalidOperationException($"Case {id}: empty input.");
        var expected = item.GetProperty("expected");

        // Признаки до заполнения пропусков: при расхождении PD сразу видно, какой признак считается иначе.
        var raw = LogRegScoringService.RawFeatures(request);
        var i = 0;
        foreach (var f in expected.GetProperty("features").EnumerateObject())
        {
            if (f.Value.ValueKind == JsonValueKind.Null)
            {
                raw[i].ShouldBeNull($"{id}: {f.Name}");
            }
            else
            {
                raw[i].ShouldNotBeNull($"{id}: {f.Name}");
                Math.Abs(raw[i]!.Value - f.Value.GetDouble()).ShouldBeLessThan(1e-9, $"{id}: {f.Name}");
            }

            i++;
        }

        var p = LogRegScoringService.Predict(request, Art);
        Math.Abs(p.Logit - expected.GetProperty("logit").GetDouble()).ShouldBeLessThan(1e-9, id);
        Math.Abs(p.Pd - expected.GetProperty("probability_of_default").GetDouble()).ShouldBeLessThanOrEqualTo(PdTolerance, id);

        var outcome = LogRegScoringService.Score(request, Art);
        outcome.ModelVersion.ShouldBe("logreg-synthetic-0.1");
        outcome.Score.ShouldBe(expected.GetProperty("score").GetInt32(), id);
        outcome.Grade.ShouldBe(Enum.Parse<RiskGrade>(expected.GetProperty("risk_grade").GetString()!), id);
        Lines(outcome.Factors.Select(f => $"{f.Code} | {f.Title} | {f.Impact} | {f.Value} | {f.ClientExplanation}"))
            .ShouldBe(Lines(expected.GetProperty("factors").EnumerateArray().Select(f =>
                $"{f.GetProperty("code").GetString()} | {f.GetProperty("title").GetString()} | {f.GetProperty("impact").GetInt32()} | "
                + $"{f.GetProperty("value").GetString()} | {f.GetProperty("client_explanation").GetString()}")), id);
        Lines(outcome.Warnings).ShouldBe(Lines(expected.GetProperty("warnings").EnumerateArray().Select(w => w.GetString()!)), id);
        Lines(outcome.MissingData).ShouldBe(Lines(expected.GetProperty("missing_data").EnumerateArray().Select(m => m.GetString()!)), id);
    }

    [Fact]
    public void Pd_to_score_mapping_matches()
    {
        foreach (var row in Root.GetProperty("score_mapping").EnumerateArray())
        {
            var pd = row.GetProperty("probability_of_default").GetDouble();
            var score = LogRegScoringService.ScoreFromPd(pd, Art);
            score.ShouldBe(row.GetProperty("score").GetInt32(), $"pd {pd}");
            RiskGrades.FromScore(score).ShouldBe(Enum.Parse<RiskGrade>(row.GetProperty("risk_grade").GetString()!), $"pd {pd}");
        }
    }

    private static string Lines(IEnumerable<string> items) => string.Join(Environment.NewLine, items);

    private static JsonElement Load(string name)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Scoring", name)));
        return document.RootElement.Clone();
    }
}
