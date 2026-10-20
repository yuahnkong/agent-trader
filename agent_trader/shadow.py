"""Shadow portfolio rebalance: the menu's top names, inverse-volatility weights, the same
15% cap and regime filter as the real account. This is the "no agents" baseline."""

from datetime import date

from . import config
from .state import Shadow


def target_weights(menu: list[dict], n: int = config.SHADOW_POSITIONS) -> dict[str, float]:
    """Top `n` menu names (the menu is already ranked), weighted by 1/vol, capped at
    MAX_POSITION_PCT. Whatever the cap trims stays in cash."""
    top = [m for m in menu if m.get("vol_252d")][:n]
    if not top:
        return {}
    inv = {m["symbol"]: 1 / m["vol_252d"] for m in top}
    total = sum(inv.values())
    return {s: min(config.MAX_POSITION_PCT, w / total) for s, w in inv.items()}


def rebalance(
    shadow: Shadow, menu: dict, closes: dict[str, float], regime_above: bool | None, today: date
) -> list[dict]:
    """Rebalance at today's closes when a new menu has appeared. With the regime filter
    off, names that left the menu are still sold, but nothing new is bought."""
    if shadow.last_menu == menu["as_of"]:
        return []
    value = shadow.value(closes)
    weights = target_weights(menu["menu"])
    trades = []

    for sym in list(shadow.shares):
        px = closes.get(sym)
        if px is None:
            continue
        target_qty = weights.get(sym, 0.0) * value / px
        if regime_above is not True:
            target_qty = min(target_qty, shadow.shares[sym]) if sym in weights else 0.0
        delta = target_qty - shadow.shares[sym]
        if delta < -1e-9:
            shadow.cash += -delta * px
            shadow.shares[sym] = target_qty
            trades.append({"date": today.isoformat(), "symbol": sym, "side": "sell", "qty": -delta, "price": px})
        if shadow.shares[sym] <= 1e-9:
            del shadow.shares[sym]

    if regime_above is True:
        for sym, w in weights.items():
            px = closes.get(sym)
            if px is None:
                continue
            want = w * value / px - shadow.shares.get(sym, 0.0)
            cost = min(want * px, shadow.cash)
            if cost < config.MIN_ORDER_USD:
                continue
            qty = cost / px
            shadow.cash -= cost
            shadow.shares[sym] = shadow.shares.get(sym, 0.0) + qty
            trades.append({"date": today.isoformat(), "symbol": sym, "side": "buy", "qty": qty, "price": px})

    shadow.last_menu = menu["as_of"]
    shadow.trades.extend(trades)
    return trades
