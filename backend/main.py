import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Back-compat re-exports: tests (and older callers) reach shared deps through
# `main`. New code should import from `api.deps` / `services` directly.
from api.deps import _MAX_BODY_BYTES, _client_ip, _error, rate_limit
from api.routers import meta, profiles, risk, snapshot
from config import settings
from crypto import DecryptionError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Interactive API docs (/docs, /redoc, /openapi.json) are disabled in production
# deployments (Vercel sets VERCEL_ENV=production; or set ENVIRONMENT=production).
_IS_PROD = (
    os.getenv("VERCEL_ENV") == "production"
    or os.getenv("ENVIRONMENT", "").strip().lower() in {"prod", "production"}
)

app = FastAPI(
    title="Agile Comrade API",
    version="3.0.0",
    description="Multi-scrum-master SaaS API (profiles in Supabase, serverless on Vercel)",
    docs_url=None if _IS_PROD else "/docs",
    redoc_url=None if _IS_PROD else "/redoc",
    openapi_url=None if _IS_PROD else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > _MAX_BODY_BYTES:
        return _error("Request body too large", 413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    # Authenticated payloads must not be cached by shared/intermediary caches.
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return _error("Internal server error", 500)


@app.exception_handler(DecryptionError)
async def decryption_error_handler(_request: Request, _exc: DecryptionError) -> JSONResponse:
    logger.error("Stored credentials could not be decrypted")
    return _error(
        "Stored credentials can't be decrypted — reconnect this profile in Settings (re-enter the Jira API token).",
        409,
    )


app.include_router(meta.router)
app.include_router(profiles.router)
app.include_router(snapshot.router)
app.include_router(risk.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.port, reload=settings.debug)
