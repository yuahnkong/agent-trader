"""Shadow portfolio rebalance: the menu's top names, inverse-volatility weights, the same
15% cap and regime filter as the real account. This is the "no agents" baseline."""

from datetime import date

from . import config
from .state import Shadow


def target_weights(menu: list[dict], sleeves: list[dict] | None = None,
                   n: int = config.SHADOW_POSITIONS) -> dict[str, float]:
    """Fixed split (config.SHADOW_SPLIT): the menu's top `n` stocks weighted by 1/vol,
    the ETF sleeve equally (inverse-vol would hand nearly all of it to the bond funds),
    and the crypto slice by 1/vol. No name above MAX_POSITION_PCT; what the cap trims
    stays in cash. With no sleeves, all of it goes to stocks."""
    sleeves = sleeves or []
    split = config.SHADOW_SPLIT if sleeves else {"stock": 1.0}
    out: dict[str, float] = {}

    def inverse_vol(rows, budget):
        rows = [r for r in rows if r.get("vol_252d")]
        if not rows:
            return
        inv = {r["symbol"]: 1 / r["vol_252d"] for r in rows}
        total = sum(inv.values())
        for sym, w in inv.items():
            out[sym] = min(config.MAX_POSITION_PCT, budget * w / total)

    inverse_vol([m for m in menu if m.get("vol_252d")][:n], split.get("stock", 0))
    etfs = [x for x in sleeves if x["asset_class"] == "etf"]
    for x in etfs:
        out[x["symbol"]] = min(config.MAX_POSITION_PCT, split.get("etf", 0) / len(etfs))
    inverse_vol([x for x in sleeves if x["asset_class"] == "crypto"], split.get("crypto", 0))
    return out


def rebalance(
    shadow: Shadow, menu: dict, closes: dict[str, float], regime_above: bool | None, today: date,
    sleeves: list[dict] | None = None,
) -> list[dict]:
    """Rebalance at today's closes when a new menu has appeared. With the regime filter
    off, names that left the menu are still sold, and only defensive ETFs are bought."""
    if shadow.last_menu == menu["as_of"]:
        return []
    value = shadow.value(closes)
    weights = target_weights(menu["menu"], sleeves)
    defensive = {x["symbol"] for x in (sleeves or []) if x.get("kind") == "defensive"}
    trades = []

    for sym in list(shadow.shares):
        px = closes.get(sym)
        if px is None:
            continue
        target_qty = weights.get(sym, 0.0) * value / px
        if regime_above is not True and sym not in defensive:
            target_qty = min(target_qty, shadow.shares[sym]) if sym in weights else 0.0
        delta = target_qty - shadow.shares[sym]
        if delta < -1e-9:
            shadow.cash += -delta * px
            shadow.shares[sym] = target_qty
            trades.append({"date": today.isoformat(), "symbol": sym, "side": "sell", "qty": -delta, "price": px})
        if shadow.shares[sym] <= 1e-9:
            del shadow.shares[sym]

    for sym, w in weights.items():
        if regime_above is True or sym in defensive:
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
