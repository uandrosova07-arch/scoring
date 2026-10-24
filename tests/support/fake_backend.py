"""Фейковый API backend для проверки scripts/e2e_backend.py без стенда.

Повторяет контракт из docs/api.md и docs/completion-flow.md backend (коммит 9f18dd4): вход, компании, подача заявки,
карточка заявки (клиент и менеджер видят разное), принятие предложения, договор, код подписания, подпись, выдача,
лента событий, /health/ready. Балл считает сервис scoring (режим External) или копия C# rules-0.3 (RulesFallback),
решение копия DecisionPolicy. Это модель API, а не backend: окончательная проверка на стенде (scripts/run_stand_checks.sh).
"""
from __future__ import annotations

import itertools
import json
import re
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import backend_reference as br  # noqa: E402

from app.rules import score as python_score  # noqa: E402
from app.schemas import ScoreRequest  # noqa: E402

AS_OF = date(2026, 10, 23)
SCENARIOS = {(s.login, s.product, int(s.amount), s.term): s for s in br.DEMO_SCENARIOS}
EXPLAIN = {
    "amount_reduced": "Банк готов выдать меньшую сумму: это 25 % вашей годовой выручки, комфортный уровень нагрузки.",
    "tax_debt": "Задолженность перед налоговой превышает 500 000 ₽.", "accounts_blocked": "Пока действует блокировка счетов, выдача невозможна.",
    "overdue_90": "В кредитной истории есть длительная просрочка.", "enforcement_debt": "Сумма исполнительных производств превышает 1 000 000 ₽.",
}


class FakeBackend:
    def __init__(self, mode: str = "External", fallback_agreed: bool = True, reveal_code: bool = True, contract_for_rejected: bool = False):
        self.mode, self.fallback_agreed, self.reveal_code, self.contract_for_rejected = mode, fallback_agreed, reveal_code, contract_for_rejected
        self.apps: dict[str, dict] = {}
        self.feed: list[dict] = []
        self.ids = itertools.count(1)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()

    # ---- логика заявки
    def create(self, login: str, body: dict) -> tuple[int, dict]:
        s = SCENARIOS.get((login, body["productCode"], int(body["amount"]), body["termMonths"]))
        if s is None:
            return 400, {"errors": {"productCode": ["нет такого демо-сценария в фейке"]}}
        if errors := br.validate_application(s, AS_OF):
            return 400, {"title": "Ошибка валидации", "errors": {k: [v] for k, v in errors.items()}}
        n = next(self.ids)
        app_id = f"00000000-0000-4000-a000-{n:012d}"
        req = br.to_json_value(br.build_request(s, f"00000000-0000-4000-b000-{n:012d}", app_id, f"AC-{n:06d}", "2026-10-23T09:00:00Z", AS_OF))
        if self.mode == "External":
            r = python_score(ScoreRequest.model_validate(req)).model_dump()
            warnings = r["warnings"]
        else:
            r = br.score_rules_v1(req, agreed=self.fallback_agreed)
            r["probability_of_default"] = float(r["probability_of_default"])
            warnings = r["warnings"] + ["external_scoring_unavailable"]
        d = br.decide(s, req["checks"], r["risk_grade"], r["score"], [f["code"] for f in r["factors"]])
        reasons = [{"code": c, "title": c, "explanation": EXPLAIN.get(c, "Пояснение для клиента.")} for c in d["reasons"]]
        approved = d["type"] in ("Approved", "ApprovedWithConditions")
        self.apps[app_id] = {
            "id": app_id, "number": f"AC-{n:06d}", "login": login, "term": s.term, "polls": 0, "state": d["type"],
            "scoring": {"score": r["score"], "grade": r["risk_grade"], "probabilityOfDefault": r["probability_of_default"],
                        "source": self.mode, "modelVersion": r["model_version"], "warnings": warnings},
            "decision": {"type": d["type"], "recommendedType": d["recommended_type"], "decidedBy": "Auto",
                         "approvedAmount": _n(d["amount"]), "approvedTermMonths": d["term_months"], "interestRate": _n(d["interest_rate"]),
                         "monthlyPayment": _n(d["monthly_payment"]),
                         "conditions": "сумма снижена" if "amount_reduced" in d["reasons"] else None, "reasons": reasons},
            "offer": {"amount": _n(d["amount"]), "termMonths": d["term_months"], "interestRate": _n(d["interest_rate"]),
                      "monthlyPayment": _n(d["monthly_payment"]), "response": "Pending"} if approved else None,
            "contract": None, "disbursement": None, "code": None,
        }
        return 201, {"id": app_id, "number": f"AC-{n:06d}", "status": "Submitted"}

    def view(self, app_id: str, manager: bool) -> dict:
        a = self.apps[app_id]
        a["polls"] += 1
        if a["polls"] <= 2:
            return {"id": app_id, "status": {"code": "Scoring"}, "decision": None, "offer": None, "availableActions": ["cancel"],
                    "contract": None, "disbursement": None, "scoring": None}
        if a["state"] == "Signed" and a["polls"] > a.get("signed_poll", 0) + 1:
            a["state"] = "Disbursed"
            a["contract"]["status"] = "Signed"
            a["disbursement"] = {"status": "Completed", "amount": a["contract"]["amount"], "paymentOrderNumber": f"PO-20261023-{a['polls']:04d}",
                                 "completedAt": "2026-10-23T09:02:13+00:00"}
        actions = {"Approved": ["acceptOffer", "declineOffer"], "ApprovedWithConditions": ["acceptOffer", "declineOffer"],
                   "ContractSigning": ["signContract", "cancel"], "ManualReview": ["cancel"]}.get(a["state"], [])
        return {"id": app_id, "number": a["number"], "status": {"code": a["state"]}, "decision": a["decision"], "offer": a["offer"],
                "availableActions": actions, "contract": a["contract"], "disbursement": a["disbursement"],
                "scoring": a["scoring"] if manager else None}

    def accept(self, app_id: str):
        a = self.apps[app_id]
        if a["state"] not in ("Approved", "ApprovedWithConditions") and not (self.contract_for_rejected and a["state"] == "Rejected"):
            return 409, {"title": "Предложение недоступно"}
        o = a["offer"] or {"amount": 1, "termMonths": a["term"], "interestRate": 1, "monthlyPayment": 1}
        a["offer"] = {**o, "response": "Accepted"}
        a["state"] = "ContractSigning"
        a["contract"] = {"number": f"KD-{app_id[-6:]}", "status": "AwaitingSignature", "amount": o["amount"], "termMonths": o["termMonths"],
                         "interestRate": o["interestRate"], "monthlyPayment": o["monthlyPayment"], "totalPayment": o["monthlyPayment"] * o["termMonths"]}
        return 200, self.view(app_id, False)

    def contract(self, app_id: str):
        c = self.apps[app_id]["contract"]
        if c is None:
            return 404, {"title": "Договор ещё не сформирован"}
        return 200, {**c, "schedule": [{"number": i + 1} for i in range(c["termMonths"])]}

    def request_code(self, app_id: str):
        a = self.apps[app_id]
        if a["contract"] is None:
            return 404, {"title": "Договор ещё не сформирован"}
        if a["state"] != "ContractSigning":
            return 409, {"title": "Подписание недоступно"}
        a["code"] = "483921"
        self.feed.append({"type": "SigningCode", "applicationId": app_id, "title": "Код подтверждения",
                          "body": f"Код подтверждения для подписания договора {a['contract']['number']}: 483921. Действует 10 минут."})
        return 200, {"contractNumber": a["contract"]["number"], "deliveredVia": "Feed", "attemptsLeft": 5,
                     "demoCode": "483921" if self.reveal_code else None}

    def sign(self, app_id: str, body: dict):
        a = self.apps[app_id]
        if a["state"] != "ContractSigning" or not a["code"]:
            return 409, {"title": "Подписание недоступно"}
        if body.get("code") != a["code"]:
            return 400, {"errors": {"code": ["Неверный код"]}}
        a["state"], a["signed_poll"] = "Signed", a["polls"]
        a["contract"]["status"] = "Signed"
        return 200, {**self.view(app_id, False), "status": {"code": "Signed"}}

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body):
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _token(self) -> str:
                return self.headers.get("Authorization", "")[7:]

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/health/ready":
                    return self._send(200, {"status": "Healthy", "checks": [{"name": "database", "status": "Healthy"},
                                                                          {"name": "scoring", "status": "Healthy" if fake.mode == "External" else "Degraded"}]})
                if path == "/health":
                    return self._send(200, {"status": "ok", "model_version": "rules-0.3"}) if fake.mode == "External" else self._send(503, {})
                if path == "/swagger/v1/swagger.json":
                    return self._send(200, {"info": {"title": "AlfaCredit API (fake)", "version": "v1"}})
                if path == "/api/companies":
                    return self._send(200, [{"id": self._token()}])
                if path == "/api/feed":
                    return self._send(200, {"items": fake.feed})
                if m := re.fullmatch(r"/api/applications/([\w-]+)/contract", path):
                    return self._send(*fake.contract(m.group(1)))
                if m := re.fullmatch(r"/api/applications/([\w-]+)", path):
                    return self._send(200, fake.view(m.group(1), self._token() == "manager"))
                self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                path = self.path
                if path == "/api/auth/login":
                    return self._send(200, {"accessToken": body["login"]})
                if path == "/api/applications":
                    return self._send(*fake.create(self._token(), body))
                if m := re.fullmatch(r"/api/applications/([\w-]+)/offer/accept", path):
                    return self._send(*fake.accept(m.group(1)))
                if m := re.fullmatch(r"/api/applications/([\w-]+)/contract/sign/request-code", path):
                    return self._send(*fake.request_code(m.group(1)))
                if m := re.fullmatch(r"/api/applications/([\w-]+)/contract/sign", path):
                    return self._send(*fake.sign(m.group(1), body))
                self._send(404, {})

        return Handler


def _n(x):
    return None if x is None else float(x)
