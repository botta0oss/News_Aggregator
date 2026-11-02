"""Real wallet on Polymarket: the simulated portfolio's buys and sales, also sent as real orders.

The simulated portfolio stays the brain and the record: it decides what to buy, when to sell,
and keeps its own capital. With the real wallet switched on, each of its decisions is followed
by a real order on the Polymarket CLOB, signed with the wallet's key:

- a bet bought from the book (taker)  -> a real buy now (FAK: what is not filled at once is
  cancelled), at most at the maximum price of the evaluation;
- a maker limit order                 -> a real limit order at the same price, post-only, that
  expires with it (GTD); cancelled when the simulated one is cancelled;
- a sale                              -> a real sale of the shares actually held, now (FAK), not
  below the simulated sale price minus LIVE_SELL_SLIPPAGE.

Real sizes are the same share of capital as the simulated bet (stake / simulated equity), applied
to the wallet's USDC balance, and capped by the per-order, daily and open-positions limits.
The wallet (key, funder address, signature type) and the limits are set from the dashboard
(table live_wallet_config; the POLYMARKET_* / LIVE_* variables of .env are the defaults), and so
is the mode (off / manual: only the bets placed by hand / auto: every bet).
Winning shares of resolved markets are redeemed from Polymarket's site: this module does not
send on-chain transactions.

Nothing here may break the simulation: every error is recorded on the order row and logged.
"""
import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db.models import LiveOrder, LiveWalletConfig, Market, PaperBet, PaperOrder
from backend.i18n import tr

logger = logging.getLogger(__name__)

MODES = ("off", "manual", "auto")
OPEN_STATUSES = ("submitted", "live")                 # may still fill
DONE_WITH_SHARES = ("filled", "partial", "live", "submitted")
MIN_ORDER_USD = 1.0                                    # Polymarket refuses smaller marketable orders
DEFAULT_MIN_SHARES = 5.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Exchange client ----------

class ClobWallet:
    """Thin synchronous wrapper of py-clob-client with plain types in and out (called in a thread).
    Tests replace it with set_client()."""

    def __init__(self, cfg: "WalletConfig"):
        from py_clob_client.client import ClobClient
        self._funder = cfg.funder
        self._client = ClobClient(
            settings.POLYMARKET_CLOB_URL, chain_id=settings.POLYMARKET_CHAIN_ID, key=cfg.private_key,
            signature_type=cfg.signature_type, funder=cfg.funder or None,
        )
        self._client.set_api_creds(self._client.create_or_derive_api_creds())

    def address(self) -> str:
        return self._funder or self._client.get_address()

    def balance(self) -> float:
        """USDC available for trading."""
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams
        raw = self._client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        return float(raw.get("balance") or 0) / 1e6

    def place(self, token_id: str, action: str, price: float, shares: float, order_type: str,
              expiration: Optional[int] = None) -> dict:
        from py_clob_client.clob_types import OrderArgs, OrderType
        args = OrderArgs(token_id=token_id, price=price, size=shares, side=action)
        if expiration:
            args.expiration = expiration
        signed = self._client.create_order(args)
        kind = {"FAK": OrderType.FAK, "GTD": OrderType.GTD, "GTC": OrderType.GTC}[order_type]
        resp = self._client.post_order(signed, kind, post_only=order_type in ("GTD", "GTC"))
        if not resp or not resp.get("success", True) or not resp.get("orderID"):
            raise RuntimeError((resp or {}).get("errorMsg") or f"Order refused: {resp}")
        # Shares received on a buy are the taking amount, shares given on a sale the making amount
        amount = resp.get("takingAmount" if action == "BUY" else "makingAmount")
        filled = float(amount) if amount not in (None, "") and str(resp.get("status")).lower() == "matched" else 0.0
        return {"id": resp["orderID"], "status": str(resp.get("status") or "").lower(), "filled": filled}

    def order(self, exchange_id: str) -> dict:
        raw = self._client.get_order(exchange_id) or {}
        return {"status": str(raw.get("status") or "").lower(), "filled": float(raw.get("size_matched") or 0)}

    def cancel(self, exchange_id: str) -> None:
        self._client.cancel(exchange_id)

    def cancel_all(self) -> None:
        self._client.cancel_all()


# ---------- Configuration (set from the dashboard) ----------

@dataclass
class WalletConfig:
    private_key: Optional[str]
    funder: Optional[str]
    signature_type: int
    max_order: float
    max_daily: float
    max_open: float
    slippage: float
    key_source: Optional[str]      # "app" (saved from the dashboard), "env" (.env) or None


KEY_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
LIMITS = {  # field: (setting of .env used as default, lowest, highest)
    "max_order_usd": ("LIVE_MAX_ORDER_USD", 1.0, 1_000_000.0),
    "max_daily_usd": ("LIVE_MAX_DAILY_USD", 1.0, 10_000_000.0),
    "max_open_usd": ("LIVE_MAX_OPEN_USD", 1.0, 10_000_000.0),
    "sell_slippage": ("LIVE_SELL_SLIPPAGE", 0.0, 0.5),
}


async def load_config(db: AsyncSession) -> WalletConfig:
    """The dashboard's values, falling back to .env for those never set."""
    row = await db.get(LiveWalletConfig, 1)

    def pick(field, env):
        value = getattr(row, field, None) if row is not None else None
        return value if value not in (None, "") else getattr(settings, env)
    key = row.private_key if row is not None and row.private_key else None
    return WalletConfig(
        private_key=key or settings.POLYMARKET_PRIVATE_KEY,
        funder=pick("funder", "POLYMARKET_FUNDER"),
        signature_type=int(pick("signature_type", "POLYMARKET_SIGNATURE_TYPE")),
        max_order=float(pick("max_order_usd", "LIVE_MAX_ORDER_USD")),
        max_daily=float(pick("max_daily_usd", "LIVE_MAX_DAILY_USD")),
        max_open=float(pick("max_open_usd", "LIVE_MAX_OPEN_USD")),
        slippage=float(pick("sell_slippage", "LIVE_SELL_SLIPPAGE")),
        key_source="app" if key else ("env" if settings.POLYMARKET_PRIVATE_KEY else None),
    )


async def save_config(db: AsyncSession, *, private_key: Optional[str] = None, clear_key: bool = False,
                      funder: Optional[str] = None, signature_type: Optional[int] = None, **limits) -> WalletConfig:
    """Saves the wallet and the limits from the dashboard. The key is write-only: it is never sent
    back. Changing the wallet is refused while real orders are on (the open positions are of the old one)."""
    from backend.betting.portfolio import get_settings
    row = await db.get(LiveWalletConfig, 1)
    if row is None:
        row = LiveWalletConfig(id=1)
        db.add(row)
    wallet_change = clear_key or (private_key not in (None, "")) or funder is not None or signature_type is not None
    if wallet_change:
        current = await load_config(db)
        changes = (clear_key and current.private_key) or (private_key not in (None, "")) \
            or (funder is not None and (funder.strip() or None) != current.funder) \
            or (signature_type is not None and signature_type != current.signature_type)
        if changes and (await get_settings(db)).live_mode != "off":
            raise ValueError(tr("Spegni il portafoglio reale prima di cambiare chiave, indirizzo o tipo di firma.",
                                "Turn the real wallet off before changing key, address or signature type."))
    if private_key not in (None, ""):
        key = private_key.strip()
        if not KEY_RE.match(key):
            raise ValueError(tr("Chiave privata non valida: servono 64 caratteri esadecimali (con o senza 0x).",
                                "Invalid private key: 64 hexadecimal characters are needed (with or without 0x)."))
        row.private_key = key if key.startswith("0x") else "0x" + key
    elif clear_key:
        row.private_key = None
    if funder is not None:
        funder = funder.strip()
        if funder and not ADDRESS_RE.match(funder):
            raise ValueError(tr("Indirizzo non valido: 0x seguito da 40 caratteri esadecimali.",
                                "Invalid address: 0x followed by 40 hexadecimal characters."))
        row.funder = funder or None
    if signature_type is not None:
        if signature_type not in (0, 1, 2):
            raise ValueError(tr("Tipo di firma non valido", "Invalid signature type"))
        row.signature_type = signature_type
    for field, value in limits.items():
        if field not in LIMITS or value is None:
            continue
        _, low, high = LIMITS[field]
        if not (low <= float(value) <= high):
            raise ValueError(tr(f"Valore fuori intervallo per {field}: tra {low:g} e {high:g}",
                                f"Value out of range for {field}: between {low:g} and {high:g}"))
        setattr(row, field, float(value))
    row.updated_at = _now()
    await db.commit()
    if wallet_change:
        _forget_client()
    return await load_config(db)


# ---------- Connection ----------

_client = None
_client_for: Optional[tuple] = None   # (key, funder, signature type) the client was made with
_client_error: Optional[str] = None
_injected = False


def set_client(client) -> None:
    """Tests: a fake wallet with the methods of ClobWallet (None: back to the real one)."""
    global _client, _client_error, _injected, _client_for
    _client, _client_error, _injected, _client_for = client, None, client is not None, None


def _forget_client() -> None:
    global _client, _client_error, _client_for
    if not _injected:
        _client, _client_error, _client_for = None, None, None


def library_installed() -> bool:
    try:
        import py_clob_client  # noqa: F401
        return True
    except ImportError:
        return False


def unavailable_reason(cfg: WalletConfig) -> Optional[str]:
    if _injected:
        return None
    if not cfg.private_key:
        return tr("Manca la chiave privata del portafoglio: inseriscila qui sotto, nelle impostazioni del portafoglio.",
                  "The wallet's private key is missing: enter it below, in the wallet settings.")
    if not library_installed():
        return tr("Manca il pacchetto py-clob-client sul server: pip install -r requirements-live.txt, poi riavvia.",
                  "The py-clob-client package is missing on the server: pip install -r requirements-live.txt, then restart.")
    return None


async def wallet(db: AsyncSession):
    """The connected wallet, made at first use and again when its settings change; None when not
    configured or unreachable (the reason is in _client_error)."""
    global _client, _client_error, _client_for
    if _injected:
        return _client
    cfg = await load_config(db)
    if unavailable_reason(cfg) is not None:
        return None
    sig = (cfg.private_key, cfg.funder, cfg.signature_type)
    if _client is not None and _client_for == sig:
        return _client
    try:
        _client = await asyncio.to_thread(ClobWallet, cfg)
        _client_for, _client_error = sig, None
    except Exception as e:
        _client, _client_for, _client_error = None, None, str(e)
        logger.error(f"Real wallet: connection failed: {e}")
        return None
    return _client


async def _call(fn, *args):
    return await asyncio.to_thread(fn, *args)


# ---------- Mode ----------

async def mode(db: AsyncSession) -> str:
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    return s.live_mode if s.live_mode in MODES else "off"


async def set_mode(db: AsyncSession, new_mode: str) -> str:
    from backend.betting.portfolio import get_settings
    if new_mode not in MODES:
        raise ValueError(tr("Modalità non valida", "Invalid mode"))
    if new_mode != "off":
        reason = unavailable_reason(await load_config(db))
        if reason:
            raise ValueError(reason)
        if await wallet(db) is None:
            raise ValueError(tr(f"Portafoglio non raggiungibile: {_client_error}", f"Wallet unreachable: {_client_error}"))
    s = await get_settings(db)
    if new_mode != "off" and s.live_mode == "off":
        s.live_since = _now()
    s.live_mode = new_mode
    await db.commit()
    if new_mode == "off":
        await cancel_resting(db, tr("Portafoglio reale disattivato", "Real wallet turned off"))
    return new_mode


async def stop(db: AsyncSession) -> dict:
    """Emergency stop: mode off and every order of the wallet cancelled on the exchange, including
    those placed from elsewhere. Shares already bought stay in the wallet."""
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    s.live_mode = "off"
    await db.commit()
    cancelled = await cancel_resting(db, tr("Arresto di emergenza", "Emergency stop"))
    client = await wallet(db)
    if client is not None:
        try:
            await _call(client.cancel_all)
        except Exception as e:
            logger.error(f"Real wallet: cancel all failed: {e}")
    return {"mode": "off", "cancelled": cancelled}


async def follows(db: AsyncSession, placed_by: str) -> bool:
    m = await mode(db)
    return m == "auto" or (m == "manual" and placed_by == "manual")


# ---------- Limits ----------

def _round_down(x: float, step: float) -> float:
    return math.floor(round(x / step, 6)) * step


def _round_up(x: float, step: float) -> float:
    return math.ceil(round(x / step, 6)) * step


def _tick() -> float:
    return settings.MAKER_TICK or 0.01


async def _rows(db: AsyncSession) -> list:
    return list((await db.execute(
        select(LiveOrder, PaperBet).outerjoin(PaperBet, PaperBet.id == LiveOrder.paper_bet_id)
        .where(LiveOrder.status.not_in(("failed", "blocked"))))).all())


def _group_key(o: LiveOrder):
    return o.paper_bet_id or o.paper_order_id or o.id


async def positions(db: AsyncSession) -> list[dict]:
    """Real shares held, per simulated bet (or per limit order not yet turned into one)."""
    groups: dict = {}
    for o, bet in await _rows(db):
        g = groups.setdefault(_group_key(o), {"market_id": o.market_id, "side": o.side, "bought": 0.0, "cost": 0.0,
                                                "sold": 0.0, "proceeds": 0.0, "resting": 0.0, "bet": None})
        if bet is not None:
            g["bet"] = bet
        if o.action == "BUY":
            g["bought"] += o.filled_shares or 0.0
            g["cost"] += (o.filled_shares or 0.0) * o.price
            if o.status in OPEN_STATUSES:
                g["resting"] += max(0.0, o.shares - (o.filled_shares or 0.0)) * o.price
        else:
            g["sold"] += o.filled_shares or 0.0
            g["proceeds"] += (o.filled_shares or 0.0) * o.price
    out = []
    for key, g in groups.items():
        held = max(0.0, g["bought"] - g["sold"])
        avg = g["cost"] / g["bought"] if g["bought"] else None
        bet = g["bet"]
        out.append({
            "key": str(key), "market_id": g["market_id"], "side": g["side"], "shares": held, "avg_price": avg,
            "cost": held * avg if avg is not None else 0.0, "resting": g["resting"], "proceeds": g["proceeds"],
            "paper_status": bet.status if bet is not None else None, "paper_bet_id": bet.id if bet is not None else None,
            # Shares of a resolved market are redeemed on Polymarket's site
            "to_redeem": held > 1e-6 and bet is not None and bet.status in ("won", "void"),
        })
    return out


async def exposure(db: AsyncSession) -> dict:
    """Money in open real positions and waiting buys, and real buys sent today (UTC)."""
    open_usd = sum(p["cost"] + p["resting"] for p in await positions(db)
                   if p["paper_status"] in (None, "open"))
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = (await db.execute(select(LiveOrder).where(
        LiveOrder.action == "BUY", LiveOrder.created_at >= midnight,
        LiveOrder.status.not_in(("failed", "blocked"))))).scalars().all()
    # Unfilled cancelled buys spent nothing; the others count for what they asked
    spent = sum(o.usd if o.status in OPEN_STATUSES else (o.filled_shares or 0.0) * o.price for o in today)
    return {"open": open_usd, "today": spent}


async def size_buy(db: AsyncSession, market: Market, price: float, paper_outlay: float) -> tuple[float, Optional[str]]:
    """Shares of the real buy: the simulated bet's share of capital applied to the wallet's balance,
    within the limits. (0, reason) when nothing can be bought."""
    from backend.betting.portfolio import ledger
    cfg = await load_config(db)
    client = await wallet(db)
    if client is None:
        return 0.0, unavailable_reason(cfg) or tr(f"Portafoglio non raggiungibile: {_client_error}", f"Wallet unreachable: {_client_error}")
    balance = await _call(client.balance)
    equity = (await ledger(db))["equity"]
    fraction = paper_outlay / equity if equity > 0 else 0.0
    exp = await exposure(db)
    caps = {
        "balance": balance * fraction,
        "order": cfg.max_order,
        "daily": cfg.max_daily - exp["today"],
        "open": cfg.max_open - exp["open"],
        "cash": balance,
    }
    usd = min(caps.values())
    shares = _round_down(max(0.0, usd) / price, 0.01) if price > 0 else 0.0
    min_shares = market.order_min_size or DEFAULT_MIN_SHARES
    if shares < min_shares or shares * price < MIN_ORDER_USD:
        limit = min(caps, key=caps.get)
        why = {
            "balance": tr(f"la stessa quota del capitale sul saldo reale ({balance:.2f} $) vale {caps['balance']:.2f} $",
                          f"the same share of capital on the real balance ({balance:.2f} $) is {caps['balance']:.2f} $"),
            "order": tr(f"limite per ordine ({cfg.max_order:.2f} $)", f"per-order limit ({cfg.max_order:.2f} $)"),
            "daily": tr(f"limite giornaliero raggiunto ({cfg.max_daily:.2f} $)", f"daily limit reached ({cfg.max_daily:.2f} $)"),
            "open": tr(f"limite sulle posizioni aperte raggiunto ({cfg.max_open:.2f} $)", f"open-positions limit reached ({cfg.max_open:.2f} $)"),
            "cash": tr(f"saldo insufficiente ({balance:.2f} $)", f"balance too low ({balance:.2f} $)"),
        }[limit]
        return 0.0, tr(f"Ordine reale troppo piccolo (minimo {min_shares:g} quote e {MIN_ORDER_USD:g} $): {why}",
                       f"Real order too small (minimum {min_shares:g} shares and {MIN_ORDER_USD:g} $): {why}")
    return shares, None


# ---------- Orders ----------

def _token(market: Market, side: str) -> Optional[str]:
    return market.yes_token_id if side == "YES" else market.no_token_id


async def _send(db: AsyncSession, row: LiveOrder, expiration: Optional[int] = None) -> LiveOrder:
    db.add(row)
    client = await wallet(db)
    try:
        if client is None:
            raise RuntimeError(unavailable_reason(await load_config(db)) or _client_error or "wallet unavailable")
        res = await _call(client.place, row.token_id, row.action, row.price, row.shares, row.order_type, expiration)
        row.exchange_id = res["id"]
        row.filled_shares = min(row.shares, res.get("filled") or 0.0)
        status = res.get("status") or ""
        if status == "live":
            row.status = "live"
        elif row.filled_shares >= row.shares - 1e-6:
            row.status = "filled"
        elif row.order_type == "FAK":
            row.status = "partial" if row.filled_shares > 0 else "cancelled"
        else:
            row.status = "submitted"
        logger.info(f"Real {row.action} {row.side} {row.shares:.2f} @ {row.price:.3f} on {row.market_id}: {row.status}")
    except Exception as e:
        row.status, row.error = "failed", str(e)[:500]
        logger.error(f"Real {row.action} on {row.market_id} failed: {e}")
    row.updated_at = _now()
    await db.commit()
    return row


async def _blocked(db: AsyncSession, row: LiveOrder, reason: str) -> LiveOrder:
    row.status, row.error, row.updated_at = "blocked", reason, _now()
    db.add(row)
    await db.commit()
    logger.info(f"Real {row.action} on {row.market_id} not sent: {reason}")
    return row


async def follow_bet(db: AsyncSession, market: Market, bet: PaperBet, max_price: float) -> Optional[LiveOrder]:
    """A simulated bet taken from the book: the real buy, now, at most at `max_price`."""
    try:
        if not await follows(db, bet.placed_by):
            return None
        price = round(min(1 - _tick(), _round_down(max_price, _tick())), 4)
        row = LiveOrder(market_id=market.id, token_id=_token(market, bet.side), side=bet.side, action="BUY",
                        order_type="FAK", price=price, shares=0.0, placed_by=bet.placed_by, paper_bet_id=bet.id)
        if not row.token_id:
            return await _blocked(db, row, tr("Il mercato non ha l'identificativo del token", "The market has no token id"))
        shares, why = await size_buy(db, market, price, bet.stake + (bet.fee or 0.0))
        row.shares = shares
        if why:
            return await _blocked(db, row, why)
        return await _send(db, row)
    except Exception as e:
        logger.error(f"Real wallet: following bet {bet.id} failed: {e}")
        await db.rollback()
        return None


async def follow_order(db: AsyncSession, market: Market, order: PaperOrder) -> Optional[LiveOrder]:
    """A simulated maker limit order: the same real limit order, post-only, expiring with it."""
    try:
        if not await follows(db, order.placed_by):
            return None
        row = LiveOrder(market_id=market.id, token_id=_token(market, order.side), side=order.side, action="BUY",
                        order_type="GTD", price=order.limit_price, shares=0.0, placed_by=order.placed_by,
                        paper_order_id=order.id)
        if not row.token_id:
            return await _blocked(db, row, tr("Il mercato non ha l'identificativo del token", "The market has no token id"))
        shares, why = await size_buy(db, market, order.limit_price, order.outlay)
        row.shares = shares
        if why:
            return await _blocked(db, row, why)
        # The CLOB applies a one-minute safety margin to GTD expirations
        return await _send(db, row, expiration=int(order.expires_at.timestamp()) + 60)
    except Exception as e:
        logger.error(f"Real wallet: following order {order.id} failed: {e}")
        await db.rollback()
        return None


async def link_fill(db: AsyncSession, order: PaperOrder, bet: PaperBet) -> None:
    """The simulated limit order became a bet: its real order now belongs to that bet (no commit)."""
    for row in (await db.execute(select(LiveOrder).where(LiveOrder.paper_order_id == order.id))).scalars().all():
        row.paper_bet_id = bet.id


async def _cancel_row(db: AsyncSession, row: LiveOrder, reason: str) -> None:
    client = await wallet(db)
    if client is not None and row.exchange_id:
        try:
            await _call(client.cancel, row.exchange_id)
        except Exception as e:
            logger.error(f"Real wallet: cancel of {row.exchange_id} failed: {e}")
            row.error = str(e)[:500]
            return  # still live as far as we know: the next sync looks again
        try:  # shares filled before the cancel
            info = await _call(client.order, row.exchange_id)
            row.filled_shares = max(row.filled_shares or 0.0, min(row.shares, info.get("filled") or 0.0))
        except Exception as e:
            logger.info(f"Real wallet: status of {row.exchange_id} unavailable after cancel: {e}")
    row.status = "partial" if (row.filled_shares or 0) > 0 else "cancelled"
    row.error = row.error or reason
    row.updated_at = _now()


async def cancel_for_order(db: AsyncSession, order: PaperOrder, reason: str) -> int:
    """The simulated limit order was cancelled: so is its real one (filled shares stay)."""
    try:
        rows = (await db.execute(select(LiveOrder).where(
            LiveOrder.paper_order_id == order.id, LiveOrder.status.in_(OPEN_STATUSES)))).scalars().all()
        for row in rows:
            await _cancel_row(db, row, reason)
        if rows:
            await db.commit()
        return len(rows)
    except Exception as e:
        logger.error(f"Real wallet: cancelling for order {order.id} failed: {e}")
        await db.rollback()
        return 0


async def cancel_resting(db: AsyncSession, reason: str) -> int:
    rows = (await db.execute(select(LiveOrder).where(LiveOrder.status.in_(OPEN_STATUSES)))).scalars().all()
    for row in rows:
        await _cancel_row(db, row, reason)
    if rows:
        await db.commit()
    return len(rows)


async def held_shares(db: AsyncSession, bet: PaperBet) -> float:
    for p in await positions(db):
        if p["paper_bet_id"] == bet.id:
            return p["shares"]
    return 0.0


async def follow_sale(db: AsyncSession, market: Market, bet: PaperBet, min_price: float) -> Optional[LiveOrder]:
    """The simulated bet was sold: cancel its waiting real buys and sell the real shares held, now,
    not below the simulated sale price minus LIVE_SELL_SLIPPAGE (nor below `min_price`). Sales follow
    whatever the mode, except off: shares bought for real must not be left behind by the simulation."""
    try:
        if await mode(db) == "off":
            return None
        resting = (await db.execute(select(LiveOrder).where(
            LiveOrder.paper_bet_id == bet.id, LiveOrder.status.in_(OPEN_STATUSES)))).scalars().all()
        for row in resting:
            await _cancel_row(db, row, tr("Scommessa venduta", "Bet sold"))
        await db.commit()
        shares = _round_down(await held_shares(db, bet), 0.01)
        if shares <= 0:
            return None
        slippage = (await load_config(db)).slippage
        floor = max(min_price, (bet.exit_price or min_price) - slippage, _tick())
        row = LiveOrder(market_id=market.id, token_id=_token(market, bet.side), side=bet.side, action="SELL",
                        order_type="FAK", price=round(min(1 - _tick(), _round_up(floor, _tick())), 4), shares=shares,
                        placed_by=bet.placed_by, paper_bet_id=bet.id)
        return await _send(db, row)
    except Exception as e:
        logger.error(f"Real wallet: following sale of {bet.id} failed: {e}")
        await db.rollback()
        return None


async def sync(db: AsyncSession) -> dict:
    """After each market sync: reads the fills of the waiting real orders, and cancels those whose
    simulated order was cancelled or expired."""
    stats = {"updated": 0, "cancelled": 0}
    rows = (await db.execute(
        select(LiveOrder, PaperOrder).outerjoin(PaperOrder, PaperOrder.id == LiveOrder.paper_order_id)
        .where(LiveOrder.status.in_(OPEN_STATUSES)))).all()
    if not rows:
        return stats
    client = await wallet(db)
    if client is None:
        return stats
    for row, paper in rows:
        try:
            if paper is not None and paper.status in ("cancelled", "expired") and row.exchange_id:
                await _cancel_row(db, row, paper.reason or tr("Ordine simulato chiuso", "Simulated order closed"))
                stats["cancelled"] += 1
                continue
            if not row.exchange_id:
                continue
            info = await _call(client.order, row.exchange_id)
            row.filled_shares = max(row.filled_shares or 0.0, min(row.shares, info.get("filled") or 0.0))
            status = info.get("status") or ""
            if row.filled_shares >= row.shares - 1e-6:
                row.status = "filled"
            elif status.startswith("cancel") or status in ("expired", "invalid", "unmatched"):
                row.status = "partial" if row.filled_shares > 0 else "cancelled"
            elif status == "live":
                row.status = "live"
            row.updated_at = _now()
            stats["updated"] += 1
        except Exception as e:
            logger.info(f"Real wallet: status of {row.exchange_id} unavailable: {e}")
    await db.commit()
    return stats


# ---------- Report ----------

def order_dict(o: LiveOrder, market: Optional[Market] = None) -> dict:
    d = {k: getattr(o, k) for k in ("id", "market_id", "side", "action", "order_type", "price", "shares", "filled_shares",
                                    "status", "exchange_id", "error", "placed_by", "paper_bet_id", "paper_order_id",
                                    "created_at", "updated_at")}
    d["usd"] = o.usd
    if market is not None:
        d["question"] = market.question
    return d


async def status(db: AsyncSession, limit: int = 100) -> dict:
    from backend.betting.portfolio import get_settings
    s = await get_settings(db)
    cfg = await load_config(db)
    out = {
        "library": library_installed() or _injected,
        "unavailable": unavailable_reason(cfg),
        "mode": s.live_mode if s.live_mode in MODES else "off",
        "since": s.live_since,
        "limits": {"order": cfg.max_order, "daily": cfg.max_daily, "open": cfg.max_open, "slippage": cfg.slippage},
        # The key itself never leaves the server
        "config": {"has_key": bool(cfg.private_key), "key_source": cfg.key_source, "funder": cfg.funder,
                   "signature_type": cfg.signature_type},
        "address": None, "balance": None, "error": None,
    }
    if out["unavailable"] is None:
        client = await wallet(db)
        if client is None:
            out["error"] = _client_error
        else:
            try:
                out["address"] = await _call(client.address)
                out["balance"] = await _call(client.balance)
            except Exception as e:
                out["error"] = str(e)
    out["exposure"] = await exposure(db)
    rows = (await db.execute(select(LiveOrder, Market).join(Market, Market.id == LiveOrder.market_id)
                             .order_by(LiveOrder.created_at.desc()).limit(limit))).all()
    out["orders"] = [order_dict(o, m) for o, m in rows]
    questions = {m.id: m.question for _, m in rows}
    pos = [p for p in await positions(db) if p["shares"] > 1e-6 or p["resting"] > 0]
    for p in pos:
        if p["market_id"] not in questions:
            m = await db.get(Market, p["market_id"])
            questions[p["market_id"]] = m.question if m else p["market_id"]
        p["question"] = questions[p["market_id"]]
    out["positions"] = pos
    return out
