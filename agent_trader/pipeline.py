"""Command-line entry points.

    python -m agent_trader.pipeline check-alpaca [--fix]
    python -m agent_trader.pipeline daily [--symbols AAPL MSFT ...]
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone

from . import config, prices
from .alpaca import AlpacaClient
from .sec import SecClient


def sec_ticker(symbol: str) -> str:
    """Alpaca writes share classes as BRK.B, SEC as BRK-B."""
    return symbol.replace(".", "-")


def check_alpaca(fix: bool = False) -> int:
    try:
        client = AlpacaClient()
    except config.MissingCredentials as e:
        print(f"FAIL: {e}")
        return 1

    problems: list[str] = []
    acct = client.account()
    print(f"Connected to {config.ALPACA_TRADING_URL}")
    print(f"  account {acct['account_number']}  status={acct['status']}")
    print(f"  equity=${float(acct['equity']):,.2f}  cash=${float(acct['cash']):,.2f}")
    print(f"  buying_power=${float(acct['buying_power']):,.2f}  multiplier={acct['multiplier']}")

    if acct["status"] != "ACTIVE":
        problems.append(f"account status is {acct['status']}, expected ACTIVE")
    if acct.get("trading_blocked") or acct.get("account_blocked"):
        problems.append("trading or account is blocked")
    if not 400 <= float(acct["equity"]) <= 800:
        problems.append(
            f"equity ${float(acct['equity']):,.2f} doesn't match the ~$600 plan; "
            "reset the paper account balance in the Alpaca dashboard"
        )

    cfg = client.account_configurations()
    # Paper accounts default to margin. The design is cash-only, so insist on 1x and no shorts.
    wanted = {"max_margin_multiplier": "1", "no_shorting": True}
    drift = {k: v for k, v in wanted.items() if str(cfg.get(k)) != str(v)}
    if drift and fix:
        print(f"  updating account configurations: {drift}")
        cfg = client.update_account_configurations(**drift)
    print(f"  max_margin_multiplier={cfg.get('max_margin_multiplier')}  "
          f"no_shorting={cfg.get('no_shorting')}  fractional={cfg.get('fractional_trading')}")
    if str(cfg.get("max_margin_multiplier")) != "1":
        problems.append("max_margin_multiplier should be 1 (cash account)")
    if not cfg.get("no_shorting"):
        problems.append("no_shorting should be true")
    if drift and not fix:
        print("  (run with --fix to apply the cash-only settings)")
    if cfg.get("fractional_trading") is False:
        problems.append("fractional trading is off")

    clock = client.clock()
    print(f"  market open={clock['is_open']}  next_open={clock['next_open']}")

    bars = client.daily_bars([config.MARKET_PROXY], start=date.today() - timedelta(days=10))
    if bars.empty:
        problems.append(f"no {config.MARKET_PROXY} bars returned from feed={config.ALPACA_DATA_FEED}")
    else:
        last = bars.iloc[-1]
        print(f"  market data ok: {last['symbol']} {last['date']} close={last['close']}")

    for p in problems:
        print(f"WARN: {p}")
    print("OK" if not problems else f"{len(problems)} issue(s) to fix")
    return 0 if not problems else 2


def daily(extra_symbols: list[str], filings_days: int, news_hours: int) -> int:
    alpaca = AlpacaClient()
    sec = SecClient()
    today = date.today()

    account = alpaca.account()
    positions = alpaca.positions()
    holdings = sorted(p["symbol"] for p in positions)
    symbols = sorted(set(holdings) | set(extra_symbols))

    # ~400 calendar days covers the 253 closes the volatility window needs.
    bars = alpaca.daily_bars([config.MARKET_PROXY, *symbols], start=today - timedelta(days=400))
    # Mid-session the feed returns today's bar built from partial trading. Only completed
    # sessions should reach the regime and volatility numbers.
    market_open = alpaca.clock()["is_open"]
    if market_open:
        bars = bars[bars["date"] < today]
    closes = prices.closes_by_symbol(bars)

    spy = closes.get(config.MARKET_PROXY)
    reg = prices.regime(spy) if spy is not None else None

    tickers = sec.ticker_map()
    filings, unmapped = {}, []
    for sym in symbols:
        info = tickers.get(sec_ticker(sym))
        if info is None:
            unmapped.append(sym)
            continue
        found = sec.recent_filings(info["cik"], since=today - timedelta(days=filings_days))
        filings[sym] = [
            {"form": f.form, "filed": f.filed.isoformat(), "items": f.items, "url": f.url}
            for f in found
        ]

    since = datetime.now(timezone.utc) - timedelta(hours=news_hours)
    headlines = alpaca.news(symbols, start=since) if symbols else []

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_open_at_pull": market_open,
        "account": {k: account[k] for k in ("equity", "cash", "buying_power")},
        "positions": [
            {k: p[k] for k in ("symbol", "qty", "avg_entry_price", "market_value", "unrealized_pl")}
            for p in positions
        ],
        "regime": None
        if reg is None
        else {
            "symbol": config.MARKET_PROXY,
            "as_of": reg.as_of.isoformat(),
            "close": reg.close,
            "sma200": round(reg.sma200, 4),
            "above_sma200": reg.above,
            "pct_from_sma200": round(reg.pct_from_sma, 4),
        },
        "prices": {
            sym: {
                "last_date": s.index[-1].isoformat(),
                "last_close": float(s.iloc[-1]),
                "vol_252d": prices.annualized_vol(s),
            }
            for sym, s in closes.items()
        },
        "filings": filings,
        "news": headlines,
        "unmapped_symbols": unmapped,
    }

    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SNAPSHOT_DIR / f"{today.isoformat()}-preopen.json"
    path.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"wrote {path.relative_to(config.ROOT)}")
    if market_open:
        print("WARN: market is open; dropped today's partial bars, prices are as of the prior close")
    if reg is None:
        print("WARN: not enough SPY history for the 200-day regime test")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_trader.pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check-alpaca", help="verify paper credentials, account setup, and data feed")
    c.add_argument("--fix", action="store_true", help="set margin to 1x and disable shorting")
    d = sub.add_parser("daily", help="pre-open data pull for holdings and given symbols")
    d.add_argument("--symbols", nargs="*", default=[])
    d.add_argument("--filings-days", type=int, default=7)
    d.add_argument("--news-hours", type=int, default=72)
    args = parser.parse_args(argv)

    if args.cmd == "check-alpaca":
        return check_alpaca(args.fix)
    return daily([s.upper() for s in args.symbols], args.filings_days, args.news_hours)


if __name__ == "__main__":
    sys.exit(main())
