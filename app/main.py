import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import model, rules
from app.preview import preview
from app.schemas import PreviewRequest, PreviewResponse, ScoreRequest, ScoreResponse

# Какая модель отвечает на /api/v1/score. По умолчанию обученная логистическая регрессия; rules-0.3 оставлены,
# пока резервные правила backend считают по ним (см. docs/model.md, «Переход»).
MODELS = {model.DEFAULT_MODEL: lambda req: model.score(req, model.DEFAULT_MODEL), rules.MODEL_VERSION: rules.score}
DEFAULT = model.DEFAULT_MODEL


def create_app(model_version: str | None = None) -> FastAPI:
    version = model_version or os.environ.get("SCORING_MODEL") or DEFAULT
    if version not in MODELS:
        raise RuntimeError(f"SCORING_MODEL={version}: неизвестная модель, доступны {', '.join(MODELS)}")
    scorer = MODELS[version]
    if version == model.DEFAULT_MODEL:
        model.load(version)  # артефакт читается при старте: битый файл должен ронять запуск, а не запрос

    app = FastAPI(
        title="Scoring service",
        version=version,
        description="Оценка кредитного риска. Контракт: docs/scoring-contract.md в репозитории backend. Решение принимает backend (C#).",
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
        return scorer(req)

    # Подсказка для формы заявки до подачи. Не кредитное решение: без балла, грейда и PD (docs/preview.md).
    # Уровни нагрузки согласованы с backend по полосам rules-0.3 и от выбранной модели не зависят.
    @app.post("/api/v1/preview", response_model=PreviewResponse)
    def post_preview(req: PreviewRequest) -> PreviewResponse:
        return preview(req)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model_version": version}

    return app


app = create_app()
