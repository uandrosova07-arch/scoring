"""Дымовая проверка запущенного сервиса (локально или в контейнере).

    python scripts/smoke.py [адрес]        # по умолчанию http://localhost:8000

Проверяет /health, затем отправляет в POST /api/v1/score три примера из data/first_three.json
и сверяет балл, грейд и список недостающих данных с ожидаемым исходом.
Код выхода 0, если всё совпало, иначе 1. Только стандартная библиотека.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
SAMPLES = Path(__file__).resolve().parent.parent / "data" / "first_three.json"


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

    for s in json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]:
        status, body = call("/api/v1/score", s["input"])
        exp = s["expected"]
        got = (body.get("score"), body.get("risk_grade"), body.get("missing_data"))
        want = (exp["score"], exp["risk_grade"], exp["missing_data"])
        ok = status == 200 and got == want and body.get("request_id") == s["input"]["request_id"]
        print(f"{'OK  ' if ok else 'FAIL'}  POST /api/v1/score {s['id']:<11} -> {status} "
              f"score={body.get('score')} risk_grade={body.get('risk_grade')}")
        if not ok:
            print(f"      ожидалось {want}\n      получено  {got}")
        failed += not ok

    print("\nВсё в порядке" if not failed else f"\nОшибок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
