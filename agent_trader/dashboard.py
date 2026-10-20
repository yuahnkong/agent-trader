"""Flat JSON tables for the dashboard artifact, one file per dataset, written to
data/dashboard/. Each file is a list of row objects; the dashboard page reads them.

    python -m agent_trader.pipeline dashboard-data
"""

import json
from datetime import date, datetime, timezone

from . import config, prices, screen
from .alpaca import AlpacaClient
from .ledger import build_ledger
from .packets import completed_closes
from .sleeves import sleeve_rows, sleeve_symbols
from .state import Trading, load_performance

OUT_DIR = config.DATA_DIR / "dashboard"
AGENT_STAGES = [
    ("market-analyst", "Research"), ("fundamentals-analyst", "Research"), ("news-analyst", "Research"),
    ("proposer", "Proposal"), ("skeptic", "Challenge"), ("implicator", "Challenge"),
    ("portfolio-manager", "Decision"),
]


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _latest_run() -> str | None:
    runs = sorted(p.parent.name for p in config.RUNS_DIR.glob("*/execution.json"))
    return runs[-1] if runs else None


def _headline(name: str, out: dict) -> tuple[str, str]:
    if name == "market-analyst":
        conf = out.get("confidence")
        return (f"{out.get('stance', '?')}" + (f" ({conf:.0%} confidence)" if isinstance(conf, (int, float)) else ""),
                out.get("summary", ""))
    if name == "fundamentals-analyst":
        top = [r.get("symbol") for r in sorted(out.get("rankings", []), key=lambda r: r.get("rank", 99))[:3]]
        return ("Top picks: " + ", ".join(top) if top else "No rankings", out.get("summary", ""))
    if name == "news-analyst":
        high = [e.get("symbol") for e in out.get("event_risk", []) if e.get("risk") == "high"]
        return (f"{len(high)} high-risk name(s)" + (": " + ", ".join(high[:6]) if high else ""), out.get("summary", ""))
    if name == "proposer":
        ps = out.get("proposals", [])
        return (f"{len(ps)} proposal(s): " + ", ".join(f"{p.get('side')} {p.get('symbol')}" for p in ps) if ps
                else "Proposed nothing", out.get("summary", ""))
    if name == "skeptic":
        worst = [c.get("proposal_id") for c in out.get("challenges", []) if c.get("severity") in ("fatal", "serious")]
        return ("Serious objections to " + ", ".join(worst) if worst else "Only minor objections", out.get("summary", ""))
    if name == "implicator":
        s = out.get("stress_10pct_drop") or {}
        after = s.get("after_usd")
        return (f"10% market drop costs ${abs(after):,.2f} after trades" if isinstance(after, (int, float))
                else "Portfolio checks done", out.get("summary", ""))
    trades = out.get("trades", [])
    return (f"Approved {len(trades)}, rejected {len(out.get('rejected', []))}", out.get("reasoning", ""))


def build(today: date | None = None) -> dict[str, list[dict]]:
    today = today or config.market_today()
    alpaca = AlpacaClient()
    trading = Trading.load()
    menu = screen.load_menu() or {"as_of": None, "menu": []}
    acct = alpaca.account()
    positions = alpaca.positions()
    for p in positions:
        p["symbol"] = config.canonical_symbol(p["symbol"])
    led = build_ledger(alpaca.fills())
    closes = completed_closes(alpaca, [config.MARKET_PROXY, *sleeve_symbols()], today)
    sleeves = sleeve_rows(closes)
    reg = prices.regime(closes[config.MARKET_PROXY]) if config.MARKET_PROXY in closes else None
    equity = float(acct["equity"])
    peak = max(trading.peak_equity, equity)
    latest = _latest_run()
    run = config.RUNS_DIR / latest if latest else None

    # Latest approved thesis per symbol, across all decision files.
    theses = {}
    for d in sorted(config.RUNS_DIR.glob("*/decision.json")):
        for t in (_load(d) or {}).get("trades", []):
            if t.get("side") == "buy":
                theses[t.get("symbol")] = {"thesis": t.get("thesis"), "invalidation": t.get("invalidation"),
                                           "bought": d.parent.name}
    broken = {}
    for r in sorted(config.RUNS_DIR.glob("*/review/*.json")):
        for b in (_load(r) or {}).get("broken_theses", []):
            broken.setdefault(b.get("symbol"), []).append(f"{r.stem} ({r.parent.parent.name}): {b.get('why', '')}")
    sectors = {m["symbol"]: m.get("sector") for m in menu["menu"]}
    sectors.update({x["symbol"]: ("Crypto" if x["asset_class"] == "crypto" else f"ETF, {x['kind']}") for x in sleeves})

    account = [{
        "as_of": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "equity": round(equity, 2), "cash": round(float(acct["cash"]), 2),
        "invested": round(sum(float(p["market_value"]) for p in positions), 2),
        "day_pl": round(equity - float(acct["last_equity"]), 2),
        "start_equity": trading.start_equity, "started": trading.started,
        "peak_equity": round(peak, 2), "drawdown": round(1 - equity / peak, 4) if peak else 0.0,
        "kill_switch_at": config.KILL_SWITCH_DRAWDOWN, "paused": trading.paused,
        "paused_reason": trading.paused_reason,
        "regime_ok": None if reg is None else reg.above,
        "spy_close": None if reg is None else round(reg.close, 2),
        "spy_sma200": None if reg is None else round(reg.sma200, 2),
        "spy_vs_sma200": None if reg is None else round(reg.pct_from_sma, 4),
        "positions": len(positions), "menu_as_of": menu["as_of"], "menu_size": len(menu["menu"]),
        "latest_run": latest[:10] if latest else None,
        "latest_session": (latest[11:] or "morning") if latest else None,
    }]

    perf = load_performance()
    performance = [{k: r.get(k) for k in ("date", "agent_equity", "shadow_value", "spy_value",
                                          "agent_return", "shadow_return", "spy_return")} for r in perf]
    if not performance and trading.started and trading.start_equity:
        # Before the first close is recorded, all three start from the same money.
        se = trading.start_equity
        performance = [{"date": trading.started, "agent_equity": se, "shadow_value": se, "spy_value": se,
                        "agent_return": 0.0, "shadow_return": 0.0, "spy_return": 0.0}]

    position_rows = []
    for p in positions:
        sym = p["symbol"]
        lots = led.lots.get(sym, [])
        opened = min((l.opened for l in lots), default=None)
        th = theses.get(sym, {})
        position_rows.append({
            "symbol": sym, "sector": sectors.get(sym) or "off menu",
            "qty": round(float(p["qty"]), 6), "market_value": round(float(p["market_value"]), 2),
            "weight": round(float(p["market_value"]) / equity, 4) if equity else 0.0,
            "cost_basis": round(float(p["cost_basis"]), 2),
            "unrealized_pl": round(float(p["unrealized_pl"]), 2),
            "unrealized_plpc": round(float(p["unrealized_plpc"]), 4),
            "opened": opened.isoformat() if opened else None,
            "days_held": (today - opened).days if opened else None,
            "thesis": th.get("thesis") or "", "invalidation": th.get("invalidation") or "",
            "broken": " | ".join(broken.get(sym, [])),
        })

    trades = []
    for ex_path in sorted(config.RUNS_DIR.glob("*/execution.json")):
        day = ex_path.parent.name
        ex = _load(ex_path) or {}
        dec = _load(ex_path.parent / "decision.json") or {}
        props = {p.get("id"): p for p in (_load(ex_path.parent / "agents" / "proposer.json") or {}).get("proposals", [])}
        by_sym = {(t.get("symbol"), t.get("side")): t for t in dec.get("trades", [])}
        for v in ex.get("verdicts", []):
            t = by_sym.get((v["symbol"], v["side"]), {})
            amount = v.get("notional") or v.get("requested_usd")
            trades.append({
                "key": f"{day}-{v['symbol']}-{v['side']}", "date": day[:10], "symbol": v["symbol"], "side": v["side"],
                "amount": None if amount in (None, "") else round(float(amount), 2),
                "outcome": ("Sent" if v["approved"] else "Blocked by risk layer"),
                "order_status": v.get("order_status") or "",
                "detail": "; ".join(r for r in v.get("reasons", []) if "already submitted" not in r) or (t.get("thesis") or ""),
            })
        for r in dec.get("rejected", []):
            p = props.get(r.get("proposal_id"), {})
            trades.append({
                "key": f"{day}-{p.get('symbol', r.get('proposal_id'))}-{p.get('side', 'buy')}-rejected", "date": day[:10],
                "symbol": p.get("symbol", r.get("proposal_id")), "side": p.get("side", ""),
                "amount": p.get("notional_usd"), "outcome": "Rejected by portfolio manager",
                "order_status": "", "detail": r.get("why", ""),
            })

    agents = []
    if run is not None:
        for name, stage in AGENT_STAGES:
            out = _load(run / ("decision.json" if name == "portfolio-manager" else f"agents/{name}.json"))
            if out is None:
                continue
            head, detail = _headline(name, out)
            agents.append({"agent": name.replace("-", " ").capitalize(), "stage": stage,
                           "headline": head, "detail": detail, "date": latest})

    news = _load(run / "inputs" / "news.json") if run else None
    events = (news or {}).get("events", {})
    port = _load(run / "inputs" / "portfolio.json") if run else None
    caps = (port or {}).get("buy_caps_usd", {})
    held = {p["symbol"] for p in positions}
    menu_rows = [{
        "symbol": m["symbol"], "name": m.get("name", ""), "sector": m.get("sector", ""),
        "gp_sector_pct": m.get("gp_sector_pct"), "fscore": m.get("fscore"), "vol_252d": m.get("vol_252d"),
        "buy_cap_usd": caps.get(m["symbol"]),
        "days_to_earnings": (events.get(m["symbol"]) or {}).get("days_to_earnings"),
        "flags": len(m.get("flags") or []), "held": m["symbol"] in held, "asset_class": "stock",
    } for m in menu["menu"]] + [{
        "symbol": x["symbol"], "name": x["name"], "sector": sectors[x["symbol"]],
        "gp_sector_pct": None, "fscore": None, "vol_252d": x["vol_252d"],
        "buy_cap_usd": caps.get(x["symbol"]), "days_to_earnings": None,
        "flags": 0, "held": x["symbol"] in held, "asset_class": x["asset_class"],
    } for x in sleeves]

    return {"account": account, "performance": performance, "positions": position_rows,
            "trades": trades, "agents": agents, "menu": menu_rows}


def write(today: date | None = None) -> list:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, rows in build(today).items():
        path = OUT_DIR / f"{name}.json"
        path.write_text(json.dumps(rows, default=str))
        paths.append(path)
    return paths
