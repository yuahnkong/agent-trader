"""The hard limits. Every trade the Portfolio Manager approves passes through `review`,
and only the trades it returns as approved are sent to Alpaca.

Nothing here can be relaxed from the decision file: limits come from config, account
state comes from Alpaca, and the decision only supplies symbol, side and size.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import config


@dataclass
class Position:
    qty: float
    market_value: float
    unrealized_pl: float


@dataclass
class Context:
    today: date
    equity: float
    buying_power: float
    peak_equity: float
    paused: bool
    regime_above_sma: bool | None  # None = not enough history, treated as risk-off
    positions: dict[str, Position]
    menu_vol: dict[str, float]  # symbol -> 252-day vol, for symbols on the menu
    median_vol: float
    orders_today: list[dict]  # this account's orders submitted today: {symbol, side}
    last_loss_sale: dict[str, date] = field(default_factory=dict)
    last_buy: dict[str, date] = field(default_factory=dict)

    @property
    def drawdown(self) -> float:
        return 0.0 if self.peak_equity <= 0 else 1 - self.equity / self.peak_equity


@dataclass
class Verdict:
    symbol: str
    side: str
    requested_usd: float | None
    approved: bool
    reasons: list[str]
    notional: float | None = None  # buys
    qty: str | None = None  # sells
    resized: bool = False


def kill_switch_tripped(ctx: Context) -> bool:
    return ctx.drawdown >= config.KILL_SWITCH_DRAWDOWN


def max_position_usd(ctx: Context, symbol: str) -> float:
    """Inverse-volatility cap: a name at the menu's median vol gets 1/TARGET_POSITIONS of
    equity, calmer names more, jumpier names less, never above MAX_POSITION_PCT."""
    vol = ctx.menu_vol.get(symbol)
    base = 1 / config.TARGET_POSITIONS
    weight = base if not vol else base * ctx.median_vol / vol
    return ctx.equity * min(config.MAX_POSITION_PCT, weight)


def _fmt_qty(q: float) -> str:
    # Alpaca accepts up to 9 decimal places; trim trailing zeros.
    return f"{q:.9f}".rstrip("0").rstrip(".")


def review(trades: list[dict], ctx: Context) -> list[Verdict]:
    """Trades are {symbol, side: buy|sell, notional_usd (buy), fraction (sell, 0-1]}.
    Sells are judged first so a rotation's sell isn't crowded out by its buy."""
    out: list[Verdict] = []
    used = len(ctx.orders_today)
    bought_today = {o["symbol"] for o in ctx.orders_today if o["side"] == "buy"}
    sold_today = {o["symbol"] for o in ctx.orders_today if o["side"] == "sell"}
    cash = ctx.buying_power
    seen: set[tuple[str, str]] = set()

    ordered = sorted(trades, key=lambda t: 0 if str(t.get("side")).lower() == "sell" else 1)
    for t in ordered:
        sym = str(t.get("symbol", "")).upper().strip()
        side = str(t.get("side", "")).lower().strip()
        req = t.get("notional_usd")
        v = Verdict(sym, side, float(req) if isinstance(req, (int, float)) else None, False, [])
        out.append(v)

        if side not in ("buy", "sell"):
            v.reasons.append(f"unknown side {side!r}")
            continue
        if (sym, side) in seen:
            v.reasons.append("duplicate of an earlier trade in this decision")
            continue
        seen.add((sym, side))
        if ctx.paused:
            v.reasons.append("trading paused by kill switch; run `pipeline resume` to re-enable")
            continue
        if kill_switch_tripped(ctx):
            v.reasons.append(f"drawdown {ctx.drawdown:.1%} from peak trips the kill switch")
            continue
        if used >= config.MAX_TRADES_PER_DAY:
            v.reasons.append(f"already {used} trades today (max {config.MAX_TRADES_PER_DAY})")
            continue

        if side == "sell":
            pos = ctx.positions.get(sym)
            if pos is None or pos.qty <= 0:
                v.reasons.append("no position to sell (no shorting)")
                continue
            if sym in bought_today:
                v.reasons.append("bought today; same-day round trips are blocked")
                continue
            frac = t.get("fraction", 1.0)
            if not isinstance(frac, (int, float)) or not 0 < frac <= 1:
                v.reasons.append(f"fraction must be in (0, 1], got {frac!r}")
                continue
            partial = frac < 0.999
            recent_buy = ctx.last_buy.get(sym)
            if (
                partial
                and pos.unrealized_pl < 0
                and recent_buy
                and ctx.today - recent_buy <= timedelta(days=config.WASH_SALE_DAYS)
            ):
                v.reasons.append(
                    f"partial loss sale within {config.WASH_SALE_DAYS} days of a buy "
                    f"({recent_buy}) would be a wash sale; sell the whole position or wait"
                )
                continue
            qty = pos.qty if not partial else pos.qty * frac
            v.qty = _fmt_qty(qty)
            v.approved = True
            used += 1
            continue

        # Buys.
        if sym not in ctx.menu_vol:
            v.reasons.append("not on the screened menu")
            continue
        if ctx.regime_above_sma is not True:
            v.reasons.append(
                "regime filter: SPY is below its 200-day average"
                if ctx.regime_above_sma is False
                else "regime filter: not enough SPY history to confirm risk-on"
            )
            continue
        if sym in sold_today:
            v.reasons.append("sold today; same-day round trips are blocked")
            continue
        loss = ctx.last_loss_sale.get(sym)
        if loss and ctx.today - loss <= timedelta(days=config.WASH_SALE_DAYS):
            v.reasons.append(f"sold at a loss on {loss}; wash-sale window runs {config.WASH_SALE_DAYS} days")
            continue
        if v.requested_usd is None or v.requested_usd <= 0:
            v.reasons.append("buy needs a positive notional_usd")
            continue
        held = ctx.positions[sym].market_value if sym in ctx.positions else 0.0
        room = max(0.0, max_position_usd(ctx, sym) - held)
        amount = min(v.requested_usd, room, cash)
        amount = int(amount * 100) / 100  # round down to the cent
        if amount < config.MIN_ORDER_USD:
            v.reasons.append(
                f"no room: cap ${max_position_usd(ctx, sym):.2f} (inverse-vol, max "
                f"{config.MAX_POSITION_PCT:.0%}), held ${held:.2f}, cash ${cash:.2f}"
            )
            continue
        if amount < v.requested_usd - 0.005:
            v.resized = True
            v.reasons.append(
                f"resized ${v.requested_usd:.2f} -> ${amount:.2f} "
                f"(cap ${max_position_usd(ctx, sym):.2f}, held ${held:.2f}, cash ${cash:.2f})"
            )
        v.notional = amount
        v.approved = True
        cash -= amount
        used += 1
    return out
