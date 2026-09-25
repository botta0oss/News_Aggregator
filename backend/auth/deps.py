"""FastAPI dependencies: authenticated user, admin role, CSRF and the session cookie."""
import hmac
from typing import Optional
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from backend.auth.ratelimit import LoginRateLimiter
from backend.auth.service import ActiveSession, resolve_session
from backend.config import settings
from backend.db.database import get_db

COOKIE_NAME = "nm_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}

login_limiter = LoginRateLimiter(
    max_per_user=settings.LOGIN_MAX_ATTEMPTS,
    max_per_ip=settings.LOGIN_MAX_ATTEMPTS_PER_IP,
    window_seconds=settings.LOGIN_WINDOW_MINUTES * 60,
)


def client_ip(request: Request) -> str:
    # Behind Cloudflare the proxy's own header is the reliable one (CLIENT_IP_HEADER); behind
    # another reverse proxy run uvicorn with --proxy-headers so request.client is the real client
    if settings.CLIENT_IP_HEADER:
        value = request.headers.get(settings.CLIENT_IP_HEADER, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else "unknown"


def _cookie_secure(request: Request) -> bool:
    mode = settings.SESSION_COOKIE_SECURE.strip().lower()
    if mode in ("true", "1", "yes"):
        return True
    if mode in ("false", "0", "no"):
        return False
    # auto: Secure everywhere except plain-HTTP local development
    return request.url.scheme == "https" or (request.url.hostname or "") not in LOCAL_HOSTS


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True, secure=_cookie_secure(request), samesite="lax", path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, secure=_cookie_secure(request), samesite="lax", path="/")


def session_token(request: Request) -> Optional[str]:
    return request.cookies.get(COOKIE_NAME)


async def require_user(request: Request, db: AsyncSession = Depends(get_db)) -> ActiveSession:
    """Any signed-in user. Unsafe methods must also carry the session's CSRF token."""
    active = await resolve_session(db, session_token(request))
    if active is None:
        raise HTTPException(status_code=401, detail="Accesso richiesto")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, active.session.csrf_token):
            raise HTTPException(status_code=403, detail="Token CSRF mancante o non valido. Ricarica la pagina.")
    request.state.auth = active
    return active


async def require_admin(active: ActiveSession = Depends(require_user)) -> ActiveSession:
    if active.user.role != "admin":
        raise HTTPException(status_code=403, detail="Serve un account amministratore per questa operazione.")
    return active
