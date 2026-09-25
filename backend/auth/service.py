"""Users and server-side sessions."""
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.auth.passwords import (
    burn_verification_time, hash_password, needs_rehash, normalize_username,
    validate_password, validate_username, verify_password,
)
from backend.config import settings
from backend.db.models import User, UserSession
from backend.i18n import tr

logger = logging.getLogger("backend.auth")

ROLES = ("admin", "viewer")
# last_seen_at is written at most this often, to avoid a DB write on every request
TOUCH_INTERVAL = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class ActiveSession:
    user: User
    session: UserSession


async def get_user(db: AsyncSession, username: str) -> Optional[User]:
    return (await db.execute(select(User).where(User.username == normalize_username(username)))).scalar_one_or_none()


async def create_user(db: AsyncSession, username: str, password: str, role: str = "viewer") -> User:
    username = validate_username(username)
    if role not in ROLES:
        raise ValueError(tr(f"Ruolo non valido: usa {' o '.join(ROLES)}.", f"Invalid role: use {' or '.join(ROLES)}."))
    validate_password(password, username)
    if await get_user(db, username):
        raise ValueError(tr(f"L'utente {username} esiste già.", f"User {username} already exists."))
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    logger.info("User created: %s (%s)", username, role)
    return user


async def authenticate(db: AsyncSession, username: str, password: str) -> Optional[User]:
    user = await get_user(db, username)
    if user is None or not user.is_active:
        burn_verification_time(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


async def create_session(db: AsyncSession, user: User, ip: Optional[str], user_agent: Optional[str]) -> tuple[str, UserSession]:
    """Returns the raw cookie token (never stored) and the new session row."""
    await purge_expired_sessions(db)
    token = secrets.token_urlsafe(32)
    now = _now()
    session = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.SESSION_TTL_HOURS),
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(session)
    user.last_login_at = now
    await db.commit()
    return token, session


async def resolve_session(db: AsyncSession, token: Optional[str]) -> Optional[ActiveSession]:
    """Validates a cookie token: unknown, expired, idle or disabled-user sessions are rejected."""
    if not token or len(token) > 200:
        return None
    row = (await db.execute(
        select(UserSession, User).join(User, User.id == UserSession.user_id).where(UserSession.token_hash == hash_token(token))
    )).first()
    if row is None:
        return None
    session, user = row
    now = _now()
    idle_limit = session.last_seen_at + timedelta(minutes=settings.SESSION_IDLE_MINUTES)
    if not user.is_active or session.expires_at <= now or idle_limit <= now:
        await db.delete(session)
        await db.commit()
        return None
    if now - session.last_seen_at >= TOUCH_INTERVAL:
        session.last_seen_at = now
        await db.commit()
    return ActiveSession(user=user, session=session)


async def revoke_session(db: AsyncSession, token: Optional[str]) -> None:
    if token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        await db.commit()


async def revoke_user_sessions(db: AsyncSession, user: User, keep_token_hash: Optional[str] = None) -> int:
    stmt = delete(UserSession).where(UserSession.user_id == user.id)
    if keep_token_hash:
        stmt = stmt.where(UserSession.token_hash != keep_token_hash)
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount or 0


async def change_password(db: AsyncSession, user: User, current: str, new: str, keep_token_hash: Optional[str] = None) -> None:
    if not verify_password(user.password_hash, current):
        raise PermissionError(tr("La password attuale non è corretta.", "The current password is not correct."))
    if current == new:
        raise ValueError(tr("La nuova password deve essere diversa da quella attuale.", "The new password must be different from the current one."))
    await set_password(db, user, new, keep_token_hash=keep_token_hash)


async def set_password(db: AsyncSession, user: User, new: str, keep_token_hash: Optional[str] = None) -> None:
    """Sets a new password and ends every other session of the user."""
    validate_password(new, user.username)
    user.password_hash = hash_password(new)
    user.password_changed_at = _now()
    await db.commit()
    await revoke_user_sessions(db, user, keep_token_hash=keep_token_hash)
    logger.info("Password changed for %s", user.username)


async def purge_expired_sessions(db: AsyncSession) -> None:
    now = _now()
    await db.execute(delete(UserSession).where(
        (UserSession.expires_at <= now) | (UserSession.last_seen_at <= now - timedelta(minutes=settings.SESSION_IDLE_MINUTES))
    ))


async def ensure_bootstrap_admin(db: AsyncSession) -> None:
    """Creates ADMIN_USERNAME/ADMIN_PASSWORD as admin, only while no user exists."""
    count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    if count:
        return
    if not (settings.ADMIN_USERNAME and settings.ADMIN_PASSWORD):
        logger.warning(
            "No dashboard user exists. Create one with: python -m backend.auth.cli create-user <username> --role admin "
            "(or set ADMIN_USERNAME and ADMIN_PASSWORD)"
        )
        return
    await create_user(db, settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD, role="admin")
    logger.warning("Bootstrap admin %s created: remove ADMIN_PASSWORD from the environment", settings.ADMIN_USERNAME)
