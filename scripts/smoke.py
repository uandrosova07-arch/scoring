"""Дымовая проверка запущенного сервиса (локально или в контейнере).

    python scripts/smoke.py [адрес]        # по умолчанию http://localhost:8000

Проверяет /health и по model_version из него выбирает набор: для rules-0.3 — пять демонстрационных компаний из
data/demo_companies.json, для logreg-synthetic-0.1 — контрольные входы data/conformance/logreg-synthetic-0.1.json.
Сверяет балл, грейд и список недостающих данных (для logreg ещё PD с допуском 1e-9).
Код выхода 0, если всё совпало, иначе 1. Только стандартная библиотека.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
DATA = Path(__file__).resolve().parent.parent / "data"


def call(path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    failed = 0
    try:
        status, body = call("/health")
    except (urllib.error.URLError, OSError) as e:
        print(f"FAIL  {BASE}/health недоступен: {e}")
        return 1
    ok = status == 200 and body.get("status") == "ok"
    print(f"{'OK  ' if ok else 'FAIL'}  GET /health -> {status} {body}")
    failed += not ok

    version = body.get("model_version")
    if version == "rules-0.3":
        items = [(c["id"], c["input"], c["expected"])
                 for c in json.loads((DATA / "demo_companies.json").read_text(encoding="utf-8"))["companies"]]
    else:
        path = DATA / "conformance" / f"{version}.json"
        if not path.exists():
            print(f"FAIL  нет контрольных входов для model_version={version}")
            return 1
        items = [(c["id"], c["input"], c["expected"]) for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]

    for case_id, payload, exp in items:
        status, body = call("/api/v1/score", payload)
        got = (body.get("score"), body.get("risk_grade"), body.get("missing_data"))
        want = (exp["score"], exp["risk_grade"], exp["missing_data"])
        ok = (status == 200 and got == want and body.get("request_id") == payload["request_id"]
              and body.get("model_version") == version)
        if ok and version != "rules-0.3":
            ok = abs(body["probability_of_default"] - exp["probability_of_default"]) <= 1e-9
        print(f"{'OK  ' if ok else 'FAIL'}  POST /api/v1/score {case_id:<20} -> {status} "
              f"score={body.get('score')} risk_grade={body.get('risk_grade')} pd={body.get('probability_of_default')}")
        if not ok:
            print(f"      ожидалось {want}\n      получено  {got}")
        failed += not ok

    print("\nВсё в порядке" if not failed else f"\nОшибок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
