"""Error envelope: {"error": {"code", "message", "fields", "details"}}."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, fields: dict | None = None, details: dict | None = None):
        self.code, self.message, self.status = code, message, status
        self.fields, self.details = fields or {}, details or {}


def envelope(code: str, message: str, fields: dict | None = None, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "fields": fields or {}, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(envelope(exc.code, exc.message, exc.fields, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = {".".join(str(p) for p in e["loc"] if p != "body"): e["msg"] for e in exc.errors()}
        return JSONResponse(envelope("validation_error", "Some fields are not valid.", fields), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse(envelope(f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code)
