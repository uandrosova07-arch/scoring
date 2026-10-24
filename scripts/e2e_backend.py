"""Сквозная проверка через API backend: демо-заявки проходят весь конвейер, решение совпадает с таблицей сравнения.

    python scripts/e2e_backend.py [адрес API] [пароль]   # по умолчанию http://localhost:8080 и Demo123!

Для каждой демо-заявки из data/demo_companies.json: вход клиентом, подача заявки с тем же продуктом, суммой и сроком,
ожидание решения конвейера, затем карточка заявки глазами менеджера. Печатает, кто считал (scoring.source и modelVersion),
балл, грейд и решение, и сверяет их со столбцом таблицы:

- считали правила rules-0.3 (этот сервис, `External`, или C# после правок, `RulesFallback`/`Rules`): столбец Python
  (`expected`, `decision.python`);
- считали резервные правила до правок (`rules-v1`) или заглушка backend (`stub-rules-*`, повторяет rules-v1): столбец C#.
На демо-заявках балл, грейд и решение в обоих столбцах одинаковые, отличается только набор факторов.

«Петрова» должна получить 400 при подаче. Код выхода 0, если всё совпало, иначе 1. Только стандартная библиотека.
Перед запуском удобно сбросить базу: `docker compose down -v && docker compose up -d`.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEMO = json.loads((Path(__file__).resolve().parent.parent / "data" / "demo_companies.json").read_text(encoding="utf-8"))
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else "Demo123!"
SETTLED = {"Approved", "ApprovedWithConditions", "Rejected", "ManualReview", "NeedsClarification", "Cancelled", "OfferDeclined"}
# Локальный стенд: системный прокси не нужен и может мешать.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict | list]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}


def login(name: str) -> str:
    status, body = http("POST", "/api/auth/login", body={"login": name, "password": PASSWORD})
    if status != 200:
        raise SystemExit(f"вход {name}: HTTP {status} {body}")
    return body["accessToken"]


def wait_settled(app_id: str, token: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status, body = http("GET", f"/api/applications/{app_id}", token)
        if status == 200 and body["status"]["code"] in SETTLED:
            return body
        if time.monotonic() > deadline:
            raise SystemExit(f"заявка {app_id} не дошла до решения за {timeout:.0f} с, последний ответ: HTTP {status}")
        time.sleep(0.5)


def _num(x):
    return None if x is None else round(float(x), 2)


def check(company: dict, manager: str) -> tuple[bool, str]:
    s = company["scenario"]
    token = login(company["backend_login"])
    status, companies = http("GET", "/api/companies", token)
    if status != 200 or not companies:
        return False, f"нет компании у {company['backend_login']}: HTTP {status}"
    payload = {"companyId": companies[0]["id"], "productCode": s["product_code"], "amount": s["amount_rub"],
               "termMonths": s["term_months"], "purpose": s["purpose"]}
    status, created = http("POST", "/api/applications", token, payload)

    if s["backend_validation_errors"]:
        ok = status == 400
        return ok, f"подача → HTTP {status}" + ("" if ok else f", ожидался 400: {created}")
    if status != 201:
        return False, f"подача → HTTP {status}: {created}"

    wait_settled(created["id"], token)
    _, card = http("GET", f"/api/applications/{created['id']}", manager)
    scoring, decision = card.get("scoring") or {}, card.get("decision") or {}
    source, model = scoring.get("source"), scoring.get("modelVersion") or ""

    if model == "rules-0.3":
        column, exp, dec = "rules-0.3", company["expected"], company["decision"]["python"]["auto"]
    elif model == "rules-v1" or model.startswith("stub-rules"):
        column, exp, dec = "C#", company["csharp_fallback"], company["decision"]["csharp_fallback"]["auto"]
    else:
        return False, f"считал неизвестный источник: source={source} modelVersion={model}"

    got = {"score": scoring.get("score"), "grade": scoring.get("grade"), "type": decision.get("type"),
           "recommended": decision.get("recommendedType"), "amount": _num(decision.get("approvedAmount")),
           "rate": _num(decision.get("interestRate")), "payment": _num(decision.get("monthlyPayment"))}
    want = {"score": exp["score"], "grade": exp["risk_grade"], "type": dec["type"], "recommended": dec["recommended_type"],
            "amount": _num(dec["amount"]), "rate": _num(dec["interest_rate"]), "payment": _num(dec["monthly_payment"])}
    if decision.get("decidedBy") == "Manager":
        # Опрос не успел до ответа демо-менеджера: сверяем только скоринг.
        got = {k: got[k] for k in ("score", "grade")}
        want = {k: want[k] for k in ("score", "grade")}
    warn = " +external_scoring_unavailable" if "external_scoring_unavailable" in (scoring.get("warnings") or []) else ""
    text = (f"{column} ({source}, {model}{warn}): {got['score']} / {got['grade']}, решение {decision.get('type')}"
            + (f", рекомендация {decision.get('recommendedType')}" if decision.get("recommendedType") else "")
            + (f", {decision.get('approvedAmount')} ₽ под {decision.get('interestRate')} %" if decision.get("approvedAmount") else ""))
    if got != want:
        diff = ", ".join(f"{k}: {got[k]} вместо {want[k]}" for k in got if got[k] != want[k])
        return False, f"{text}; не совпало: {diff}"
    return True, text


def main() -> int:
    status, _ = http("GET", "/health/ready")
    print(f"{BASE}/health/ready → HTTP {status}")
    manager = login("manager")
    failed = 0
    for company in DEMO["companies"]:
        ok, text = check(company, manager)
        failed += not ok
        print(f"{'OK  ' if ok else 'FAIL'}  {company['id']:<12} {text}")
    print("\nВсё совпало с таблицей" if not failed else f"\nРасхождений: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
