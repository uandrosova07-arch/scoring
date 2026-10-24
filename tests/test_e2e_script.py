"""scripts/e2e_backend.py на фейковом backend: положительные сценарии доходят до выдачи, отказ не пускает к договору,
скрипт падает, если режим, версия правил или поведение отказа не те. Настоящий стенд: scripts/run_stand_checks.sh."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import e2e_backend  # noqa: E402
from support.fake_backend import FakeBackend  # noqa: E402


def run(fake: FakeBackend, mode: str, tmp_path: Path, *extra: str) -> tuple[int, dict]:
    out = tmp_path / "result.json"
    code = e2e_backend.main(["--api", fake.url, "--scoring", fake.url, "--mode", mode, "--timeout", "10",
                             "--json", str(out), "--report", str(tmp_path / "report.md"), *extra])
    return code, json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", ["External", "RulesFallback"])
@pytest.mark.parametrize("reveal_code", [True, False], ids=["code-in-response", "code-from-feed"])
def test_positive_scenarios_reach_disbursement_and_reject_stops(mode, reveal_code, tmp_path):
    with FakeBackend(mode=mode, reveal_code=reveal_code) as fake:
        code, data = run(fake, mode, tmp_path)
    by = {r["scenario"]: r for r in data["results"]}
    assert code == 0, [c for r in data["results"] for c in r["checks"] if not c["ok"]]
    assert by["approve"]["disbursement"]["amount"] == 1_500_000 and by["approve"]["disbursement"]["status"] == "Completed"
    assert by["reduced"]["disbursement"]["amount"] == 2_400_000
    assert "amount_reduced" in by["reduced"]["decision"]["reasons"]
    assert by["reject"]["outcome"].startswith("Rejected") and "disbursement" not in by["reject"]
    assert {r["scoring"]["source"] for r in data["results"] if r.get("scoring")} == {mode}
    assert by["approve"]["code_source"] == ("ответ API" if reveal_code else "лента событий")
    assert data["versions"]["expected_model"] == "rules-0.3"
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith(f"# Сквозная проверка: {mode} / rules-0.3")


def test_fails_when_fallback_still_runs_rules_v1(tmp_path):
    with FakeBackend(mode="RulesFallback", fallback_agreed=False) as fake:
        code, data = run(fake, "RulesFallback", tmp_path)
    assert code == 1
    failed = {c["check"] for r in data["results"] for c in r["checks"] if not c["ok"]}
    assert "версия правил" in failed


def test_fails_when_mode_differs(tmp_path):
    with FakeBackend(mode="External") as fake:
        code, _ = run(fake, "RulesFallback", tmp_path)
    assert code == 1


def test_fails_when_rejected_application_can_get_a_contract(tmp_path):
    with FakeBackend(mode="External", contract_for_rejected=True) as fake:
        code, data = run(fake, "External", tmp_path)
    reject = next(r for r in data["results"] if r["scenario"] == "reject")
    assert code == 1 and not reject["ok"]
    assert "принять предложение нельзя" in {c["check"] for c in reject["checks"] if not c["ok"]}
