import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from backend.auth.deps import (
    client_ip, clear_session_cookie, login_limiter, require_user, session_token, set_session_cookie,
)
from backend.auth.passwords import MAX_PASSWORD_LENGTH, normalize_username
from backend.auth.service import (
    ActiveSession, authenticate, change_password, create_session, revoke_session,
)
from backend.db.database import get_db
from backend.i18n import tr

logger = logging.getLogger("backend.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserInfo(BaseModel):
    username: str
    role: str


class SessionInfo(BaseModel):
    user: UserInfo
    csrf_token: str


def _session_info(active_user, csrf_token: str) -> dict:
    return {"user": {"username": active_user.username, "role": active_user.role}, "csrf_token": csrf_token}


@router.post("/login", response_model=SessionInfo)
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    ip = client_ip(request)
    username = normalize_username(body.username)
    wait = login_limiter.retry_after(ip, username)
    if wait:
        raise HTTPException(status_code=429, detail=tr(f"Troppi tentativi. Riprova tra {max(1, round(wait / 60))} minuti.",
                                  f"Too many attempts. Try again in {max(1, round(wait / 60))} minutes."),
                            headers={"Retry-After": str(wait)})

    user = await authenticate(db, username, body.password)
    if user is None:
        login_limiter.record_failure(ip, username)
        logger.warning("Failed login for %r from %s", username, ip)
        # Same message whether the username exists or not
        raise HTTPException(status_code=401, detail=tr("Username o password non corretti.", "Wrong username or password."))

    login_limiter.reset(ip, username)
    await revoke_session(db, session_token(request))  # never reuse a pre-login session
    token, session = await create_session(db, user, ip, request.headers.get("user-agent"))
    set_session_cookie(response, request, token)
    logger.info("Login: %s from %s", user.username, ip)
    return _session_info(user, session.csrf_token)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db),
                 _active: ActiveSession = Depends(require_user)):
    await revoke_session(db, session_token(request))
    clear_session_cookie(response, request)
    response.status_code = 204
    return response


@router.get("/me", response_model=SessionInfo)
async def me(active: ActiveSession = Depends(require_user)):
    return _session_info(active.user, active.session.csrf_token)


@router.post("/password", status_code=204)
async def update_password(body: PasswordChangeRequest, response: Response, db: AsyncSession = Depends(get_db),
                          active: ActiveSession = Depends(require_user)):
    """Changes the password and ends every other session of this user."""
    try:
        await change_password(db, active.user, body.current_password, body.new_password,
                              keep_token_hash=active.session.token_hash)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    response.status_code = 204
    return response
