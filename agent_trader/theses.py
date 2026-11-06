"""The current thesis and invalidation for each position, rebuilt from decision files.

A buy's decision sets the thesis. Later decisions can change the invalidation (or the
thesis text) without trading, through `thesis_updates`; before that existed, the
Portfolio Manager re-anchored stops only in its prose reasoning, and every later run
read the stale level. A full sell clears the entry, so a re-buy starts fresh.
"""

import json
from pathlib import Path

from . import config


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
                prev = out.get(sym)
                # Adding to a position keeps the original entry date; the newest text wins.
                out[sym] = {"thesis": t.get("thesis"), "invalidation": t.get("invalidation"),
                            "bought": prev["bought"] if prev else run, "updated": run, "update_why": None}
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
            out[sym]["updated"] = run
            out[sym]["update_why"] = u.get("why")
    if held is not None:
        out = {s: v for s, v in out.items() if s in held}
    return out
