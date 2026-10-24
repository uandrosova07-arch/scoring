from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.preview import preview
from app.rules import MODEL_VERSION, score
from app.schemas import PreviewRequest, PreviewResponse, ScoreRequest, ScoreResponse

app = FastAPI(
    title="Scoring service",
    version=MODEL_VERSION,
    description="Оценка кредитного риска по воспроизводимым правилам. "
    "Контракт: docs/scoring-contract.md в репозитории backend. Решение принимает backend (C#).",
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Формат ошибки из контракта: 400 и {"error":"validation","details":{поле: описание}}.
    details: dict[str, str] = {}
    for e in exc.errors():
        field = ".".join(str(p) for p in e["loc"] if p != "body") or "body"
        details.setdefault(field, e["msg"])
    return JSONResponse(status_code=400, content={"error": "validation", "details": details})


# /score оставлен как короткий псевдоним: тот же запрос и тот же ответ.
@app.post("/score", response_model=ScoreResponse, include_in_schema=False)
@app.post("/api/v1/score", response_model=ScoreResponse)
def post_score(req: ScoreRequest) -> ScoreResponse:
    return score(req)


# Подсказка для формы заявки до подачи. Не кредитное решение: без балла, грейда и PD (docs/preview.md).
@app.post("/api/v1/preview", response_model=PreviewResponse)
def post_preview(req: PreviewRequest) -> PreviewResponse:
    return preview(req)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model_version": MODEL_VERSION}
