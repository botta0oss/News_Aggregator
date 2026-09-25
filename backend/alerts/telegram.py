"""Telegram notifications through the Bot API (sendMessage)."""
import html
import logging

import httpx

from backend.config import settings
from backend.i18n import tr

logger = logging.getLogger(__name__)

_transport = None  # tests replace it with an httpx.MockTransport


class TelegramError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)


def escape(text) -> str:
    return html.escape(str(text or ""), quote=False)


async def send_message(text: str, silent: bool = False) -> None:
    """Sends an HTML message to TELEGRAM_CHAT_ID. Raises TelegramError with a readable reason."""
    if not is_configured():
        raise TelegramError(tr("Telegram non è configurato: servono TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID", "Telegram is not configured: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are needed"))
    url = f"{settings.TELEGRAM_API_URL}/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": settings.TELEGRAM_CHAT_ID,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }
    try:
        async with httpx.AsyncClient(timeout=15, transport=_transport) as client:
            r = await client.post(url, json=payload)
    except httpx.HTTPError as e:
        raise TelegramError(tr(f"Telegram non raggiungibile: {e.__class__.__name__}", f"Telegram unreachable: {e.__class__.__name__}")) from None
    if r.status_code != 200:
        try:
            reason = r.json().get("description") or r.text
        except ValueError:
            reason = r.text
        # Never log or return the URL: it contains the bot token
        raise TelegramError(tr(f"Telegram ha rifiutato il messaggio ({r.status_code}): {reason[:200]}",
                              f"Telegram refused the message ({r.status_code}): {reason[:200]}"))
