"""Command-line entry points.

    python -m agent_trader.pipeline check-alpaca [--fix]
    python -m agent_trader.pipeline status
    python -m agent_trader.pipeline screen                 # rebuild state/menu.json (~10 min)
    python -m agent_trader.pipeline prepare                # pre-open: data + agent packets
    python -m agent_trader.pipeline execute [--dry-run]    # risk layer + orders + open report
    python -m agent_trader.pipeline close                  # post-close: P&L, shadow, SPY
    python -m agent_trader.pipeline close-report           # close report incl. agent reviews
    python -m agent_trader.pipeline resume                 # clear a kill-switch pause
    python -m agent_trader.pipeline dashboard-data         # JSON tables for the dashboard
    python -m agent_trader.pipeline learn                  # scorecard + news memory (post-close)
    python -m agent_trader.pipeline clock                  # is the market open? (exit 3 if closed)
    python -m agent_trader.pipeline daily [--symbols AAPL MSFT ...]

The agents sit between `prepare` and `execute` (and between `close` and `close-report`);
see .claude/skills/preopen and .claude/skills/postclose.
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from . import attribution, config, learning, packets, prices, reports, screen
from .alpaca import AlpacaClient
from .ledger import build_ledger
from .risk import kill_switch_tripped, review
from .sec import SecClient
from .shadow import rebalance
from .sleeves import sleeve_rows, sleeve_symbols
from .state import Shadow, Trading, load_performance, record_performance, save_performance

sec_ticker = screen.sec_ticker


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
    today = config.market_today()

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


# Daily cycle -------------------------------------------------------------------------


def _menu(today: date, refresh: bool) -> dict:
    menu = screen.load_menu()
    if screen.menu_is_stale(menu, today):
        if not refresh:
            raise SystemExit("state/menu.json is missing or stale; run `pipeline screen` first")
        print("menu is stale, re-running the screen")
        menu = screen.run_screen(today)
    return menu


def _session_today(alpaca: AlpacaClient, today: date) -> bool:
    """True if the market is open today or will open later today (ET)."""
    clock = alpaca.clock()
    if clock["is_open"]:
        return True
    nxt = datetime.fromisoformat(clock["next_open"]).astimezone(config.MARKET_TZ).date()
    return nxt == today


def liquidate(alpaca: AlpacaClient, trading: Trading, today: date, reason: str) -> list[dict]:
    """Kill switch: cancel open orders, sell every position, pause trading."""
    alpaca.cancel_all_orders()
    orders = []
    for p in alpaca.positions():
        sym = config.canonical_symbol(p["symbol"])
        orders.append(alpaca.submit_order(
            sym, "sell", f"ks-{today:%Y%m%d}-{sym.replace('/', '')}", qty=p["qty"]
        ))
    trading.paused, trading.paused_reason, trading.paused_at = True, reason, today.isoformat()
    trading.save()
    return orders


def prepare(refresh_menu: bool = True, session: str | None = None) -> int:
    today = config.market_today()
    alpaca = AlpacaClient()
    menu = _menu(today, refresh_menu)
    trading = Trading.load()
    run = packets.run_dir(today, session)

    held = [config.canonical_symbol(p["symbol"]) for p in alpaca.positions()]
    menu_syms = [m["symbol"] for m in menu["menu"]]
    closes = packets.completed_closes(
        alpaca, [*packets.MARKET_ETFS, *packets.SECTOR_ETFS, *menu_syms, *held, *sleeve_symbols()], today
    )
    sleeves = sleeve_rows(closes)
    acct, positions, led, ctx = packets.account_context(alpaca, trading, menu, closes, today, sleeves)
    trading.save()

    port = packets.portfolio_packet(acct, positions, led, ctx, menu, closes, today, sleeves)
    packets.write_json(run / "inputs" / "market.json", packets.market_packet(closes, sleeves))
    packets.write_json(run / "inputs" / "fundamentals.json", packets.fundamentals_packet(menu, held))
    packets.write_json(run / "inputs" / "news.json",
                       packets.news_packet(alpaca, sorted(set(menu_syms) | set(held) | set(sleeve_symbols())), today))
    packets.write_json(run / "inputs" / "portfolio.json", port)
    packets.write_json(run / "inputs" / "learning.json", learning.learning_packet(today))

    session = _session_today(alpaca, today)
    print(f"prepared {run.relative_to(config.ROOT)}/inputs for {today}")
    print(f"  menu {menu['as_of']}: {len(menu_syms)} stocks + {len(sleeves)} ETFs/crypto; holdings: {', '.join(held) or 'none'}")
    print(f"  equity ${ctx.equity:,.2f}, buys {'allowed' if port['regime_allows_buys'] else 'BLOCKED by regime'}, "
          f"{port['trades_remaining_today']} trade(s) left today")
    if ctx.paused:
        print(f"  PAUSED since {trading.paused_at}: {trading.paused_reason}")
    if not session:
        print("  market is closed today; `execute` will not send orders")
    return 0


def execute(dry_run: bool = False, session: str | None = None) -> int:
    today = config.market_today()
    run = packets.run_dir(today, session)
    alpaca = AlpacaClient()
    trading = Trading.load()
    menu = _menu(today, refresh=False)
    decision_path = run / "decision.json"
    decision = json.loads(decision_path.read_text()) if decision_path.exists() else {"trades": []}
    if not decision_path.exists():
        print(f"WARN: no {decision_path.relative_to(config.ROOT)}; treating as no trades")

    held = [config.canonical_symbol(p["symbol"]) for p in alpaca.positions()]
    closes = packets.completed_closes(alpaca, [config.MARKET_PROXY, *held, *sleeve_symbols()], today)
    _, _, _, ctx = packets.account_context(alpaca, trading, menu, closes, today, sleeve_rows(closes))
    notes, verdicts = [], []

    if kill_switch_tripped(ctx) and not trading.paused:
        reason = f"equity ${ctx.equity:,.2f} is {ctx.drawdown:.1%} below peak ${ctx.peak_equity:,.2f}"
        notes.append(f"KILL SWITCH: {reason}. Liquidating and pausing.")
        if not dry_run:
            liquidate(alpaca, trading, today, reason)
        ctx.paused = True
    else:
        trading.save()

    def cid(t: dict) -> str:
        sym = str(t.get("symbol", "")).upper().strip().replace("/", "")
        return f"at-{today:%Y%m%d}{session or ''}-{sym}-{str(t.get('side', '')).lower().strip()}"

    # A re-run must not judge trades it already sent as new: they'd count against
    # today's limits a second time and show up as rejected.
    rows, fresh = [], []
    for t in decision.get("trades", []):
        sent = alpaca.order_by_client_id(cid(t))
        if sent is None:
            fresh.append(t)
            continue
        rows.append({"symbol": config.canonical_symbol(sent["symbol"]), "side": sent["side"], "requested_usd": t.get("notional_usd"),
                     "approved": True, "reasons": ["already submitted earlier today"],
                     "notional": sent.get("notional"), "qty": sent.get("qty"), "resized": False,
                     "order_id": sent["id"], "client_order_id": cid(t), "order_status": sent["status"]})

    verdicts = review(fresh, ctx)
    session = _session_today(alpaca, today)
    if not session:
        notes.append("Market is closed today; no orders sent.")
    for v in verdicts:
        row = asdict(v)
        if v.approved and session and not dry_run:
            order_cid = cid({"symbol": v.symbol, "side": v.side})
            try:
                order = alpaca.submit_order(v.symbol, v.side, order_cid, notional=v.notional, qty=v.qty)
                row.update(order_id=order["id"], client_order_id=order_cid, order_status=order["status"])
            except Exception as e:  # noqa: BLE001 - a broker rejection is logged, not fatal
                detail = getattr(getattr(e, "response", None), "text", "") or str(e)
                row.update(order_status=f"error: {detail[:200]}")
        elif v.approved:
            row.update(order_status="dry run" if dry_run else "not sent (market closed)")
        rows.append(row)

    execution = {"date": today.isoformat(), "version": config.version_for(run.name), "dry_run": dry_run,
                 "notes": notes, "verdicts": rows}
    packets.write_json(run / "execution.json", execution)
    _log_recommendations(run, execution)
    path = reports.open_report(run, execution)
    for r in rows:
        status = r.get("order_status") or ("rejected: " + "; ".join(r["reasons"]))
        print(f"  {r['side']} {r['symbol']}: {status}")
    for n in notes:
        print(f"  {n}")
    print(f"wrote {path.relative_to(config.ROOT)}")
    return 0


def _log_recommendations(run, execution: dict) -> None:
    """One line per agent per day, so each agent's calls can be scored later."""
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.LOGS_DIR / "recommendations.jsonl"
    day = run.name
    existing = path.read_text().splitlines() if path.exists() else []
    kept = [l for l in existing if json.loads(l).get("date") != day]
    for name in reports.AGENTS:
        # The Portfolio Manager's output is the decision file itself.
        f = run / ("decision.json" if name == "portfolio-manager" else f"agents/{name}.json")
        if f.exists():
            try:
                kept.append(json.dumps({"date": day, "agent": name, "output": json.loads(f.read_text())}))
            except json.JSONDecodeError:
                kept.append(json.dumps({"date": day, "agent": name, "error": "invalid JSON"}))
    kept.append(json.dumps({"date": day, "agent": "risk-layer", "output": execution}))
    path.write_text("\n".join(kept) + "\n")


def close() -> int:
    today = config.market_today()
    run = packets.run_dir(today)
    alpaca = AlpacaClient()
    trading = Trading.load()
    menu = _menu(today, refresh=False)

    acct = alpaca.account()
    positions = alpaca.positions()
    equity = float(acct["equity"])
    for p in positions:
        p["symbol"] = config.canonical_symbol(p["symbol"])
    held = [p["symbol"] for p in positions]
    shadow = Shadow.load()
    all_fills = alpaca.fills()
    # Everything either side ever held, so the invested-money series can price past days.
    traded = {config.canonical_symbol(f["symbol"]) for f in all_fills} | {t["symbol"] for t in shadow.trades}
    syms = {config.MARKET_PROXY, *held, *shadow.shares, *traded, *(m["symbol"] for m in menu["menu"]),
            *sleeve_symbols()}
    start = today - timedelta(days=400)
    bars = alpaca.daily_bars(sorted(x for x in syms if "/" not in x), start=start)
    if alpaca.clock()["is_open"]:
        bars = bars[bars["date"] < today]
    closes = prices.closes_by_symbol(bars)
    # Crypto never closes; its latest bar (today's, still forming) is the price at this run.
    closes.update(prices.closes_by_symbol(alpaca.crypto_bars(sorted(x for x in syms if "/" in x), start=start)))
    sleeves = sleeve_rows(closes)
    last = {s: float(c.iloc[-1]) for s, c in closes.items()}
    spy = closes.get(config.MARKET_PROXY)
    reg = prices.regime(spy) if spy is not None else None

    trading.start_if_needed(today, equity, reg.close if reg else None)
    if shadow.started is None:
        shadow.started, shadow.cash = today.isoformat(), trading.start_equity or equity
    shadow_trades = rebalance(shadow, menu, last, reg.above if reg else None, today, sleeves)
    shadow.save()

    start = trading.start_equity or equity
    shadow_value = shadow.value(last)
    spy_value = start * last[config.MARKET_PROXY] / trading.spy_start_close if trading.spy_start_close else None
    perf = {
        "date": today.isoformat(),
        "agent_equity": round(equity, 2),
        "shadow_value": round(shadow_value, 2),
        "spy_value": None if spy_value is None else round(spy_value, 2),
        "agent_return": round(equity / start - 1, 5),
        "shadow_return": round(shadow_value / start - 1, 5),
        "spy_return": None if spy_value is None else round(spy_value / start - 1, 5),
    }
    rows = record_performance(perf)
    # Invested-money returns, rebuilt from history so earlier rows backfill too.
    days = [d for d in spy.index if d >= date.fromisoformat(trading.started)] if spy is not None else []
    rows = attribution.merge_into_performance(
        rows,
        attribution.invested_series(attribution.agent_trades(all_fills), closes, days),
        attribution.invested_series(attribution.shadow_trades(shadow.trades), closes, days))
    save_performance(rows)
    perf = rows[-1] if rows[-1]["date"] == perf["date"] else perf
    version = config.version_for(run.name)

    notes = []
    drawdown = 1 - equity / trading.peak_equity if trading.peak_equity else 0.0
    if drawdown >= config.KILL_SWITCH_DRAWDOWN and not trading.paused:
        reason = f"equity ${equity:,.2f} is {drawdown:.1%} below peak ${trading.peak_equity:,.2f}"
        liquidate(alpaca, trading, today, reason)
        notes.append(f"KILL SWITCH: {reason}. Sell orders queued for the next open; trading paused.")
    trading.save()

    fills = [f for f in alpaca.fills(after=today - timedelta(days=1))
             if datetime.fromisoformat(f["transaction_time"].replace("Z", "+00:00"))
             .astimezone(config.MARKET_TZ).date() == today]
    led = build_ledger(all_fills)
    decisions = [json.loads(d.read_text()) for d in sorted(config.RUNS_DIR.glob(f"{today.isoformat()}*/decision.json"))]
    data = {
        "date": today.isoformat(),
        "equity": equity,
        "day_pl": round(equity - float(acct["last_equity"]), 2),
        "drawdown": round(drawdown, 4),
        "paused": trading.paused,
        "notes": notes,
        "performance": perf,
        "version": version,
        "since_version": attribution.since_version(rows, version),
        "fills_today": [{k: f[k] for k in ("symbol", "side", "qty", "price", "transaction_time")} for f in fills],
        "positions": [
            {"symbol": p["symbol"], "qty": float(p["qty"]), "market_value": float(p["market_value"]),
             "weight": float(p["market_value"]) / equity if equity else 0.0,
             "unrealized_pl": float(p["unrealized_pl"]), "unrealized_plpc": float(p["unrealized_plpc"]),
             "change_today": float(p.get("change_today") or 0),
             "lots": [{"qty": l.qty, "cost": l.price, "opened": l.opened.isoformat()} for l in led.lots.get(p["symbol"], [])]}
            for p in positions
        ],
        "todays_decisions": decisions,
        "theses": _open_theses(),
        "shadow_trades": shadow_trades,
        "shadow_holdings": {s: round(q, 6) for s, q in shadow.shares.items()},
        "recent_performance": load_performance()[-10:],
    }
    packets.write_json(run / "close.json", data)
    # Post-close reviewers read lessons and their track record too.
    packets.write_json(run / "inputs" / "learning.json", learning.learning_packet(today))
    print(f"wrote {(run / 'close.json').relative_to(config.ROOT)}")
    print(f"  agents ${equity:,.2f} ({perf['agent_return']:+.2%})  shadow ${shadow_value:,.2f} "
          f"({perf['shadow_return']:+.2%})  SPY {'n/a' if spy_value is None else f'${spy_value:,.2f}'}")
    for n in notes:
        print(f"  {n}")
    return 0


def _open_theses() -> dict:
    """Latest approved thesis per held symbol, from past decision files, so post-close
    reviewers can judge whether it still holds."""
    out = {}
    for d in sorted(config.RUNS_DIR.glob("*/decision.json")):
        try:
            for t in json.loads(d.read_text()).get("trades", []):
                if t.get("side") == "buy":
                    out[t["symbol"]] = {"date": d.parent.name, "thesis": t.get("thesis"),
                                        "invalidation": t.get("invalidation")}
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def close_report() -> int:
    path = reports.close_report(packets.run_dir(config.market_today()))
    print(f"wrote {path.relative_to(config.ROOT)}")
    return 0


def status() -> int:
    alpaca = AlpacaClient()
    trading = Trading.load()
    acct = alpaca.account()
    menu = screen.load_menu()
    print(f"equity ${float(acct['equity']):,.2f}  cash ${float(acct['cash']):,.2f}  "
          f"peak ${trading.peak_equity:,.2f}  started {trading.started or 'not yet'}")
    if trading.paused:
        print(f"PAUSED since {trading.paused_at}: {trading.paused_reason}")
    for p in alpaca.positions():
        print(f"  {p['symbol']:<6} {float(p['qty']):>10.4f} sh  ${float(p['market_value']):>8.2f}  "
              f"P&L ${float(p['unrealized_pl']):>7.2f} ({float(p['unrealized_plpc']):+.1%})")
    for o in alpaca.orders(status="open"):
        print(f"  open order: {o['side']} {o['symbol']} {o.get('notional') or o.get('qty')} ({o['status']})")
    if menu:
        print(f"menu {menu['as_of']}: {', '.join(m['symbol'] for m in menu['menu'])}")
    perf = load_performance()
    if perf:
        p = perf[-1]
        print(f"{p['date']}: agents {p['agent_return']:+.2%}  shadow {p['shadow_return']:+.2%}  "
              f"SPY {'n/a' if p['spy_return'] is None else format(p['spy_return'], '+.2%')}")
        if p.get("agent_invested_return") is not None:
            print(f"  invested money only: agents {p['agent_invested_return']:+.2%}  "
                  f"shadow {p['shadow_invested_return']:+.2%}  "
                  f"(agents {p['agent_invested_pct']:.0%} invested, shadow {p['shadow_invested_pct']:.0%})")
    return 0


def resume() -> int:
    trading = Trading.load()
    if not trading.paused:
        print("not paused")
        return 0
    equity = float(AlpacaClient().account()["equity"])
    # Reset the peak, or the next run would trip the switch again on the same drawdown.
    trading.paused, trading.paused_reason, trading.paused_at = False, None, None
    trading.peak_equity = equity
    trading.save()
    print(f"resumed; peak equity reset to ${equity:,.2f}")
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
    sub.add_parser("screen", help="rebuild the candidate menu (state/menu.json)")
    p = sub.add_parser("prepare", help="pre-open: refresh data and write agent input packets")
    p.add_argument("--no-refresh", action="store_true", help="fail instead of re-running a stale screen")
    p.add_argument("--session", help="label for an extra run the same day, e.g. pm -> runs/<date>-pm")
    e = sub.add_parser("execute", help="risk-check runs/<today>/decision.json and send orders")
    e.add_argument("--dry-run", action="store_true")
    e.add_argument("--session", help="the same label given to prepare")
    sub.add_parser("close", help="post-close: P&L, shadow portfolio, benchmark, review packet")
    sub.add_parser("close-report", help="write reports/<today>-close.md")
    sub.add_parser("status", help="account, positions, menu, performance")
    sub.add_parser("resume", help="clear a kill-switch pause")
    sub.add_parser("dashboard-data", help="write data/dashboard/*.json for the dashboard artifact")
    sub.add_parser("clock", help="print whether the US market is open now (exit 0 open, 3 closed)")
    sub.add_parser("learn", help="grade past calls (state/scorecard.json) and save today's news to memory")
    args = parser.parse_args(argv)

    if args.cmd == "check-alpaca":
        return check_alpaca(args.fix)
    if args.cmd == "daily":
        return daily([s.upper() for s in args.symbols], args.filings_days, args.news_hours)
    if args.cmd == "screen":
        r = screen.run_screen()
        print(f"wrote state/menu.json: {', '.join(m['symbol'] for m in r['menu'])}")
        return 0
    if args.cmd == "prepare":
        return prepare(not args.no_refresh, args.session)
    if args.cmd == "execute":
        return execute(args.dry_run, args.session)
    if args.cmd == "close":
        return close()
    if args.cmd == "close-report":
        return close_report()
    if args.cmd == "status":
        return status()
    if args.cmd == "clock":
        c = AlpacaClient().clock()
        print(f"market {'OPEN' if c['is_open'] else 'CLOSED'}  next_open={c['next_open']}  next_close={c['next_close']}")
        return 0 if c["is_open"] else 3
    if args.cmd == "learn":
        card = learning.write_scorecard()
        today = config.market_today().isoformat()
        for run in sorted(config.RUNS_DIR.glob(f"{today}*")):
            if run.is_dir():
                learning.remember_news(run)
        for agent, s in card["summary"].items():
            rate = "n/a" if s["hit_rate"] is None else f"{s['hit_rate']:.0%}"
            print(f"  {agent:<20} right {s['right']:>3}  wrong {s['wrong']:>3}  pending {s['pending']:>3}  hit rate {rate}")
        print("wrote state/scorecard.json and state/memory/news.jsonl")
        return 0
    if args.cmd == "dashboard-data":
        from . import dashboard
        for path in dashboard.write():
            print(f"wrote {path.relative_to(config.ROOT)}")
        return 0
    return resume()


if __name__ == "__main__":
    sys.exit(main())
