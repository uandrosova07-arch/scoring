"""Сквозная проверка через API backend: заявка → скоринг → решение → предложение → договор → код → подписание → выдача.

    python scripts/e2e_backend.py --mode External                 # сервис scoring отвечает
    python scripts/e2e_backend.py --mode RulesFallback            # scoring остановлен: считают резервные правила backend
    python scripts/e2e_backend.py --api http://localhost:18080 --scoring http://localhost:18000 --mode External \\
        --report e2e-external.md --json e2e-external.json

Сценарии (данные seed backend, суммы и сроки как в общем демо, ожидаемое из data/demo_companies.json):

| Сценарий          | Клиент        | Ожидается                                                                          |
|-------------------|---------------|------------------------------------------------------------------------------------|
| approve           | client_good   | одобрено как просили → принятие → договор → демо-код → подписание → Disbursed      |
| reduced           | client_medium | меньшая сумма с объяснением (amount_reduced) → тот же путь до Disbursed            |
| reject            | client_bad    | отказ: нет предложения и действий, принять нельзя (409), договора нет (404)        |
| manual (справка)  | client_manual | менеджер с рекомендацией «одобрить»; дальше демо-менеджер 50/50, поэтому не идём   |
| submission (справка) | client_new | 400 при подаче                                                                     |

В каждом сценарии со скорингом проверяется карточка менеджера: source = --mode, modelVersion = --model (по умолчанию rules-0.3),
балл и грейд как в таблице. Перед запуском нужна чистая база (отдельный compose-проект, см. scripts/run_stand_checks.sh).
Код выхода 0, если все обязательные проверки прошли. Только стандартная библиотека.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = {c["id"]: c for c in json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["companies"]}
SETTLED = {"Approved", "ApprovedWithConditions", "Rejected", "ManualReview", "NeedsClarification", "Cancelled", "OfferDeclined"}
SCENARIOS = (
    ("approve", "demo-good", True),
    ("reduced", "demo-medium", True),
    ("reject", "demo-bad", True),
    ("manual", "demo-manual", False),
    ("submission", "demo-new", False),
)
# Локальный стенд: системный прокси не нужен и может мешать.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Api:
    def __init__(self, base: str, password: str, timeout: float):
        self.base, self.password, self.timeout = base.rstrip("/"), password, timeout

    def call(self, method: str, path: str, token: str | None = None, body: dict | None = None, base: str | None = None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request((base or self.base) + path, data=data, headers=headers, method=method)
        try:
            with _OPENER.open(req, timeout=15) as resp:
                raw = resp.read()
                return resp.status, _parse(raw)
        except urllib.error.HTTPError as e:
            return e.code, _parse(e.read())
        except (urllib.error.URLError, OSError) as e:
            return 0, {"error": str(e)}

    def login(self, name: str) -> str:
        status, body = self.call("POST", "/api/auth/login", body={"login": name, "password": self.password})
        if status != 200:
            raise Failure(f"вход {name}: HTTP {status} {body}")
        return body["accessToken"]

    def wait(self, app_id: str, token: str, done, what: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while True:
            status, body = self.call("GET", f"/api/applications/{app_id}", token)
            if status == 200 and done(body):
                return body
            if time.monotonic() > deadline:
                code = body.get("status", {}).get("code") if isinstance(body, dict) else None
                raise Failure(f"не дождались «{what}» за {self.timeout:.0f} с: HTTP {status}, статус {code}")
            time.sleep(0.5)


def _parse(raw: bytes):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw.decode(errors="replace")[:300]}


class Failure(Exception):
    pass


class Checks:
    """Список проверок одного сценария: что ожидали, что получили, прошло ли."""

    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def eq(self, name: str, got, want) -> bool:
        ok = got == want
        self.items.append((name, ok, f"{got!r}" if ok else f"{got!r}, ожидалось {want!r}"))
        return ok

    def true(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(i[1] for i in self.items)


def _num(x):
    return None if x is None else round(float(x), 2)


def _signing_code(api: Api, app_id: str, token: str, issued: dict) -> str:
    if issued.get("demoCode"):
        return issued["demoCode"]
    # Код не раскрыт в ответе (Contracts__RevealCodeInResponse=false): берём его из ленты, как клиент в кабинете.
    status, feed = api.call("GET", "/api/feed?pageSize=50", token)
    for item in (feed.get("items") or []) if status == 200 else []:
        if item.get("applicationId") == app_id and item.get("type") == "SigningCode":
            # «…договора KD-000001: 483921. Действует…»: в номере договора тоже 6 цифр, код идёт после двоеточия
            if m := re.search(r":\s*(\d{6})\b", item.get("body", "")):
                return m.group(1)
    raise Failure("демо-код не найден ни в ответе, ни в ленте")


def scenario_data(demo_id: str, model_version: str) -> dict:
    company = copy.deepcopy(DEMO[demo_id])
    if model_version == "logreg-synthetic-0.1":
        controls = json.loads((ROOT / "data" / "conformance" / f"{model_version}.json").read_text(encoding="utf-8"))
        target = "demo-reduced" if demo_id == "demo-medium" else demo_id
        case = next(c for c in controls["cases"] if c["id"] == target)
        company["expected"] = case["expected"]
        if demo_id == "demo-medium":
            company["backend_login"] = "client_reduced"
    return company


def run_scenario(api: Api, name: str, demo_id: str, manager: str, args) -> dict:
    company = scenario_data(demo_id, args.model)
    s = company["scenario"]
    exp = company["expected"]
    dec = company["decision"]["python"]["auto"]
    checks, result = Checks(), {"scenario": name, "demo_id": demo_id, "login": company["backend_login"],
                                "input": {"product": s["product_code"], "amount": s["amount_rub"], "term_months": s["term_months"],
                                          "purpose": s["purpose"]}}
    started = time.monotonic()
    try:
        token = api.login(company["backend_login"])
        status, companies = api.call("GET", "/api/companies", token)
        if status != 200 or not companies:
            raise Failure(f"нет компании: HTTP {status}")
        payload = {"companyId": companies[0]["id"], "productCode": s["product_code"], "amount": s["amount_rub"],
                   "termMonths": s["term_months"], "purpose": s["purpose"]}
        status, created = api.call("POST", "/api/applications", token, payload)

        if name == "submission":
            checks.eq("подача отклонена", status, 400)
            result["outcome"] = f"HTTP {status} при подаче"
            return _finish(result, checks, started)
        if not checks.eq("заявка создана", status, 201):
            raise Failure(f"подача: {created}")
        app_id = created["id"]
        result["application_id"] = app_id

        settled = api.wait(app_id, token, lambda b: b["status"]["code"] in SETTLED, "решение")
        _, card = api.call("GET", f"/api/applications/{app_id}", manager)
        scoring, decision = card.get("scoring") or {}, settled.get("decision") or {}
        if decision.get("decidedBy") == "Manager":
            decision = card.get("decision") or decision
        result["scoring"] = {k: scoring.get(k) for k in ("score", "grade", "probabilityOfDefault", "source", "modelVersion", "warnings")}
        checks.eq("источник скоринга", scoring.get("source"), args.mode)
        checks.eq("версия правил", scoring.get("modelVersion"), args.model)
        if args.mode == "RulesFallback":
            checks.true("предупреждение external_scoring_unavailable", "external_scoring_unavailable" in (scoring.get("warnings") or []))
        checks.eq("балл и грейд", (scoring.get("score"), scoring.get("grade")), (exp["score"], exp["risk_grade"]))
        result["decision"] = {k: decision.get(k) for k in ("type", "recommendedType", "approvedAmount", "interestRate", "monthlyPayment", "conditions")}
        result["decision"]["reasons"] = [r.get("code") for r in decision.get("reasons") or []]

        if name == "manual":
            checks.eq("решение", (decision.get("type"), decision.get("recommendedType")), ("ManualReview", "Approved"))
            result["outcome"] = "ManualReview, рекомендация Approved"
            return _finish(result, checks, started)

        if name == "reject":
            checks.eq("решение", decision.get("type"), "Rejected")
            checks.eq("причины-стоп-факторы", sorted(r for r in result["decision"]["reasons"]), sorted(dec["decision_reasons"]))
            checks.true("у отказа есть объяснение", all(r.get("explanation") for r in decision.get("reasons") or []))
            checks.eq("предложения нет", settled.get("offer"), None)
            checks.true("нет действий accept/sign", not {"acceptOffer", "signContract"} & set(settled.get("availableActions") or []),
                        str(settled.get("availableActions")))
            st_accept, _ = api.call("POST", f"/api/applications/{app_id}/offer/accept", token)
            checks.eq("принять предложение нельзя", st_accept, 409)
            st_contract, _ = api.call("GET", f"/api/applications/{app_id}/contract", token)
            checks.eq("договора нет", st_contract, 404)
            st_code, _ = api.call("POST", f"/api/applications/{app_id}/contract/sign/request-code", token)
            checks.true("код подписания не выдаётся", st_code in (404, 409), f"HTTP {st_code}")
            _, final = api.call("GET", f"/api/applications/{app_id}", token)
            checks.eq("статус остался Rejected", final.get("status", {}).get("code"), "Rejected")
            checks.eq("выдачи нет", final.get("disbursement"), None)
            result["outcome"] = "Rejected, договор и выдача недоступны"
            return _finish(result, checks, started)

        # approve и reduced: предложение → договор → код → подпись → выдача
        checks.eq("решение", decision.get("type"), dec["type"])
        checks.eq("одобренная сумма", _num(decision.get("approvedAmount")), _num(dec["amount"]))
        checks.eq("ставка", _num(decision.get("interestRate")), _num(dec["interest_rate"]))
        checks.eq("платёж", _num(decision.get("monthlyPayment")), _num(dec["monthly_payment"]))
        if name == "reduced":
            reduced = next((r for r in decision.get("reasons") or [] if r.get("code") == "amount_reduced"), None)
            checks.true("сумма меньше запрошенной", _num(decision.get("approvedAmount")) < s["amount_rub"])
            checks.true("есть объяснение amount_reduced", bool(reduced and reduced.get("explanation")),
                        (reduced or {}).get("explanation", ""))
            checks.true("условия в решении", bool(decision.get("conditions")), decision.get("conditions") or "")
        offer = settled.get("offer") or {}
        checks.eq("сумма предложения", _num(offer.get("amount")), _num(dec["amount"]))
        checks.true("можно принять", "acceptOffer" in (settled.get("availableActions") or []))

        status, accepted = api.call("POST", f"/api/applications/{app_id}/offer/accept", token)
        checks.eq("принятие предложения", status, 200)
        checks.eq("статус после принятия", accepted.get("status", {}).get("code"), "ContractSigning")
        status, contract = api.call("GET", f"/api/applications/{app_id}/contract", token)
        checks.eq("договор сформирован", status, 200)
        checks.eq("сумма договора", _num(contract.get("amount")), _num(dec["amount"]))
        checks.eq("ставка договора", _num(contract.get("interestRate")), _num(dec["interest_rate"]))
        checks.eq("строк в графике", len(contract.get("schedule") or []), s["term_months"])
        if args.model == "logreg-synthetic-0.1":
            for field in ("totalPayment", "totalInterest"):
                checks.true(f"стоимость оффера {field} присутствует", isinstance(offer.get(field), (int, float)))
                checks.eq(f"стоимость договора совпадает с оффером: {field}", _num(contract.get(field)), _num(offer.get(field)))
        result["contract"] = {"number": contract.get("number"), "amount": contract.get("amount"), "interestRate": contract.get("interestRate"),
                              "monthlyPayment": contract.get("monthlyPayment"), "totalPayment": contract.get("totalPayment")}

        status, issued = api.call("POST", f"/api/applications/{app_id}/contract/sign/request-code", token)
        checks.eq("запрос кода", status, 200)
        code = _signing_code(api, app_id, token, issued)
        result["code_source"] = "ответ API" if issued.get("demoCode") else "лента событий"
        status, signed = api.call("POST", f"/api/applications/{app_id}/contract/sign", token, {"code": code})
        checks.eq("подписание", status, 200)
        checks.true("статус после подписи", signed.get("status", {}).get("code") in ("Signed", "Disbursed"), signed.get("status", {}).get("code", ""))
        final = api.wait(app_id, token, lambda b: b["status"]["code"] == "Disbursed", "Disbursed")
        disb = final.get("disbursement") or {}
        checks.eq("выдача завершена", disb.get("status"), "Completed")
        checks.eq("сумма выдачи", _num(disb.get("amount")), _num(dec["amount"]))
        checks.true("платёжное поручение", bool(disb.get("paymentOrderNumber")), disb.get("paymentOrderNumber") or "")
        checks.eq("договор подписан", (final.get("contract") or {}).get("status"), "Signed")
        result["disbursement"] = {k: disb.get(k) for k in ("status", "amount", "paymentOrderNumber", "completedAt")}
        result["outcome"] = f"Disbursed {disb.get('amount')} ₽, {disb.get('paymentOrderNumber')}"
    except Failure as e:
        checks.true("сценарий выполнен", False, str(e))
        result["outcome"] = f"ошибка: {e}"
    return _finish(result, checks, started)


def _finish(result: dict, checks: Checks, started: float) -> dict:
    result["checks"] = [{"check": n, "ok": ok, "detail": d} for n, ok, d in checks.items]
    result["ok"] = checks.ok
    result["duration_s"] = round(time.monotonic() - started, 1)
    return result


def collect_versions(api: Api, args) -> dict:
    versions = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "api": args.api, "mode": args.mode,
                "expected_model": args.model, "demo_data_as_of": json.loads((ROOT / "data" / "demo_companies.json").read_text(encoding="utf-8"))["meta"]["as_of"]}
    st, ready = api.call("GET", "/health/ready")
    versions["api_ready"] = {"http": st, "status": ready.get("status"), "checks": {c.get("name"): c.get("status") for c in ready.get("checks") or []}}
    st, spec = api.call("GET", "/swagger/v1/swagger.json")
    versions["api_openapi"] = (spec.get("info") or {}).get("version") if st == 200 else f"HTTP {st}"
    st, health = api.call("GET", "/health", base=args.scoring.rstrip("/"))
    versions["scoring_health"] = health if st == 200 else f"недоступен (HTTP {st or 'нет соединения'})"
    return versions


def report_md(versions: dict, results: list[dict]) -> str:
    lines = [f"# Сквозная проверка: {versions['mode']} / {versions['expected_model']}", "",
             f"Время: {versions['checked_at']}. API: {versions['api']}. Демо-данные на {versions['demo_data_as_of']}.", "",
             "| Компонент | Версия / состояние |", "|---|---|",
             f"| scoring `/health` | `{json.dumps(versions['scoring_health'], ensure_ascii=False)}` |",
             f"| API `/health/ready` | {versions['api_ready']['status']} (HTTP {versions['api_ready']['http']}), {versions['api_ready']['checks']} |",
             f"| API OpenAPI | {versions['api_openapi']} |", "",
             "| Сценарий | Вход | Скоринг | Решение | Итог | Проверки |", "|---|---|---|---|---|---|"]
    for r in results:
        i = r["input"]
        sc = r.get("scoring") or {}
        d = r.get("decision") or {}
        scoring = f"{sc.get('score')} / {sc.get('grade')}, {sc.get('source')}, {sc.get('modelVersion')}" if sc else "—"
        decision = (f"{d.get('type')}" + (f", рек. {d['recommendedType']}" if d.get("recommendedType") else "")
                    + (f", {d['approvedAmount']} ₽ под {d['interestRate']} %" if d.get("approvedAmount") else "")) if d else "—"
        passed = sum(c["ok"] for c in r["checks"])
        verdict = f"{'OK' if r['ok'] else 'FAIL'} {passed}/{len(r['checks'])}"
        lines.append(f"| {r['scenario']} | {r['login']}, {i['product']}, {i['amount']} ₽, {i['term_months']} мес. | {scoring} | {decision} "
                     f"| {r.get('outcome', '')} | {verdict} |")
    failed = [(r["scenario"], c) for r in results for c in r["checks"] if not c["ok"]]
    if failed:
        lines += ["", "Не прошли:", ""] + [f"- {s}: {c['check']} — {c['detail']}" for s, c in failed]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--api", default="http://localhost:8080")
    p.add_argument("--scoring", default="http://localhost:8000", help="адрес сервиса scoring для /health (версия)")
    p.add_argument("--mode", choices=("External", "RulesFallback", "Rules"), required=True, help="ожидаемый scoring.source")
    p.add_argument("--model", default="rules-0.3", help="ожидаемый scoring.modelVersion")
    p.add_argument("--password", default="Demo123!")
    p.add_argument("--timeout", type=float, default=90, help="сколько ждать каждого перехода статуса, с")
    p.add_argument("--report", help="записать отчёт в Markdown")
    p.add_argument("--json", help="записать результаты в JSON")
    args = p.parse_args(argv)

    api = Api(args.api, args.password, args.timeout)
    versions = collect_versions(api, args)
    if versions["api_ready"]["http"] != 200:
        print(f"API {args.api} не готов: {versions['api_ready']}")
        return 1
    manager = api.login("manager")
    results = [dict(run_scenario(api, name, demo_id, manager, args), required=required) for name, demo_id, required in SCENARIOS]

    text = report_md(versions, results)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"versions": versions, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r["scenario"] for r in results if not r["ok"]]
    print("Все проверки прошли" if not failed else f"Не прошли сценарии: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
