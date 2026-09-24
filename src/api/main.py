"""
FastAPI backend.

    uvicorn src.api.main:app --reload --port 8000
"""
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from config.settings import settings
from src.api.routes import admin, auth, chat, feedback
from src.utils.errors import AppError, RateLimitError
from src.utils.logger import get_logger, request_id_var

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm up embeddings + store so the first user does not wait for model loading
    import asyncio

    from src.rag.vector_store import get_primary_store
    try:
        store = await asyncio.to_thread(get_primary_store)
        log.info("vector store ready", extra={"store": store.name})
    except Exception as e:
        log.error("vector store warmup failed - will retry on first request", extra={"error": str(e)})
    yield


app = FastAPI(title=settings.APP_NAME, version="1.0.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(feedback.router)
app.include_router(admin.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = rid
    log.info("http", extra={"method": request.method, "path": request.url.path, "status": response.status_code,
                            "ms": round((time.perf_counter() - start) * 1000, 1)})
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    log.warning("app error", extra={"code": exc.code, "error": str(exc), "details": exc.details})
    headers = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(int(exc.details.get("retry_after", 1)) + 1)
    return JSONResponse(status_code=exc.status_code, headers=headers,
                        content={"error": exc.code, "message": exc.user_message, **exc.details})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"error": "invalid_request", "message": "Invalid request",
                                                  "details": [e["msg"] for e in exc.errors()][:5]})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": "internal_error", "message": "Something went wrong."})


@app.get("/health")
async def health():
    from src.rag.vector_store import _primary
    return {"status": "ok", "vector_store": getattr(_primary, "name", "not_loaded"),
            "llm_provider": settings.LLM_PROVIDER, "langsmith": settings.LANGSMITH_TRACING}
