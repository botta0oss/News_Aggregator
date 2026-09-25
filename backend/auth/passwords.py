"""Argon2id password hashing (argon2-cffi defaults follow the RFC 9106 recommendations)."""
import re
from backend.i18n import tr
from functools import lru_cache
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # bounds hashing cost for oversized inputs
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

_hasher = PasswordHasher()


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def validate_username(username: str) -> str:
    username = normalize_username(username)
    if not USERNAME_RE.match(username):
        raise ValueError(tr("Lo username deve avere 3-32 caratteri: lettere, numeri, punto, trattino o underscore.", "The username must have 3-32 characters: letters, numbers, dot, hyphen or underscore."))
    return username


def validate_password(password: str, username: str = "") -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(tr(f"La password deve avere almeno {MIN_PASSWORD_LENGTH} caratteri.", f"The password must have at least {MIN_PASSWORD_LENGTH} characters."))
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(tr(f"La password può avere al massimo {MAX_PASSWORD_LENGTH} caratteri.", f"The password can have at most {MAX_PASSWORD_LENGTH} characters."))
    if username and username.lower() in password.lower():
        raise ValueError(tr("La password non può contenere lo username.", "The password cannot contain the username."))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _hasher.hash("dummy-password-for-timing")


def burn_verification_time(password: str) -> None:
    """Runs a verification against a dummy hash, so unknown usernames take as long as wrong passwords."""
    verify_password(_dummy_hash(), password)
