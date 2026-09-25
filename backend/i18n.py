"""Language of the texts the API writes for people: plans, reasons, error messages, alerts.

A dashboard request carries X-Lang (it / en) and a middleware sets it for the whole request;
background work (alerts, Telegram, summaries) uses APP_LANGUAGE. Texts are written as
tr(italian, english) next to the code that builds them.
"""
from contextvars import ContextVar
from typing import Optional

from backend.config import settings

LANGS = ("it", "en")
_lang: ContextVar[Optional[str]] = ContextVar("lang", default=None)


def lang() -> str:
    current = _lang.get()
    if current in LANGS:
        return current
    return settings.APP_LANGUAGE if settings.APP_LANGUAGE in LANGS else "en"


def set_lang(value: Optional[str]):
    """Sets the language for the current context; returns the token for reset_lang."""
    value = (value or "").strip().lower()[:2]
    return _lang.set(value if value in LANGS else None)


def reset_lang(token) -> None:
    _lang.reset(token)


def tr(it: str, en: str) -> str:
    return it if lang() == "it" else en


def dec(x: float, digits: int = 1) -> str:
    """A number with the decimal separator of the language: 1,5 / 1.5."""
    text = f"{x:.{digits}f}"
    return text.replace(".", ",") if lang() == "it" else text


def dollars(x: float, digits: int = 2) -> str:
    """41,02 $ / $41.02"""
    return f"{dec(x, digits)} $" if lang() == "it" else f"${dec(x, digits)}"


def side(s: str) -> str:
    """YES / NO as shown to people."""
    return tr("SÌ", "YES") if s == "YES" else "NO"
