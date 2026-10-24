"""Демо-заявки по seed backend: запросы, ожидаемый ответ Python, резервные C#-правила и итоговое решение банка.

    python scripts/demo_comparison.py build            # пересобрать data/demo_companies.json и data/requests/*.json
    python scripts/demo_comparison.py check            # то же в памяти и сравнить с файлами: код выхода 1, если отличается
    python scripts/demo_comparison.py table [адрес]    # таблица Python vs C# (Python берётся из запущенного сервиса)
    python scripts/demo_comparison.py examples адрес   # пересобрать docs/score-examples.md по ответам сервиса

Данные компаний, продукты, суммы и сроки берутся из scripts/backend_reference.py (копия seed и правил backend).
Ожидаемый ответ Python (rules-0.3) считается копией C# с согласованными правками, независимо от app/rules.py:
тесты сверяют с ним настоящий сервис. Столбец csharp_fallback: код backend как есть (rules-v1). Дата расчёта зафиксирована (AS_OF), поэтому возраст бизнеса и все
результаты воспроизводимы. Только стандартная библиотека.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_reference as br  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEMO_FILE = ROOT / "data" / "demo_companies.json"
REQUESTS_DIR = ROOT / "data" / "requests"
EXAMPLES_FILE = ROOT / "docs" / "score-examples.md"

AS_OF = date(2026, 10, 23)
CREATED_AT = "2026-10-23T09:00:00Z"
PYTHON_MODEL_VERSION = "rules-0.3"

TITLES = {
    "demo-good": "ООО «Ромашка»: стабильный бизнес",
    "demo-medium": "ИП Кузнецов: заметная долговая нагрузка",
    "demo-manual": "ООО «Вектор»: крупная сумма",
    "demo-bad": "ООО «Закат»: просрочки и налоговая задолженность",
    "demo-new": "ИП Петрова: бизнесу 4 месяца, нет расходов и прибыли",
}


# ---------------------------------------------------------------- ожидаемый ответ Python (rules-0.3)

def python_expected(req: dict) -> dict:
    """Ответ, который должен дать сервис: копия C# с правками rules-0.3 (не app/rules.py), см. scripts/backend_reference.py."""
    r = br.score_rules_v1(req, agreed=True)
    return {
        "score": r["score"],
        "risk_grade": r["risk_grade"],
        "probability_of_default": float(r["probability_of_default"]),
        "missing_data": r["missing_data"],
        "warnings": r["warnings"],
        "factors": [{"code": f["code"], "impact": f["impact"], "value": f["value"]} for f in r["factors"]],
    }


# ---------------------------------------------------------------- сборка данных

def _money(x):
    return None if x is None else float(x) if x != x.to_integral_value() else int(x)


def _decision_json(d: dict | None) -> dict | None:
    if d is None:
        return None
    # Ключ reasons переименован: в data/ это слово зарезервировано за старым форматом ответа (см. tests/test_demo.py).
    return {("decision_reasons" if k == "reasons" else k): _money(v) if k in ("amount", "interest_rate", "monthly_payment") and v is not None else v
            for k, v in d.items()}


def _decisions(s: br.Scenario, req: dict, grade: str, score: int, codes: list[str]) -> dict:
    auto = br.decide(s, req["checks"], grade, score, codes)
    out = {"auto": _decision_json(auto)}
    if auto["type"] == "ManualReview":
        # В демо менеджер отвечает сам: 50/50 одобрение или отказ. Условия одобрения зависят от грейда.
        out["demo_manager_if_approved"] = _decision_json(br.demo_manager_approve(s, grade))
    return out


def build_all() -> tuple[dict, dict[str, dict]]:
    companies, requests = [], {}
    for n, s in enumerate(br.DEMO_SCENARIOS, start=1):
        req = br.to_json_value(br.build_request(
            s, request_id=f"00000000-0000-4000-8000-00000000090{n}", application_id=f"00000000-0000-4000-9000-00000000090{n}",
            number=f"AC-00090{n}", created_at=CREATED_AT, today=AS_OF))
        requests[s.id] = req
        py = python_expected(req)
        cs = br.score_rules_v1(req)
        cs_factors = [{"code": f["code"], "impact": f["impact"], "value": f["value"]} for f in cs["factors"]]
        errors = br.validate_application(s, AS_OF)
        companies.append({
            "id": s.id,
            "title": TITLES[s.id],
            "backend_login": s.login,
            "scenario": {
                "product_code": s.product, "amount_rub": int(s.amount), "term_months": s.term, "purpose": s.purpose,
                "agreed_outcome": s.agreed_outcome, "agreed_note": s.agreed_note,
                # Пусто: заявка проходит подачу и этап Validate и доходит до скоринга.
                "backend_validation_errors": errors,
            },
            "input": req,
            "expected": py,
            "csharp_fallback": {
                "source": f"rules-v1: код backend {br.BACKEND_COMMIT} до правок rules-0.3 (scripts/backend_reference.py), "
                          "подтверждён бэкендером тестом DemoSeedScoringTests",
                "score": cs["score"], "risk_grade": cs["risk_grade"],
                "probability_of_default": float(cs["probability_of_default"]),
                "factors": cs_factors, "warnings": cs["warnings"], "missing_data": cs["missing_data"],
            },
            "decision": {
                "python": _decisions(s, req, py["risk_grade"], py["score"], [f["code"] for f in py["factors"]]),
                "csharp_fallback": _decisions(s, req, cs["risk_grade"], cs["score"], [f["code"] for f in cs["factors"]]),
            },
        })
    meta = {
        "status": "rules-0.3: факторы и пороги резервных C#-правил плюс согласуемое правило для расходов",
        "backend_commit": br.BACKEND_COMMIT,
        "as_of": AS_OF.isoformat(),
        "contract": "POST /api/v1/score, docs/scoring-contract.md в репозитории backend",
        "model_version": PYTHON_MODEL_VERSION,
        "note": "Собрано scripts/demo_comparison.py build. input: запрос ровно в том виде, как его отправит backend "
                "(SeedData.cs, KnownProfiles.cs, продукт, сумма и срок из общего демо, возраст бизнеса на as_of). "
                "expected: ответ Python rules-0.3, посчитанный копией C# с правками rules-0.3, а не сервисом. "
                "csharp_fallback: текущие резервные правила backend (rules-v1). "
                "decision: решение кредитной политики backend при каждом из двух ответов.",
    }
    return {"meta": meta, "companies": companies}, requests


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def build() -> int:
    demo, requests = build_all()
    DEMO_FILE.write_text(_dump(demo), encoding="utf-8")
    REQUESTS_DIR.mkdir(exist_ok=True)
    for name, req in requests.items():
        (REQUESTS_DIR / f"{name}.json").write_text(_dump(req), encoding="utf-8")
    print(f"записано: {DEMO_FILE.relative_to(ROOT)}, {len(requests)} файлов в {REQUESTS_DIR.relative_to(ROOT)}")
    return 0


def check() -> int:
    demo, requests = build_all()
    stale = [] if DEMO_FILE.read_text(encoding="utf-8") == _dump(demo) else [str(DEMO_FILE.relative_to(ROOT))]
    for name, req in requests.items():
        path = REQUESTS_DIR / f"{name}.json"
        if not path.exists() or path.read_text(encoding="utf-8") != _dump(req):
            stale.append(str(path.relative_to(ROOT)))
    print("данные совпадают с пересборкой" if not stale else "отличаются от пересборки: " + ", ".join(stale))
    return 1 if stale else 0


# ---------------------------------------------------------------- вызов сервиса

def call(base: str, payload: dict | bytes) -> tuple[int, dict]:
    data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(base.rstrip("/") + "/api/v1/score", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _short(d: dict | None) -> str:
    if d is None:
        return "—"
    t = {"Approved": "одобрено", "ApprovedWithConditions": "с условиями", "ManualReview": "менеджер",
         "Rejected": "отказ"}[d["type"]]
    if d["type"] == "ManualReview":
        return f"{t} (рек. {d['recommended_type']})"
    if d["type"] == "Rejected":
        return f"{t}: {', '.join(r for r in d['decision_reasons'] if not r.startswith(('risk_grade', 'factor_')))}"
    def rub(x) -> str:
        return f"{x:,.2f}".replace(",", " ").replace(".", ",").removesuffix(",00")

    rate = f"{d['interest_rate']:g}".replace(".", ",")
    return f"{t}: {rub(d['amount'])} ₽ под {rate} %, платёж {rub(d['monthly_payment'])} ₽/мес"


def table(base: str | None) -> int:
    demo = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    print("| Заявка | Python: балл / грейд | C#: балл / грейд | Решение при Python | Решение при C# |")
    print("|---|---|---|---|---|")
    failed = 0
    for c in demo["companies"]:
        exp = c["expected"]
        if base:
            status, body = call(base, c["input"])
            if status != 200 or (body["score"], body["risk_grade"]) != (exp["score"], exp["risk_grade"]):
                failed += 1
                print(f"<!-- {c['id']}: сервис ответил {status} {body.get('score')}/{body.get('risk_grade')}, "
                      f"ожидалось {exp['score']}/{exp['risk_grade']} -->")
        cs = c["csharp_fallback"]
        print(f"| {c['id']} | {exp['score']} / {exp['risk_grade']} | {cs['score']} / {cs['risk_grade']} "
              f"| {_short(c['decision']['python']['auto'])} | {_short(c['decision']['csharp_fallback']['auto'])} |")
    return 1 if failed else 0


def examples(base: str) -> int:
    demo = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    out = [
        "# Примеры вызова `POST /api/v1/score`", "",
        "Контракт: `docs/scoring-contract.md` в репозитории backend. Адрес внутри Compose: `http://scoring:8000/api/v1/score`.",
        f"Запросы собраны из seed backend (коммит `{demo['meta']['backend_commit']}`) ровно в том виде, как их отправляет backend,"
        f" возраст бизнеса на {demo['meta']['as_of']}. Ответы сняты с запущенного сервиса `{PYTHON_MODEL_VERSION}`"
        " командой `python scripts/demo_comparison.py examples http://localhost:8000`, а не написаны вручную.",
        "Готовые файлы запросов: `data/requests/*.json` (`curl -d @data/requests/demo-good.json ...`). Сравнение с резервными"
        " C#-правилами и итоговые решения: [grade-comparison.md](grade-comparison.md).", "",
    ]
    for c in demo["companies"]:
        status, body = call(base, c["input"])
        if status != 200:
            print(f"{c['id']}: сервис ответил {status}", file=sys.stderr)
            return 1
        out += [f"## {c['title']} (`{c['id']}`): {body['score']} баллов, грейд {body['risk_grade']}", "",
                "Заявка: {}, {} ₽ на {} мес.".format(c["scenario"]["product_code"], f"{c['scenario']['amount_rub']:,}".replace(",", " "),
                                                    c["scenario"]["term_months"])]
        if c["scenario"]["backend_validation_errors"]:
            out.append("В демо такой запрос не отправляется: " + "; ".join(c["scenario"]["backend_validation_errors"].values()) + ".")
        out += ["", "```bash", f"curl -s -X POST http://localhost:8000/api/v1/score -H 'Content-Type: application/json' -d @data/requests/{c['id']}.json",
                "```", "", "Ответ `200`:", "", "```json", json.dumps(body, ensure_ascii=False, indent=2), "```", ""]
    bad = dict(demo["companies"][0]["input"])
    bad["application"] = {**bad["application"], "amount_rub": "1500000", "term_months": 0}
    status, body = call(base, bad)
    out += ["## Некорректный запрос", "",
            "Сумма строкой и нулевой срок в запросе `demo-good`. Сервис не приводит типы молча и отвечает `400`;"
            " backend на `4xx` не повторяет запрос и сразу считает резервными C#-правилами (`RulesFallback`).", "",
            f"Ответ `{status}`:", "", "```json", json.dumps(body, ensure_ascii=False, indent=2), "```", ""]
    EXAMPLES_FILE.write_text("\n".join(out), encoding="utf-8")
    print(f"записано: {EXAMPLES_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["check"]
    if cmd == "build":
        sys.exit(build())
    if cmd == "check":
        sys.exit(check())
    if cmd == "table":
        sys.exit(table(rest[0] if rest else None))
    if cmd == "examples" and rest:
        sys.exit(examples(rest[0]))
    print(__doc__)
    sys.exit(2)
