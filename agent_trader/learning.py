"""How the system learns between runs. Nothing here retrains a model: the agents get a
memory and a track record, and a Reviewer agent turns those into a short lessons file.

- state/scorecard.json   every past call graded against what prices did next (Python)
- state/memory/news.jsonl notable headlines and event flags, kept for 60 days (Python)
- state/lessons.json      the Reviewer agent's working lessons, read by every agent

A call is only graded once HORIZON trading days have passed; younger ones are listed as
pending so nobody draws conclusions from one afternoon.
"""

import json
from datetime import date, timedelta

import pandas as pd

from . import config, prices
from .alpaca import AlpacaClient

SCORECARD_PATH = config.STATE_DIR / "scorecard.json"
MEMORY_PATH = config.STATE_DIR / "memory" / "news.jsonl"
LESSONS_PATH = config.STATE_DIR / "lessons.json"
HORIZON = 5  # trading days before a call is graded
MEMORY_DAYS = 60


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _runs() -> list:
    return sorted(p for p in config.RUNS_DIR.iterdir() if p.is_dir()) if config.RUNS_DIR.exists() else []


def _closes(alpaca: AlpacaClient, symbols: set[str], start: date) -> dict[str, pd.Series]:
    stocks = sorted(s for s in symbols if "/" not in s)
    coins = sorted(s for s in symbols if "/" in s)
    out = {}
    if stocks:
        bars = alpaca.daily_bars(stocks, start=start)
        if alpaca.clock()["is_open"]:
            bars = bars[bars["date"] < config.market_today()]
        out.update(prices.closes_by_symbol(bars))
    if coins:
        out.update(prices.closes_by_symbol(alpaca.crypto_bars(coins, start=start)))
    return out


def _forward(s: pd.Series | None, day: date, n: int | None = None) -> tuple[float | None, int]:
    """Return from the last close on or before `day` to n sessions later (or the latest),
    and how many sessions that covers."""
    if s is None or s.empty:
        return None, 0
    before = s[s.index <= day]
    after = s[s.index > day]
    if before.empty or after.empty:
        return None, 0
    end = after.iloc[min(n, len(after)) - 1] if n else after.iloc[-1]
    used = min(n, len(after)) if n else len(after)
    return float(end / before.iloc[-1] - 1), used


def build_scorecard(alpaca: AlpacaClient | None = None) -> dict:
    alpaca = alpaca or AlpacaClient()
    runs = _runs()
    if not runs:
        return {"calls": [], "summary": {}}
    first = date.fromisoformat(runs[0].name[:10])
    symbols = {config.MARKET_PROXY}
    for run in runs:
        for f in ("agents/proposer.json", "decision.json", "agents/news-analyst.json"):
            data = _load(run / f) or {}
            for row in data.get("proposals", []) + data.get("trades", []) + data.get("event_risk", []):
                if row.get("symbol"):
                    symbols.add(config.canonical_symbol(row["symbol"]))
    closes = _closes(alpaca, symbols, first - timedelta(days=10))
    spy = closes.get(config.MARKET_PROXY)
    calls = []

    def grade(agent, kind, day, symbol, claim, sign):
        """sign +1: the call is right if the symbol beats SPY; -1: right if it lags."""
        r, n = _forward(closes.get(symbol), day)
        m, _ = _forward(spy, day, n or None)
        row = {"agent": agent, "kind": kind, "date": day.isoformat(), "symbol": symbol, "claim": claim,
               "sessions": n, "return": None if r is None else round(r, 4),
               "vs_spy": None if r is None or m is None else round(r - m, 4)}
        row["status"] = "pending" if n < HORIZON or row["vs_spy"] is None else (
            "right" if sign * row["vs_spy"] > 0 else "wrong")
        calls.append(row)

    for run in runs:
        day = date.fromisoformat(run.name[:10])
        props = {p.get("id"): p for p in (_load(run / "agents/proposer.json") or {}).get("proposals", [])}
        decision = _load(run / "decision.json") or {}
        executed = {(config.canonical_symbol(v["symbol"]), v["side"])
                    for v in (_load(run / "execution.json") or {}).get("verdicts", []) if v.get("approved")}
        for t in decision.get("trades", []):
            sym, side = config.canonical_symbol(t.get("symbol", "")), t.get("side")
            sent = (sym, side) in executed
            grade("portfolio-manager", "approved " + side + (" (sent)" if sent else " (blocked by risk layer)"),
                  day, sym, t.get("thesis", "")[:160], 1 if side == "buy" else -1)
        for r in decision.get("rejected", []):
            p = props.get(r.get("proposal_id"), {})
            if p.get("symbol"):
                grade("portfolio-manager", "rejected " + str(p.get("side")), day,
                      config.canonical_symbol(p["symbol"]), r.get("why", "")[:160], -1 if p.get("side") == "buy" else 1)
        for p in props.values():
            if p.get("symbol"):
                grade("proposer", "proposed " + str(p.get("side")), day, config.canonical_symbol(p["symbol"]),
                      p.get("thesis", "")[:160], 1 if p.get("side") == "buy" else -1)
        for c in (_load(run / "agents/skeptic.json") or {}).get("challenges", []):
            p = props.get(c.get("proposal_id"), {})
            if p.get("symbol") and c.get("severity") in ("fatal", "serious"):
                grade("skeptic", f"{c['severity']} objection to {p.get('side')}", day,
                      config.canonical_symbol(p["symbol"]), c.get("case_against", "")[:160],
                      -1 if p.get("side") == "buy" else 1)
        stance = (_load(run / "agents/market-analyst.json") or {}).get("stance")
        if stance and spy is not None:
            r, n = _forward(spy, day, HORIZON)
            right = None if r is None or n < HORIZON else (
                (r > 0.005 and stance == "risk-on") or (r < -0.005 and stance == "risk-off")
                or (abs(r) <= 0.02 and stance == "neutral"))
            calls.append({"agent": "market-analyst", "kind": "stance", "date": day.isoformat(),
                          "symbol": config.MARKET_PROXY, "claim": stance, "sessions": n,
                          "return": None if r is None else round(r, 4), "vs_spy": None,
                          "status": "pending" if right is None else ("right" if right else "wrong")})
        for e in (_load(run / "agents/news-analyst.json") or {}).get("event_risk", []):
            if e.get("risk") != "high" or not e.get("symbol"):
                continue
            sym = config.canonical_symbol(e["symbol"])
            r, n = _forward(closes.get(sym), day, HORIZON)
            m, _ = _forward(spy, day, n or None)
            big = None if r is None or m is None or n < HORIZON else abs(r - m) > 0.03
            calls.append({"agent": "news-analyst", "kind": "high event risk", "date": day.isoformat(),
                          "symbol": sym, "claim": e.get("why", "")[:160], "sessions": n,
                          "return": None if r is None else round(r, 4),
                          "vs_spy": None if r is None or m is None else round(r - m, 4),
                          "status": "pending" if big is None else ("right" if big else "wrong")})

    summary = {}
    for c in calls:
        s = summary.setdefault(c["agent"], {"right": 0, "wrong": 0, "pending": 0})
        s[c["status"]] += 1
    for s in summary.values():
        graded = s["right"] + s["wrong"]
        s["hit_rate"] = None if graded == 0 else round(s["right"] / graded, 3)
    return {"as_of": config.market_today().isoformat(), "horizon_sessions": HORIZON,
            "summary": summary, "calls": calls,
            "how_graded": {
                "buys": "right if the symbol beat SPY from the decision date to the latest close",
                "rejections and serious skeptic objections to buys": "right if the symbol lagged SPY",
                "market stance": "risk-on right if SPY rose >0.5% over 5 sessions, risk-off if it fell "
                                 ">0.5%, neutral if it moved less than 2% either way",
                "high event risk": "right if the symbol moved more than 3% relative to SPY within 5 sessions",
            }}


def write_scorecard() -> dict:
    card = build_scorecard()
    SCORECARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCORECARD_PATH.write_text(json.dumps(card, indent=1) + "\n")
    return card


def remember_news(run) -> int:
    """Append the News Analyst's flags and notable headlines from `run` to the memory file,
    dropping anything older than MEMORY_DAYS. Idempotent per run."""
    note = _load(run / "agents/news-analyst.json") or {}
    day = run.name[:10]
    rows = [json.loads(l) for l in MEMORY_PATH.read_text().splitlines()] if MEMORY_PATH.exists() else []
    cutoff = (config.market_today() - timedelta(days=MEMORY_DAYS)).isoformat()
    rows = [r for r in rows if r["date"] >= cutoff and r.get("run") != run.name]
    for e in note.get("event_risk", []):
        rows.append({"date": day, "run": run.name, "symbol": e.get("symbol"), "type": f"{e.get('risk')} event risk",
                     "text": e.get("why", ""), "event_date": e.get("date")})
    for n in note.get("notable", []):
        rows.append({"date": day, "run": run.name, "symbol": n.get("symbol"), "type": "headline",
                     "text": f"{n.get('headline', '')}: {n.get('read', '')}"})
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


def learning_packet(today: date) -> dict:
    """What every agent reads before it works: the lessons, its track record, and the
    last 60 days of news memory."""
    card = _load(SCORECARD_PATH) or {"summary": {}, "calls": []}
    memory = [json.loads(l) for l in MEMORY_PATH.read_text().splitlines()] if MEMORY_PATH.exists() else []
    return {
        "lessons": [l for l in (_load(LESSONS_PATH) or {}).get("lessons", []) if l.get("status") == "active"],
        "scorecard_summary": card.get("summary", {}),
        "graded_calls": [c for c in card.get("calls", []) if c["status"] != "pending"][-40:],
        "pending_calls": sum(1 for c in card.get("calls", []) if c["status"] == "pending"),
        "news_memory": memory[-150:],
        "note": "Lessons come from a handful of trades; treat each as a hypothesis with the "
                "evidence it cites, not a rule. Hard limits are in code and lessons can't change them.",
    }
