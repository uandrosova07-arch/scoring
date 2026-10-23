using AlfaCredit.Core.Abstractions;
using AlfaCredit.Core.Common;
using AlfaCredit.Core.Domain.Applications;
using AlfaCredit.Core.Domain.Checks;
using AlfaCredit.Core.Domain.Companies;
using AlfaCredit.Core.Domain.Decisions;
using AlfaCredit.Core.Domain.Products;
using AlfaCredit.Core.Domain.Scoring;
using AlfaCredit.Core.Features.Decisions;
using AlfaCredit.Core.Features.Processing;
using AlfaCredit.Core.Features.Scoring;
using AlfaCredit.Infrastructure.Persistence.Seeding;
using AlfaCredit.Infrastructure.Scoring;
using Shouldly;

namespace AlfaCredit.UnitTests.Processing;

/// <summary>
/// Сверка с сервисом скоринга (репозиторий scoring: docs/grade-comparison.md, data/demo_companies.json).
/// Демо-заявки общего сценария на данных seed: запрос к скорингу, резервные правила rules-v1 и решение кредитной политики
/// при ответе Python (rules-0.2) и при откате на встроенные правила. Если тест упал, изменились seed, правила или политика:
/// сообщите команде скоринга, таблицу сравнения нужно пересобрать (python scripts/demo_comparison.py build).
/// </summary>
public sealed class DemoSeedScoringTests
{
    // Дата расчёта зафиксирована: от неё зависит возраст бизнеса в запросе.
    private static readonly DateTimeOffset AsOf = new(2026, 10, 23, 9, 0, 0, TimeSpan.Zero);

    // data/requests/demo-good.json из репозитория scoring в компактном виде.
    private const string DemoGoodRequestJson = """{"request_id":"00000000-0000-4000-8000-000000000901","application":{"id":"00000000-0000-4000-9000-000000000901","number":"AC-000901","created_at":"2026-10-23T09:00:00Z","amount_rub":1500000,"term_months":12,"purpose":"WorkingCapital","estimated_monthly_payment_rub":138593,"product":{"code":"working-capital","type":"WorkingCapital","base_rate_percent":19.5,"requires_collateral":false,"auto_approval_limit_rub":5000000},"collateral_offered":false},"company":{"inn":"7701234567","legal_form":"Ooo","okved":"47.11","region_code":"77","registered_at":"2019-03-12","business_age_months":91,"tax_regime":"Osno","employees_count":24,"financials":{"period_months":12,"revenue_rub":48000000,"expenses_rub":40800000,"net_profit_rub":7200000,"monthly_debt_payments_rub":150000},"history":{"previous_applications":0,"previous_rejections":0,"previous_loans_disbursed":0}},"checks":{"egrul":{"status":"Active","head_disqualified":false,"mass_address":false,"registered_at":"2019-03-12","okved":"47.11"},"fns":{"tax_debt_rub":0,"has_blocked_accounts":false,"reported_revenue_rub":47300000},"credit_bureau":{"credit_history_months":60,"active_loans":1,"closed_loans":3,"total_debt_rub":1200000,"monthly_payments_rub":150000,"max_overdue_days_24m":0,"overdue_count_24m":0,"has_write_offs":false},"fssp":{"open_proceedings":0,"open_amount_rub":0}}}""";

    private readonly DecisionPolicy _policy = new(new DecisionPolicyOptions());

    [Fact]
    public void Request_for_demo_good_is_identical_to_the_scoring_repository_example()
    {
        var (_, request) = Build("client_good", "working-capital", 1_500_000, 12);
        var pinned = request with
        {
            RequestId = Guid.Parse("00000000-0000-4000-8000-000000000901"),
            Application = request.Application with { Id = Guid.Parse("00000000-0000-4000-9000-000000000901"), Number = "AC-000901" },
        };

        HttpScoringService.SerializeRequest(pinned).ShouldBe(DemoGoodRequestJson);
    }

    [Theory]
    [InlineData("client_good", "working-capital", 1_500_000, 12, 91, 138_593, 95, "A", "credit_history:15,revenue_to_amount:15,business_age:10,tax_debt:5,debt_load:0,enforcement:0")]
    [InlineData("client_medium", "working-capital", 3_000_000, 24, 40, 151_956, 55, "C", "business_age:10,credit_history:-5,revenue_to_amount:5,tax_debt:-5,debt_load:0,enforcement:0")]
    [InlineData("client_manual", "working-capital", 8_000_000, 24, 121, 405_215, 70, "B", "debt_load:-25,credit_history:15,revenue_to_amount:15,business_age:10,tax_debt:5,enforcement:0")]
    [InlineData("client_bad", "express", 500_000, 6, 80, 89_012, 0, "E", "credit_history:-30,tax_debt:-30,debt_load:-25,revenue_to_amount:15,business_age:10,enforcement:-10")]
    public void Fallback_rules_on_seed_demo_application(string login, string productCode, int amount, int termMonths, int ageMonths, int payment, int score, string grade, string factors)
    {
        var (_, request) = Build(login, productCode, amount, termMonths);

        request.Company.BusinessAgeMonths.ShouldBe(ageMonths);
        request.Application.EstimatedMonthlyPaymentRub.ShouldBe(payment);

        var outcome = RuleBasedScoringService.Score(request);

        outcome.Score.ShouldBe(score);
        outcome.Grade.ShouldBe(Enum.Parse<RiskGrade>(grade));
        string.Join(",", outcome.Factors.Select(f => $"{f.Code}:{f.Impact}")).ShouldBe(factors);
        outcome.Warnings.ShouldBeEmpty();
    }

    // source: python — балл и грейд, которые сервис scoring (rules-0.2) возвращает на этот же запрос; rules — резервные правила.
    [Theory]
    [InlineData("python", "client_good", "working-capital", 1_500_000, 12, 55, "C", "ApprovedWithConditions", null, 1_500_000d, 22.5, 140_752.78)]
    [InlineData("rules", "client_good", "working-capital", 1_500_000, 12, 95, "A", "Approved", null, 1_500_000d, 19.5, 138_593.06)]
    [InlineData("python", "client_medium", "working-capital", 3_000_000, 24, 75, "B", "Approved", null, 3_000_000d, 21d, 154_156.95)]
    [InlineData("rules", "client_medium", "working-capital", 3_000_000, 24, 55, "C", "ApprovedWithConditions", null, 2_400_000d, 22.5, 125_100.96)]
    [InlineData("python", "client_manual", "working-capital", 8_000_000, 24, 35, "D", "ManualReview", "ApprovedWithConditions", null, null, null)]
    [InlineData("rules", "client_manual", "working-capital", 8_000_000, 24, 70, "B", "ManualReview", "Approved", null, null, null)]
    [InlineData("python", "client_bad", "express", 500_000, 6, 20, "E", "Rejected", null, null, null, null)]
    [InlineData("rules", "client_bad", "express", 500_000, 6, 0, "E", "Rejected", null, null, null, null)]
    public void Policy_outcome_with_python_and_fallback_grades(
        string source,
        string login,
        string productCode,
        int amount,
        int termMonths,
        int score,
        string grade,
        string type,
        string? recommended,
        double? approvedAmount,
        double? rate,
        double? monthlyPayment)
    {
        var (application, request) = Build(login, productCode, amount, termMonths);
        var scoring = ScoringResult.Create(
            application.Id,
            request.RequestId,
            new ScoringOutcome(score, Enum.Parse<RiskGrade>(grade), null, [], [], [], source, source == "python" ? ScoringSource.External : ScoringSource.RulesFallback),
            durationMs: 0,
            AsOf);

        var outcome = _policy.Decide(application, application.ChecksSnapshot, scoring);

        outcome.Type.ShouldBe(Enum.Parse<DecisionType>(type), source);
        outcome.RecommendedType.ShouldBe(recommended is null ? (DecisionType?)null : Enum.Parse<DecisionType>(recommended), source);
        outcome.Amount.ShouldBe((decimal?)approvedAmount, source);
        outcome.InterestRate.ShouldBe((decimal?)rate, source);
        outcome.MonthlyPayment.ShouldBe((decimal?)monthlyPayment, source);
    }

    [Fact]
    public void New_company_is_rejected_at_submission_and_fallback_would_give_grade_E()
    {
        Should.Throw<ValidationException>(() => { Build("client_new", "express", 300_000, 12); });

        // Тот же запрос, что data/requests/demo-new.json: backend его сейчас не отправляет, это проверка нехватки данных.
        var registeredAt = new DateOnly(2026, 6, 15);
        var payment = Money.WholeRubles(LoanMath.MonthlyPayment(300_000, 23.0m, 12));
        var request = new ScoringRequest(
            Guid.CreateVersion7(),
            new ScoringApplication(Guid.NewGuid(), "AC-000905", AsOf, 300_000, 12, LoanPurpose.WorkingCapital, payment, new ScoringProduct("express", ProductType.Express, 23.0m, false, 3_000_000), false),
            new ScoringCompany("772812345678", LegalForm.Ip, "96.02", "77", registeredAt, 4, TaxRegime.Patent, 2, new ScoringFinancials(12, 1_200_000, null, null, 0), new ScoringHistory(0, 0, 0)),
            new ScoringChecks(
                new EgrulResult(EgrulStatus.Active, false, false, registeredAt, "96.02"),
                new FnsResult(0, false, null),
                new CreditBureauResult(0, 0, 0, 0, 0, 0, 0, false),
                new FsspResult(0, 0)));

        var outcome = RuleBasedScoringService.Score(request);

        payment.ShouldBe(28_223);
        outcome.Score.ShouldBe(25);
        outcome.Grade.ShouldBe(RiskGrade.E);
        outcome.Warnings.ShouldHaveSingleItem().ShouldBe("net_profit_missing");
    }

    /// <summary>Заявка как в DbSeeder и ApplicationService: компания и продукт из seed, проверки как у симуляторов.</summary>
    private static (CreditApplication Application, ScoringRequest Request) Build(string login, string productCode, decimal amount, int termMonths)
    {
        var demo = SeedData.Companies.Single(c => c.OwnerLogin == login);
        var company = Company.Create(
            demo.Inn,
            demo.Ogrn,
            demo.Name,
            demo.LegalForm,
            demo.Okved,
            demo.RegisteredAt,
            demo.Address,
            demo.HeadName,
            demo.TaxRegime,
            demo.AnnualRevenue,
            demo.AnnualExpenses,
            demo.NetProfit,
            demo.MonthlyDebtPayments,
            demo.EmployeesCount,
            AsOf);
        var product = SeedData.CreateProducts().Single(p => p.Code == productCode);
        var application = CreditApplication.Create(company, product, Guid.NewGuid(), amount, termMonths, LoanPurpose.WorkingCapital, null, AsOf);

        foreach (var check in KnownChecks(application.Id, demo.Inn, demo.RegisteredAt, demo.Okved))
        {
            application.RecordCheck(check);
        }

        return (application, ApplicationPipeline.BuildScoringRequest(application, new CompanyApplicationHistory(0, 0, 0), AsOf));
    }

    /// <summary>Копия KnownProfiles.cs (класс internal в Infrastructure): ответы симуляторов по ИНН демо-компаний.</summary>
    private static IEnumerable<ExternalCheck> KnownChecks(Guid applicationId, string inn, DateOnly registeredAt, string okved)
    {
        var (egrul, fns, bureau, fssp) = inn switch
        {
            "7701234567" => (new EgrulResult(EgrulStatus.Active, false, false, registeredAt, okved), new FnsResult(0, false, 47_300_000), new CreditBureauResult(60, 1, 3, 1_200_000, 150_000, 0, 0, false), new FsspResult(0, 0)),
            "503212345678" => (new EgrulResult(EgrulStatus.Active, false, false, registeredAt, okved), new FnsResult(12_000, false, 9_100_000), new CreditBureauResult(30, 1, 1, 900_000, 40_000, 25, 1, false), new FsspResult(0, 0)),
            "7812345678" => (new EgrulResult(EgrulStatus.Active, false, false, registeredAt, okved), new FnsResult(0, false, 176_000_000), new CreditBureauResult(96, 3, 5, 40_000_000, 1_200_000, 0, 0, false), new FsspResult(0, 0)),
            "5401234567" => (new EgrulResult(EgrulStatus.Active, false, true, registeredAt, okved), new FnsResult(700_000, true, 13_200_000), new CreditBureauResult(48, 4, 1, 9_000_000, 600_000, 95, 3, false), new FsspResult(2, 1_400_000)),
            _ => throw new ArgumentOutOfRangeException(nameof(inn), inn, "Нет профиля симуляторов для этого ИНН."),
        };

        yield return ExternalCheck.Succeeded(applicationId, CheckSource.Egrul, egrul, "ok", [], 1, AsOf);
        yield return ExternalCheck.Succeeded(applicationId, CheckSource.Fns, fns, "ok", [], 1, AsOf);
        yield return ExternalCheck.Succeeded(applicationId, CheckSource.CreditBureau, bureau, "ok", [], 1, AsOf);
        yield return ExternalCheck.Succeeded(applicationId, CheckSource.Fssp, fssp, "ok", [], 1, AsOf);
    }
}
