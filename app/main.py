from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.services.catia.service import CatiaError
from app.core.status_code import StatusCode

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info("Starting {} v{}", settings.app_name, settings.version)
    yield
    logger.info("Shutting down {} v{}", settings.app_name, settings.version)

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.include_router(api_router, prefix=f"{settings.api_prefix}/v1")

def _error_response(
    code: int, message: str, status_code: int, data: Any = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": data},
    )

@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(StatusCode.VALIDATION_ERROR, "request validation failed", 422, exc.errors())

@app.exception_handler(CatiaError)
async def catia_error_handler(request: Request, exc: CatiaError) -> JSONResponse:
    logger.error("CATIA error: {}", exc)
    return _error_response(StatusCode.CATIA_UNAVAILABLE, str(exc), 502)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _error_response(exc.status_code, str(exc.detail), exc.status_code)

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error")
    return _error_response(
        StatusCode.INTERNAL_ERROR, "internal server error", 500
    )


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {
        "message": settings.app_name,
        "docs": "/docs",
        "health": f"{settings.api_prefix}/v1/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)