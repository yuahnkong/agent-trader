import json
from datetime import date, timedelta

import pandas as pd

from agent_trader import attribution, config, learning

D = [date(2026, 10, 19) + timedelta(days=i) for i in range(1, 6)]  # Tue 10-20 .. Sat 10-24


def series(values, days=D):
    return pd.Series(values, index=days[:len(values)])


def test_invested_return_ignores_cash_and_chains_days():
    closes = {"A": series([11.0, 12.1, 12.1])}
    trades = [{"date": D[0], "symbol": "A", "side": "buy", "qty": 10, "price": 10.0}]
    rows = attribution.invested_series(trades, closes, D[:3])
    assert [r["day_return"] for r in rows] == [0.1, 0.1, 0.0]
    assert rows[-1]["return"] == 0.21  # 1.1 * 1.1 - 1, whatever cash sits beside it


def test_new_money_counts_as_flow_not_gain():
    closes = {"A": series([10.0, 10.0]), "B": series([20.0, 22.0])}
    trades = [{"date": D[0], "symbol": "A", "side": "buy", "qty": 10, "price": 10.0},
              {"date": D[1], "symbol": "B", "side": "buy", "qty": 5, "price": 20.0}]
    rows = attribution.invested_series(trades, closes, D[:2])
    assert rows[1]["flow"] == 100.0
    assert rows[1]["day_return"] == 0.05  # $10 gain on $200 invested


def test_weekend_trade_counts_on_next_listed_day_and_no_rows_before_first_trade():
    closes = {"A": series([10.0, 10.0, 10.0, 11.0, 11.0])}
    trades = [{"date": D[4], "symbol": "A", "side": "buy", "qty": 1, "price": 10.0}]
    assert attribution.invested_series(trades, closes, D[:4]) == []
    trades = [{"date": D[1], "symbol": "A", "side": "buy", "qty": 1, "price": 10.0}]
    rows = attribution.invested_series(trades, closes, [D[0], D[2], D[3]])
    assert rows[0]["date"] == D[2].isoformat() and rows[-1]["return"] == 0.1


def test_version_lookup_orders_sessions_within_a_day():
    assert config.version_for("2026-10-20") == "1"
    assert config.version_for("2026-10-20-pm") == "2"
    assert config.version_for("2026-10-28") == "3"
    assert config.version_for("2026-10-29") == config.VERSIONS[-1][0]


def test_since_version_rebases_on_the_last_close_before_it():
    perf = [{"date": "2026-10-20", "agent_return": 0.0, "shadow_return": 0.0, "spy_return": 0.0},
            {"date": "2026-10-28", "agent_return": 0.10, "shadow_return": 0.21, "spy_return": 0.0},
            {"date": "2026-10-29", "agent_return": 0.21, "shadow_return": 0.21, "spy_return": 0.1}]
    sv = attribution.since_version(perf, "4")
    assert sv["since"] == "2026-10-28"
    assert sv["agent_return"] == 0.1 and sv["shadow_return"] == 0.0 and sv["spy_return"] == 0.1
    assert attribution.since_version(perf, "1")["since"] == "start"


def test_trade_calls_stay_pending_until_twenty_sessions(tmp_path, monkeypatch):
    run = tmp_path / "2026-10-20"
    (run / "agents").mkdir(parents=True)
    (run / "agents" / "proposer.json").write_text(json.dumps(
        {"proposals": [{"id": "P1", "symbol": "A", "side": "buy", "thesis": "x"}]}))
    days = pd.bdate_range("2026-10-19", periods=30).date
    spy = pd.Series(100.0, index=days)
    a = pd.Series([100.0, 100.0] + [105.0] * 28, index=days)
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(learning, "_closes", lambda *_: {"SPY": spy, "A": a})

    card = learning.build_scorecard(alpaca=object())
    (call,) = card["calls"]
    assert call["grades"]["5"]["status"] == "right"
    assert call["grades"]["20"]["status"] == "right"
    assert call["grades"]["60"]["status"] == "pending"
    assert call["status"] == "right" and call["version"] == "1"
    s = card["summary"]["proposer"]
    assert s["graded"] == 1 and s["enough_data"] is False

    monkeypatch.setattr(learning, "_closes", lambda *_: {"SPY": spy[:10], "A": a[:10]})
    (call,) = learning.build_scorecard(alpaca=object())["calls"]
    assert call["grades"]["5"]["status"] == "right" and call["status"] == "pending"
