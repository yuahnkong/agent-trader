import json
from datetime import date, timedelta

import pandas as pd

from agent_trader import config, learning
from agent_trader.theses import open_theses


def write(runs, name, decision, execution=None):
    d = runs / name
    d.mkdir(parents=True)
    (d / "decision.json").write_text(json.dumps(decision))
    if execution is not None:
        (d / "execution.json").write_text(json.dumps(execution))


def buy(sym, inv, thesis="t"):
    return {"symbol": sym, "side": "buy", "notional_usd": 10, "thesis": thesis, "invalidation": inv}


def approved(*pairs):
    return {"verdicts": [{"symbol": s, "side": side, "approved": True} for s, side in pairs]}


def test_thesis_update_reanchors_stop_and_keeps_entry_date(tmp_path):
    write(tmp_path, "2026-10-20", {"trades": [buy("IDXX", "below $575")]}, approved(("IDXX", "buy")))
    write(tmp_path, "2026-11-03", {"trades": [], "thesis_updates": [
        {"symbol": "IDXX", "invalidation": "below $600", "thesis": None, "why": "gap up"},
        {"symbol": "NOPE", "invalidation": "ignored: not held"}]})
    t = open_theses(runs_dir=tmp_path)
    assert set(t) == {"IDXX"}
    assert t["IDXX"]["invalidation"] == "below $600"
    assert t["IDXX"]["thesis"] == "t"  # null thesis keeps the old text
    assert (t["IDXX"]["bought"], t["IDXX"]["updated"]) == ("2026-10-20", "2026-11-03")


def test_full_sell_clears_and_blocked_trades_are_ignored(tmp_path):
    write(tmp_path, "2026-10-20", {"trades": [buy("A", "a1"), buy("B", "b1")]}, approved(("A", "buy")))
    write(tmp_path, "2026-10-21", {"trades": [{"symbol": "A", "side": "sell", "fraction": 1.0}]},
          approved(("A", "sell")))
    t = open_theses(runs_dir=tmp_path)
    assert t == {}  # A sold, B's buy was blocked by the risk layer


def test_partial_sell_keeps_thesis_and_held_filter(tmp_path):
    write(tmp_path, "2026-10-20", {"trades": [buy("A", "a1"), buy("BTC/USD", "c1")]})
    write(tmp_path, "2026-10-21", {"trades": [{"symbol": "A", "side": "sell", "fraction": 0.5}]})
    assert open_theses(runs_dir=tmp_path)["A"]["invalidation"] == "a1"
    assert set(open_theses({"BTC/USD"}, runs_dir=tmp_path)) == {"BTC/USD"}


def test_stance_outcome_is_symmetric():
    assert learning.stance_outcome(0.012) == "risk-on"
    assert learning.stance_outcome(-0.012) == "risk-off"
    assert learning.stance_outcome(0.009) == "neutral"
    assert learning.stance_outcome(-0.01) == "neutral"


class FakeAlpaca:
    def __init__(self, closes):
        self.closes = closes

    def daily_bars(self, symbols, start):
        rows = [{"symbol": s, "date": d, "close": c} for s in symbols for d, c in self.closes.get(s, {}).items()]
        return pd.DataFrame(rows, columns=["symbol", "date", "close"])

    def crypto_bars(self, symbols, start):
        return self.daily_bars([], start)

    def clock(self):
        return {"is_open": False}


def test_scorecard_baselines_and_skeptic_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(40)]
    spy = {d: 100 * (1.004 ** i) for i, d in enumerate(days)}  # +2% a week: risk-on weeks
    xyz = {d: 50.0 for d in days}
    run = tmp_path / days[0].isoformat()
    (run / "agents").mkdir(parents=True)
    (run / "agents" / "market-analyst.json").write_text(json.dumps({"stance": "neutral"}))
    (run / "agents" / "proposer.json").write_text(json.dumps(
        {"proposals": [{"id": "P1", "symbol": "XYZ", "side": "buy", "thesis": "x"}]}))
    (run / "agents" / "skeptic.json").write_text(json.dumps({"challenges": [
        {"proposal_id": "P1", "severity": "serious", "about": "sizing", "case_against": "too big"},
        {"proposal_id": "P1", "severity": "serious", "about": "direction", "case_against": "will lag"}]}))
    card = learning.build_scorecard(FakeAlpaca({"SPY": spy, "XYZ": xyz}))
    ma = card["summary"]["market-analyst"]
    assert ma["wrong"] == 1  # a neutral call in a +2% week is no longer "right"
    assert ma["baselines"]["always risk-on"] == 1.0
    skeptic = [c for c in card["calls"] if c["agent"] == "skeptic"]
    assert len(skeptic) == 1 and skeptic[0]["claim"] == "will lag"
