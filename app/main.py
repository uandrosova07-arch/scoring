from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.rules import MODEL_VERSION, score
from app.schemas import ScoreRequest, ScoreResponse

app = FastAPI(
    title="Scoring service",
    version=MODEL_VERSION,
    description="Оценка кредитного риска по воспроизводимым правилам. Решение принимает backend (C#).",
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": "Некорректные входные данные",
            "status": 422,
            "detail": "Запрос не прошёл проверку, расчёт не выполнялся",
            "errors": errors,
        },
    )


@app.post("/score", response_model=ScoreResponse)
def post_score(req: ScoreRequest) -> ScoreResponse:
    return score(req)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model_version": MODEL_VERSION}
