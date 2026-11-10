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


def test_stop_price_set_on_buy_raised_by_update_and_kept_on_add(tmp_path):
    from agent_trader.theses import stop_check
    write(tmp_path, "2026-11-11", {"trades": [
        {**buy("IDXX", "close below 600"), "stop_price": 600},
        {**buy("VOO", "SPY below 200-day"), "stop_price": 727.98, "stop_on": "spy"},
        buy("KR", "8% under fill")]})
    write(tmp_path, "2026-11-12", {"trades": [buy("IDXX", "adds, no stop given")], "thesis_updates": [
        {"symbol": "IDXX", "stop_price": "640"}, {"symbol": "KR", "stop_price": -1}]})
    t = open_theses(runs_dir=tmp_path)
    assert t["IDXX"]["stop_price"] == 640.0 and t["IDXX"]["stop_on"] == "IDXX"
    assert t["VOO"]["stop_on"] == "SPY"
    assert t["KR"]["stop_price"] is None  # a bad number is ignored, not stored
    checks = stop_check(t, {"IDXX": 650.0, "SPY": 720.0, "KR": 60.0}, ["IDXX", "VOO", "KR", "BND"])
    assert checks["IDXX"]["status"] == "near" and checks["IDXX"]["pct_above_stop"] == 0.0156
    assert checks["VOO"]["status"] == "tripped" and checks["VOO"]["mark"] == 720.0
    assert checks["KR"]["status"] == checks["BND"]["status"] == "missing"


def test_stop_alerts_order_and_report_column():
    from agent_trader.reports import _stop
    from agent_trader.theses import stop_alerts
    checks = {
        "A": {"stop_price": 10.0, "stop_on": "A", "mark": 12.0, "pct_above_stop": 0.2, "status": "ok"},
        "B": {"stop_price": 10.0, "stop_on": "B", "mark": 10.2, "pct_above_stop": 0.02, "status": "near"},
        "C": {"stop_price": 10.0, "stop_on": "C", "mark": 9.5, "pct_above_stop": -0.05, "status": "tripped"},
        "D": {"stop_price": None, "stop_on": "D", "mark": None, "pct_above_stop": None, "status": "missing"},
    }
    alerts = stop_alerts(checks)
    assert [a[0] for a in alerts] == ["C", "B", "D"]
    assert _stop(checks["C"], "C") == "10 (-5.0%) **TRIPPED**"
    assert _stop(checks["D"], "D") == "none set"
    assert _stop({**checks["A"], "stop_on": "SPY"}, "A") == "SPY 10 (+20.0%)"
