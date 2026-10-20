"""Markdown reports committed to reports/: one after the pre-open run, one after the close."""

import json
from pathlib import Path

from . import config

AGENTS = ("market-analyst", "fundamentals-analyst", "news-analyst", "proposer", "skeptic",
          "implicator", "portfolio-manager")


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _money(x) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:+.2%}"


def open_report(run: Path, execution: dict) -> Path:
    day = run.name
    market = _load(run / "inputs" / "market.json") or {}
    port = _load(run / "inputs" / "portfolio.json") or {}
    decision = _load(run / "decision.json") or {}
    reg = market.get("regime") or {}
    lines = [
        f"# Pre-open report, {day}",
        "",
        f"Equity {_money(port.get('account', {}).get('equity'))}, cash {_money(port.get('account', {}).get('cash'))}. "
        f"SPY {reg.get('spy_close', 'n/a')} vs 200-day {reg.get('sma200', 'n/a')} "
        f"({_pct(reg.get('pct_from_sma200'))}): buys {'allowed' if port.get('regime_allows_buys') else 'BLOCKED'}.",
        "",
        "## Research",
        "",
    ]
    for name in AGENTS[:3]:
        note = _load(run / "agents" / f"{name}.json") or {}
        headline = note.get("stance") or note.get("summary") or "(no output)"
        lines += [f"**{name}**: {headline}", ""]
        if note.get("summary") and note.get("stance"):
            lines += [note["summary"], ""]

    proposals = (_load(run / "agents" / "proposer.json") or {}).get("proposals", [])
    lines += ["## Proposals", ""]
    if not proposals:
        lines += ["None.", ""]
    for p in proposals:
        size = _money(p.get("notional_usd")) if p.get("side") == "buy" else f"{p.get('fraction', 1):.0%} of position"
        lines += [f"- **{p.get('id', '?')}** {p.get('side', '?').upper()} {p.get('symbol', '?')} {size}: {p.get('thesis', '')}",
                  f"  - wrong if: {p.get('invalidation', '')}"]
    lines.append("")

    for name in ("skeptic", "implicator"):
        out = _load(run / "agents" / f"{name}.json") or {}
        items = out.get("challenges") or out.get("checks") or []
        if items:
            lines += [f"## {name.title()}", ""]
            for c in items:
                lines.append(f"- {c.get('proposal_id', '?')}: {c.get('case_against') or c.get('summary', '')}")
            lines.append("")

    lines += ["## Portfolio Manager", "", decision.get("reasoning", "(no reasoning recorded)"), ""]
    lines += ["## Risk layer and orders", "", "| Trade | Requested | Result | Order |", "|---|---|---|---|"]
    for v in execution.get("verdicts", []):
        req = _money(v.get("requested_usd")) if v["side"] == "buy" else (v.get("qty") or "sell")
        result = "approved" if v["approved"] else "rejected"
        if v.get("reasons"):
            result += ": " + "; ".join(v["reasons"])
        order = v.get("order_status") or "-"
        lines.append(f"| {v['side']} {v['symbol']} | {req} | {result} | {order} |")
    if not execution.get("verdicts"):
        lines.append("| no trades | | | |")
    for note in execution.get("notes", []):
        lines += ["", f"> {note}"]
    path = config.REPORTS_DIR / f"{day}-open.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def close_report(run: Path) -> Path:
    day = run.name
    close = _load(run / "close.json") or {}
    perf = close.get("performance", {})
    lines = [
        f"# Close report, {day}",
        "",
        "| | Value | Since start |",
        "|---|---|---|",
        f"| Agents (Alpaca paper) | {_money(perf.get('agent_equity'))} | {_pct(perf.get('agent_return'))} |",
        f"| Shadow (rules only) | {_money(perf.get('shadow_value'))} | {_pct(perf.get('shadow_return'))} |",
        f"| SPY | {_money(perf.get('spy_value'))} | {_pct(perf.get('spy_return'))} |",
        "",
        f"Day P&L {_money(close.get('day_pl'))}. Drawdown from peak {_pct(-close.get('drawdown', 0))}."
        + (" **Trading is paused by the kill switch.**" if close.get("paused") else ""),
        "",
        "## Fills today",
        "",
    ]
    fills = close.get("fills_today", [])
    lines += [f"- {f['side']} {f['qty']} {f['symbol']} @ {f['price']}" for f in fills] or ["None."]
    lines += ["", "## Positions", "", "| Symbol | Value | Weight | Unrealized | Thesis broken? |", "|---|---|---|---|---|"]
    flags = {}
    for name in AGENTS:
        rv = _load(run / "review" / f"{name}.json") or {}
        for f in rv.get("broken_theses", []):
            flags.setdefault(f.get("symbol"), []).append(f"{name}: {f.get('why', '')}")
    for p in close.get("positions", []):
        lines.append(
            f"| {p['symbol']} | {_money(p['market_value'])} | {p['weight']:.1%} | "
            f"{_money(p['unrealized_pl'])} ({p['unrealized_plpc']:+.1%}) | {'; '.join(flags.get(p['symbol'], [])) or 'no'} |"
        )
    if not close.get("positions"):
        lines.append("| all cash | | | | |")
    shadow_trades = close.get("shadow_trades", [])
    if shadow_trades:
        lines += ["", "## Shadow rebalance", ""]
        lines += [f"- {t['side']} {t['symbol']} {t['qty']:.4f} @ {t['price']}" for t in shadow_trades]
    lines += ["", "## Reviews", ""]
    for name in AGENTS:
        rv = _load(run / "review" / f"{name}.json")
        if rv:
            lines += [f"**{name}**: {rv.get('summary', '')}", ""]
    path = config.REPORTS_DIR / f"{day}-close.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path
