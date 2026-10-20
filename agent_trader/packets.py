"""Per-agent input packets. Each research agent gets a different slice of the data
(DESIGN.md: "they bring different information rather than restating each other"), and
every number in a packet is computed here so agents never do arithmetic on raw filings.

Layout of a run directory, runs/YYYY-MM-DD/:
    inputs/market.json        Market Analyst
    inputs/fundamentals.json  Fundamentals Analyst
    inputs/news.json          News & Events Analyst
    inputs/portfolio.json     Proposer, Implicator, Portfolio Manager
    agents/<agent>.json       what each agent wrote
    decision.json             the Portfolio Manager's final trade list
    execution.json            what the risk layer approved and what Alpaca accepted
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from . import config, prices
from .alpaca import AlpacaClient
from .ledger import Ledger, build_ledger
from .risk import Context, Position, kill_switch_tripped, max_position_usd
from .screen import SlimSec, median_vol, sec_ticker
from .sec import SecClient
from .state import Trading

# Free proxies for what the Market Analyst would ideally read. Only SEC and Alpaca are
# reachable from the run environment, so VIX, yields and credit come from ETFs.
MARKET_ETFS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000 (small caps)",
    "RSP": "S&P 500 equal weight (breadth)",
    "VIXY": "short-term VIX futures (volatility proxy; decays over time)",
    "SHY": "1-3y Treasuries",
    "IEF": "7-10y Treasuries",
    "TLT": "20y+ Treasuries",
    "HYG": "high-yield credit",
    "LQD": "investment-grade credit",
    "GLD": "gold",
    "UUP": "US dollar",
}
SECTOR_ETFS = {
    "XLK": "Technology", "XLV": "Health Care", "XLE": "Energy", "XLU": "Utilities",
    "XLC": "Communication", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLI": "Industrials", "XLB": "Materials", "XLRE": "Real Estate", "XLF": "Financials",
}


def run_dir(day: date, session: str | None = None) -> Path:
    """runs/YYYY-MM-DD, or runs/YYYY-MM-DD-<session> for an extra run the same day."""
    return config.RUNS_DIR / (day.isoformat() + (f"-{session}" if session else ""))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str) + "\n")


def _ret(s: pd.Series, n: int) -> float | None:
    return None if len(s) <= n else round(float(s.iloc[-1] / s.iloc[-1 - n] - 1), 4)


def completed_closes(alpaca: AlpacaClient, symbols: list[str], today: date, days: int = 400):
    """Closes for completed sessions only (no partial bar for today). Crypto comes from its
    own endpoint, in UTC days."""
    syms = sorted(set(symbols))
    stocks = [x for x in syms if "/" not in x]
    coins = [x for x in syms if "/" in x]
    start, end = today - timedelta(days=days), today - timedelta(days=1)
    bars = alpaca.daily_bars(stocks, start=start, end=end) if stocks else None
    if coins:
        cb = alpaca.crypto_bars(coins, start=start, end=end)
        bars = cb if bars is None else pd.concat([bars, cb], ignore_index=True)
    if bars is None or bars.empty:
        return {}
    return prices.closes_by_symbol(bars[bars["date"] < today])


def market_packet(closes: dict[str, pd.Series], sleeves: list[dict]) -> dict:
    def row(sym, label):
        s = closes.get(sym)
        if s is None or s.empty:
            return {"symbol": sym, "label": label, "missing": True}
        sma200 = float(s.iloc[-200:].mean()) if len(s) >= 200 else None
        return {
            "symbol": sym, "label": label, "as_of": s.index[-1].isoformat(),
            "close": round(float(s.iloc[-1]), 4),
            "ret_5d": _ret(s, 5), "ret_21d": _ret(s, 21), "ret_63d": _ret(s, 63),
            "pct_from_sma200": None if sma200 is None else round(float(s.iloc[-1]) / sma200 - 1, 4),
        }

    spy = closes.get(config.MARKET_PROXY)
    reg = prices.regime(spy) if spy is not None else None
    vol20 = prices.annualized_vol(spy, 20) if spy is not None else None
    return {
        "regime": None if reg is None else {
            "as_of": reg.as_of.isoformat(), "spy_close": reg.close,
            "sma200": round(reg.sma200, 2), "above_sma200": reg.above,
            "pct_from_sma200": round(reg.pct_from_sma, 4),
        },
        "spy_realized_vol_20d": None if vol20 is None else round(vol20, 4),
        "spy_realized_vol_252d": None if spy is None else prices.annualized_vol(spy),
        "broad": [row(s, l) for s, l in MARKET_ETFS.items()],
        "sectors": sorted((row(s, l) for s, l in SECTOR_ETFS.items()),
                          key=lambda r: -(r.get("ret_21d") or -9)),
        "sleeves": sleeves,
        "note": "Yields, VIX and credit spreads are ETF proxies; TLT down = long yields up. "
                "The regime filter (SPY vs 200-day SMA) is enforced in code regardless of stance; "
                "when it is off, only the defensive ETFs (kind 'defensive') can be bought. "
                "`sleeves` are the ETFs and crypto the agents may buy besides screened stocks; "
                "crypto vol is annualized over 365 days.",
    }


def estimate_next_earnings(forms: list[dict], today: date) -> str | None:
    """Earnings recur on a yearly cycle, so last year's results dates + 364 days is a fair
    guess. Prefer 8-K item 2.02 (results press release); fall back to 10-Q/10-K dates."""
    dates = [date.fromisoformat(f["filed"]) for f in forms if f["form"] == "8-K" and "2.02" in (f.get("items") or "")]
    if not dates:
        dates = [date.fromisoformat(f["filed"]) for f in forms if f["form"] in ("10-Q", "10-K")]
    future = sorted(d + timedelta(days=364) for d in dates if d + timedelta(days=364) >= today - timedelta(days=1))
    return future[0].isoformat() if future else None


def account_context(alpaca: AlpacaClient, trading: Trading, menu: dict, closes, today: date,
                    sleeves: list[dict] | None = None):
    sleeves = sleeves or []
    acct = alpaca.account()
    positions = alpaca.positions()
    for p in positions:
        p["symbol"] = config.canonical_symbol(p["symbol"])
    led = build_ledger(alpaca.fills())
    start = datetime.combine(today, datetime.min.time(), config.MARKET_TZ)
    todays = [o for o in alpaca.orders(after=start.astimezone(timezone.utc))
              if (o.get("client_order_id") or "").startswith("at-")
              and o.get("status") not in ("canceled", "rejected", "expired")]
    spy = closes.get(config.MARKET_PROXY)
    reg = prices.regime(spy) if spy is not None else None

    trading.start_if_needed(today, float(acct["equity"]), reg.close if reg else None)
    ctx = Context(
        today=today,
        equity=float(acct["equity"]),
        buying_power=float(acct.get("non_marginable_buying_power") or acct["buying_power"]),
        peak_equity=trading.peak_equity,
        paused=trading.paused,
        regime_above_sma=None if reg is None else reg.above,
        positions={p["symbol"]: Position(float(p["qty"]), float(p["market_value"]), float(p["unrealized_pl"]))
                   for p in positions},
        menu_vol={**{m["symbol"]: m["vol_252d"] for m in menu["menu"]},
                  **{x["symbol"]: x["vol_252d"] for x in sleeves}},
        median_vol=median_vol(menu),
        orders_today=[{"symbol": config.canonical_symbol(o["symbol"]), "side": o["side"]} for o in todays],
        last_loss_sale={s: d for s in {r.symbol for r in led.realized} if (d := led.last_loss_sale(s))},
        last_buy={s: led.last_buy(s) for s in led.buys},
        asset_class={**{m["symbol"]: "stock" for m in menu["menu"]},
                     **{x["symbol"]: x["asset_class"] for x in sleeves},
                     **{c: "crypto" for c in config.CRYPTO}},
        defensive={x["symbol"] for x in sleeves if x["kind"] == "defensive"},
    )
    return acct, positions, led, ctx


def portfolio_packet(acct, positions, led: Ledger, ctx: Context, menu: dict, closes, today: date,
                     sleeves: list[dict] | None = None) -> dict:
    sleeves = sleeves or []
    def lots(sym):
        return [
            {"qty": round(l.qty, 6), "cost_per_share": l.price, "opened": l.opened.isoformat(),
             "days_held": (today - l.opened).days,
             "long_term_on": (l.opened + timedelta(days=366)).isoformat()}
            for l in led.lots.get(sym, [])
        ]

    menu_syms = {m["symbol"] for m in menu["menu"]} | {x["symbol"] for x in sleeves}
    held = {sym: ctx.positions[sym].market_value for sym in ctx.positions}
    crypto_room = max(0.0, config.MAX_CRYPTO_PCT * ctx.equity - ctx.crypto_held())

    def cap(sym):
        room = max(0.0, max_position_usd(ctx, sym) - held.get(sym, 0.0))
        return round(min(room, crypto_room) if ctx.asset_class.get(sym) == "crypto" else room, 2)
    spy = closes.get(config.MARKET_PROXY)
    betas = {}
    for sym in sorted(menu_syms | set(held)):
        b = prices.beta(closes[sym], spy) if spy is not None and sym in closes else None
        betas[sym] = None if b is None else round(b, 3)
    drop = sum(-0.10 * (betas.get(s) or 1.0) * mv for s, mv in held.items())
    blocked = {}
    for sym in menu_syms:
        loss = ctx.last_loss_sale.get(sym)
        if loss and (today - loss).days <= config.WASH_SALE_DAYS:
            blocked[sym] = f"wash-sale window after loss sale on {loss}"
    return {
        "date": today.isoformat(),
        "account": {"equity": ctx.equity, "cash": float(acct["cash"]), "buying_power": ctx.buying_power},
        "kill_switch": {
            "paused": ctx.paused, "peak_equity": ctx.peak_equity,
            "drawdown_from_peak": round(ctx.drawdown, 4),
            "trips_at": config.KILL_SWITCH_DRAWDOWN, "tripped": kill_switch_tripped(ctx),
        },
        "regime_allows_buys": ctx.regime_above_sma is True,
        "buys_allowed_when_regime_off": sorted(ctx.defensive),
        "trades_remaining_today": max(0, config.MAX_TRADES_PER_DAY - len(ctx.orders_today)),
        "orders_already_today": ctx.orders_today,
        "positions": [
            {"symbol": p["symbol"], "qty": float(p["qty"]), "market_value": float(p["market_value"]),
             "weight": round(float(p["market_value"]) / ctx.equity, 4) if ctx.equity else None,
             "avg_entry_price": float(p["avg_entry_price"]), "unrealized_pl": float(p["unrealized_pl"]),
             "unrealized_plpc": float(p["unrealized_plpc"]), "on_menu": p["symbol"] in menu_syms,
             "asset_class": ctx.asset_class.get(p["symbol"], "stock"),
             "lots": lots(p["symbol"])}
            for p in positions
        ],
        "buy_caps_usd": {sym: cap(sym) for sym in sorted(menu_syms)},
        "asset_classes": {sym: ctx.asset_class.get(sym, "stock") for sym in sorted(menu_syms)},
        "crypto": {"max_pct_of_equity": config.MAX_CRYPTO_PCT, "held_usd": round(ctx.crypto_held(), 2),
                   "room_usd": round(crypto_room, 2)},
        "buy_blocked": blocked,
        "betas_252d": betas,
        "stress_10pct_market_drop": {
            "current_portfolio_loss_usd": round(drop, 2),
            "per_100usd_position": {s: None if b is None else round(-10 * b, 2) for s, b in betas.items()},
            "method": "beta x -10% x position value; a missing beta is treated as 1.0",
        },
        "recent_realized": [
            {"symbol": r.symbol, "closed": r.closed.isoformat(), "pnl": round(r.pnl, 2),
             "long_term": r.long_term}
            for r in led.realized if (today - r.closed).days <= 60
        ],
        "limits": {
            "max_position_pct": config.MAX_POSITION_PCT,
            "max_trades_per_day": config.MAX_TRADES_PER_DAY,
            "wash_sale_days": config.WASH_SALE_DAYS,
            "kill_switch_drawdown": config.KILL_SWITCH_DRAWDOWN,
            "min_order_usd": config.MIN_ORDER_USD,
            "max_crypto_pct": config.MAX_CRYPTO_PCT,
            "sizing": "buy_caps_usd already applies inverse-vol sizing, the 15% cap and the crypto cap, "
                      "net of current holdings",
        },
    }


def fundamentals_packet(menu: dict, held: list[str]) -> dict:
    keep = ("symbol", "name", "sector", "sic_description", "close", "vol_252d", "dollar_volume",
            "gross_profitability", "gp_sector_pct", "fscore", "fscore_computable", "fscore_tests",
            "fy_end", "known_since", "revenue", "net_income", "cfo", "total_assets", "flags")
    return {
        "menu_as_of": menu["as_of"],
        "rules": menu["rules"],
        "universe_size": menu["universe_size"],
        "candidates": [{k: m.get(k) for k in keep} for m in menu["menu"]],
        "holdings_not_on_menu": sorted(set(held) - {m["symbol"] for m in menu["menu"]}),
        "note": "gross_profitability = (revenue - COGS) / total assets, latest 10-K as filed by "
                "known_since. gp_sector_pct is the rank within sector across the eligible universe.",
    }


def news_packet(alpaca: AlpacaClient, symbols: list[str], today: date, hours: int = 72) -> dict:
    sec = SecClient()
    slim = SlimSec(sec)
    tickers = sec.ticker_map()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    headlines = []
    query = [x.replace("/", "") for x in symbols]  # the news API writes crypto as BTCUSD
    for i in range(0, len(query), 25):
        headlines += alpaca.news(query[i:i + 25], start=since, limit=150)
    for h in headlines:
        h["symbols"] = [config.canonical_symbol(x) for x in h["symbols"]]
    events = {}
    for sym in symbols:
        info = tickers.get(sec_ticker(sym))
        if info is None:
            continue
        prof = slim.profile(info["cik"]) or {"forms": []}
        recent = sec.recent_filings(info["cik"], since=today - timedelta(days=7))
        nxt = estimate_next_earnings(prof["forms"], today)
        events[sym] = {
            "filings_7d": [{"form": f.form, "filed": f.filed.isoformat(), "items": f.items, "url": f.url}
                           for f in recent],
            "next_earnings_estimate": nxt,
            "days_to_earnings": None if nxt is None else (date.fromisoformat(nxt) - today).days,
        }
    seen, uniq = set(), []
    for h in sorted(headlines, key=lambda h: h["created_at"], reverse=True):
        if h["id"] not in seen:
            seen.add(h["id"])
            uniq.append({**h, "symbols": [s for s in h["symbols"] if s in symbols] or h["symbols"]})
    return {
        "window_hours": hours,
        "headlines": uniq[:200],
        "events": events,
        "note": "next_earnings_estimate is last year's results date + 364 days, not a confirmed "
                "date. 8-K items: 2.02 results, 5.02 officer changes, 1.01 material agreements, "
                "4.01 auditor change, 4.02 non-reliance on prior financials.",
    }
