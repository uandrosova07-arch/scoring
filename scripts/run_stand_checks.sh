#!/usr/bin/env bash
# Сквозная проверка общего стенда в двух режимах: External (отвечает scoring) и RulesFallback (scoring остановлен).
#
#   BACKEND_DIR=../probiinik scripts/run_stand_checks.sh
#
# Стенд поднимается отдельным compose-проектом (по умолчанию alfacredit-e2e): своя база в томе alfacredit-e2e_db-data,
# свои контейнеры и порты (API 18080, scoring 18000, PostgreSQL 15432). Рабочий проект alfacredit и его том не трогаются.
# Перед прогоном и после него удаляется только том тестового проекта. Результаты: $E2E_OUT (отчёты, JSON, версии образов).
#
# Переменные: BACKEND_DIR (репозиторий backend), E2E_PROJECT, E2E_API_PORT, E2E_SCORING_PORT, E2E_POSTGRES_PORT,
# E2E_OUT, E2E_COMPOSE_OVERRIDE (доп. compose-файл, например docs/backend/compose.scoring.yml, если в backend ещё заглушка),
# KEEP=1 (не удалять тестовый стенд после прогона), DRY_RUN=1 (только показать команды).
set -euo pipefail

SCORING_DIR=$(cd "$(dirname "$0")/.." && pwd)
BACKEND_DIR=$(cd "${BACKEND_DIR:-$SCORING_DIR/../probiinik}" && pwd)
PROJECT=${E2E_PROJECT:-alfacredit-e2e}
OUT=${E2E_OUT:-$SCORING_DIR/e2e-results/$(date -u +%Y%m%dT%H%M%SZ)}
PYTHON=${PYTHON:-python3}

if [[ "$PROJECT" == "alfacredit" ]]; then
  echo "E2E_PROJECT=alfacredit это рабочий проект: его том был бы удалён. Выберите другое имя." >&2
  exit 2
fi

export API_PORT=${E2E_API_PORT:-18080}
export SCORING_PORT=${E2E_SCORING_PORT:-18000}
export POSTGRES_PORT=${E2E_POSTGRES_PORT:-15432}
export SCORING_MODE=External
export SIMULATORS_FAILURE_RATE=0
API="http://localhost:$API_PORT"
SCORING="http://localhost:$SCORING_PORT"

COMPOSE=(docker compose -p "$PROJECT" --project-directory "$BACKEND_DIR" -f "$BACKEND_DIR/docker-compose.yml")
if [[ -n "${E2E_COMPOSE_OVERRIDE:-}" ]]; then
  COMPOSE+=(-f "$E2E_COMPOSE_OVERRIDE")
fi

run() {
  echo "+ $*"
  if [[ -z "${DRY_RUN:-}" ]]; then "$@"; fi
}

wait_ready() {
  [[ -n "${DRY_RUN:-}" ]] && return 0
  for _ in $(seq 1 90); do
    if curl -fsS "$API/health/ready" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  echo "API $API не стал готов за 3 минуты" >&2
  return 1
}

cleanup() {
  if [[ -z "${KEEP:-}" ]]; then run "${COMPOSE[@]}" down -v --remove-orphans; fi
}

mkdir -p "$OUT"
echo "Проект: $PROJECT, backend: $BACKEND_DIR, результаты: $OUT"
run "${COMPOSE[@]}" down -v --remove-orphans            # чистая тестовая база
trap cleanup EXIT
run "${COMPOSE[@]}" up -d --build
wait_ready

# Версии компонентов: образы, их идентификаторы, версия правил сервиса scoring
if [[ -z "${DRY_RUN:-}" ]]; then
  "${COMPOSE[@]}" images > "$OUT/images.txt"
  "${COMPOSE[@]}" ps --format json > "$OUT/containers.json" || true
  curl -fsS "$SCORING/health" > "$OUT/scoring-health.json" || echo '{"error":"scoring /health недоступен"}' > "$OUT/scoring-health.json"
fi

status=0
run "$PYTHON" "$SCORING_DIR/scripts/e2e_backend.py" --api "$API" --scoring "$SCORING" --mode External \
  --report "$OUT/e2e-external.md" --json "$OUT/e2e-external.json" || status=1

# Откат: scoring остановлен, backend считает резервными правилами (RulesFallback, ожидается та же версия rules-0.3)
run "${COMPOSE[@]}" stop scoring
run "$PYTHON" "$SCORING_DIR/scripts/e2e_backend.py" --api "$API" --scoring "$SCORING" --mode RulesFallback \
  --report "$OUT/e2e-rulesfallback.md" --json "$OUT/e2e-rulesfallback.json" || status=1
run "${COMPOSE[@]}" start scoring

if [[ -z "${DRY_RUN:-}" ]]; then
  cat "$OUT/e2e-external.md" "$OUT/e2e-rulesfallback.md" > "$OUT/summary.md"
  echo "Итог: $OUT/summary.md"
fi
exit $status
