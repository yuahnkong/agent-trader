"""Return on invested money, for the agents and the shadow, so the comparison isn't decided
by how much cash each side happened to hold. The shadow went fully invested on day one
while the agents deployed over a week; total-account returns mostly measure that.

Both series are rebuilt from history at every close (Alpaca fills for the agents, the
shadow's own trade list), so nothing new is stored and past days backfill themselves.

Each day's return is a modified Dietz estimate with that day's net purchases counted at
the start of the day: R = (V - V_prev - flow) / (V_prev + flow), where V is the market
value of the holdings at the close. Daily returns chain into a time-weighted return.
"""

from datetime import date

import pandas as pd

from . import config
from .ledger import _fill_date


def _price_on(s: pd.Series | None, day: date) -> float | None:
    if s is None:
        return None
    before = s[s.index <= day]
    return None if before.empty else float(before.iloc[-1])


def invested_series(trades: list[dict], closes: dict[str, pd.Series], days: list[date]) -> list[dict]:
    """`trades`: {date, symbol, side, qty, price}. A trade on a day outside `days`
    (crypto on a weekend) counts on the next day in `days`. Returns one row per day from
    the first trade on: invested value, net flow, the day's return and the chained return."""
    pending = sorted(trades, key=lambda t: t["date"])
    shares: dict[str, float] = {}
    rows, prev, growth, i = [], 0.0, 1.0, 0
    for day in days:
        flow = 0.0
        while i < len(pending) and pending[i]["date"] <= day:
            t = pending[i]
            sign = 1 if t["side"] == "buy" else -1
            shares[t["symbol"]] = shares.get(t["symbol"], 0.0) + sign * t["qty"]
            flow += sign * t["qty"] * t["price"]
            i += 1
        if not shares and not rows:
            continue
        value = 0.0
        for sym, q in shares.items():
            px = _price_on(closes.get(sym), day)
            if px is not None and abs(q) > 1e-9:
                value += q * px
        base = prev + flow
        r = (value - prev - flow) / base if base > 1e-6 else 0.0
        growth *= 1 + r
        rows.append({"date": day.isoformat(), "invested": round(value, 2), "flow": round(flow, 2),
                     "day_return": round(r, 6), "return": round(growth - 1, 6)})
        prev = value
    return rows


def agent_trades(fills: list[dict]) -> list[dict]:
    return [{"date": _fill_date(f), "symbol": config.canonical_symbol(f["symbol"]), "side": f["side"],
             "qty": float(f["qty"]), "price": float(f["price"])} for f in fills]


def shadow_trades(trades: list[dict]) -> list[dict]:
    return [{**t, "date": date.fromisoformat(t["date"])} for t in trades]


def merge_into_performance(perf: list[dict], agents: list[dict], shadow: list[dict]) -> list[dict]:
    """Add invested-money fields to each performance row, using the latest series row on
    or before its date (weekend rows carry Friday's)."""
    def latest(series, day):
        rows = [r for r in series if r["date"] <= day]
        return rows[-1] if rows else None

    for p in perf:
        a, s = latest(agents, p["date"]), latest(shadow, p["date"])
        p["agent_invested_return"] = a["return"] if a else None
        p["shadow_invested_return"] = s["return"] if s else None
        p["agent_invested_pct"] = (round(a["invested"] / p["agent_equity"], 4)
                                   if a and p.get("agent_equity") else None)
        p["shadow_invested_pct"] = (round(s["invested"] / p["shadow_value"], 4)
                                    if s and p.get("shadow_value") else None)
        p["version"] = config.version_for(p["date"])
    return perf


def since_version(perf: list[dict], version: str) -> dict | None:
    """Returns from the last close before `version` started to the latest row. Total
    returns compare account values; invested returns compare the chained series."""
    if not perf:
        return None
    start = config.version_start(version)[:10]
    before = [p for p in perf if p["date"] < start]
    last = perf[-1]
    keys = ("agent_return", "shadow_return", "spy_return", "agent_invested_return", "shadow_invested_return")
    if not before:  # the version started with the experiment: since-start figures already apply
        return {"version": version, "since": "start", **{k: last.get(k) for k in keys}}
    base = before[-1]

    def chained(key):
        if last.get(key) is None or base.get(key) is None:
            return None
        return round((1 + last[key]) / (1 + base[key]) - 1, 5)

    return {"version": version, "since": base["date"], **{k: chained(k) for k in keys}}
