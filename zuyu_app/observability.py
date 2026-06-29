from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings


LOGGER_NAME = "zuyu"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure_logging(settings: Settings) -> logging.Logger:
    logger = get_logger()
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, settings.log_level))
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **payload: Any) -> None:
    message = {"event": event, **payload}
    logger.info(json.dumps(message, ensure_ascii=False, default=str))


async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    logger = get_logger()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            logger,
            "request.error",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
            error=str(exc),
        )
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = str(duration_ms)
    log_event(
        logger,
        "request.complete",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    return response


def error_payload(message: str, request: Request, *, code: str, status_code: int, details: Any | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder({
            "error": {"code": code, "message": message, "details": details},
            "request_id": getattr(request.state, "request_id", None),
        }),
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return error_payload(str(exc.detail), request, code="http_error", status_code=exc.status_code)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_payload(
        "Request validation failed",
        request,
        code="validation_error",
        status_code=422,
        details=exc.errors(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger = get_logger()
    log_event(
        logger,
        "server.exception",
        request_id=getattr(request.state, "request_id", None),
        method=request.method,
        path=request.url.path,
        error=repr(exc),
    )
    return error_payload("Unexpected server error", request, code="server_error", status_code=500)
