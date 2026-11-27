"""The current thesis and invalidation for each position, rebuilt from decision files.

A buy's decision sets the thesis. Later decisions can change the invalidation (or the
thesis text) without trading, through `thesis_updates`; before that existed, the
Portfolio Manager re-anchored stops only in its prose reasoning, and every later run
read the stale level. A full sell clears the entry, so a re-buy starts fresh.

Each position can also carry a numeric `stop_price`: a close below it means the price
part of the invalidation has tripped. `stop_on` names the symbol whose close is checked
(default: the position itself; VOO and VTI use SPY's 200-day line). With the number
stored, Python measures the distance to every stop, so agents stop doing that by hand.
Stops are advisory: a tripped stop is flagged to the agents, not sold automatically.
"""

import json
from pathlib import Path

from . import config

STOP_WATCH_PCT = 0.03  # a mark within 3% of its stop is flagged as "near"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def open_theses(held: set[str] | None = None, runs_dir: Path | None = None) -> dict[str, dict]:
    """{symbol: {thesis, invalidation, bought, updated, update_why}} from every decision
    file in run order. With `held`, only those symbols are returned."""
    out: dict[str, dict] = {}
    for d in sorted((runs_dir or config.RUNS_DIR).glob("*/decision.json")):
        run = d.parent.name
        decision = _load(d)
        execution = _load(d.parent / "execution.json")
        # Trades the risk layer blocked never happened; skip them once execution is known.
        sent = None if not execution else {
            (config.canonical_symbol(str(v.get("symbol", ""))), v.get("side"))
            for v in execution.get("verdicts", []) if v.get("approved")}
        for t in decision.get("trades", []):
            sym = config.canonical_symbol(str(t.get("symbol", "")).upper().strip())
            if not sym or (sent is not None and (sym, t.get("side")) not in sent):
                continue
            if t.get("side") == "buy":
                prev = out.get(sym) or {}
                # Adding to a position keeps the original entry date; the newest text wins,
                # and a buy without a numeric stop keeps the one already set.
                stop = _price(t.get("stop_price"))
                out[sym] = {"thesis": t.get("thesis"), "invalidation": t.get("invalidation"),
                            "stop_price": stop if stop else prev.get("stop_price"),
                            "stop_on": _stop_on(t, sym) if stop else prev.get("stop_on", sym),
                            "bought": prev.get("bought", run), "updated": run, "update_why": None}
            elif t.get("side") == "sell" and (t.get("fraction") is None or t.get("fraction") >= 0.999):
                out.pop(sym, None)
        for u in decision.get("thesis_updates", []) or []:
            sym = config.canonical_symbol(str(u.get("symbol", "")).upper().strip())
            if sym not in out:
                continue
            if u.get("invalidation"):
                out[sym]["invalidation"] = u["invalidation"]
            if u.get("thesis"):
                out[sym]["thesis"] = u["thesis"]
            if _price(u.get("stop_price")):
                out[sym]["stop_price"] = _price(u["stop_price"])
                out[sym]["stop_on"] = _stop_on(u, sym)
            out[sym]["updated"] = run
            out[sym]["update_why"] = u.get("why")
    if held is not None:
        out = {s: v for s, v in out.items() if s in held}
    return out


def _price(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _stop_on(row: dict, sym: str) -> str:
    on = str(row.get("stop_on") or "").upper().strip()
    return config.canonical_symbol(on) if on else sym


def stop_check(theses: dict[str, dict], marks: dict[str, float], held: list[str]) -> dict[str, dict]:
    """Distance from each held position's mark to its stop. `marks` are latest closes.
    status: tripped (mark at or under the stop), near (within STOP_WATCH_PCT), ok,
    missing (no numeric stop stored), or no mark (no price for `stop_on`)."""
    out = {}
    for sym in held:
        th = theses.get(sym, {})
        stop, on = th.get("stop_price"), th.get("stop_on") or sym
        mark = marks.get(on)
        row = {"stop_price": stop, "stop_on": on, "mark": None if mark is None else round(mark, 4),
               "pct_above_stop": None}
        if not stop:
            row["status"] = "missing"
        elif mark is None:
            row["status"] = "no mark"
        else:
            row["pct_above_stop"] = round(mark / stop - 1, 4)
            row["status"] = ("tripped" if mark <= stop else
                             "near" if mark / stop - 1 <= STOP_WATCH_PCT else "ok")
        out[sym] = row
    return out


def stop_alerts(checks: dict[str, dict]) -> list[str]:
    """One line per position that needs attention, nearest first."""
    rows = []
    for sym, c in checks.items():
        if c["status"] in ("tripped", "near"):
            where = "" if c["stop_on"] == sym else f" ({c['stop_on']})"
            rows.append((c["pct_above_stop"], f"{sym}: {c['status'].upper()}, mark {c['mark']:g}{where} vs stop "
                                               f"{c['stop_price']:g} ({c['pct_above_stop']:+.1%})"))
        elif c["status"] in ("missing", "no mark"):
            rows.append((9.0, f"{sym}: {'no numeric stop stored' if c['status'] == 'missing' else 'no price for ' + c['stop_on']}"))
    return [text for _, text in sorted(rows, key=lambda r: r[0])]
