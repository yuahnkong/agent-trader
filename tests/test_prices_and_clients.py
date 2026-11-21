from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from agent_trader import prices
from agent_trader.alpaca import AlpacaClient
from agent_trader.pipeline import sec_ticker
from agent_trader.sec import is_financial_sic


def series(values):
    start = date(2025, 1, 1)
    return pd.Series(values, index=[start + timedelta(days=i) for i in range(len(values))])


def test_regime_needs_200_closes():
    assert prices.regime(series([100.0] * 199)) is None


def test_regime_above_and_below():
    up = prices.regime(series([100.0] * 199 + [120.0]))
    assert up.above and up.sma200 == pytest.approx(100.1)
    down = prices.regime(series([100.0] * 199 + [80.0]))
    assert not down.above


def test_annualized_vol():
    assert prices.annualized_vol(series([100.0] * 252)) is None
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.01, 252)
    closes = series(100 * np.exp(np.concatenate([[0], np.cumsum(rets)])))
    expected = rets.std(ddof=1) * np.sqrt(252)
    assert prices.annualized_vol(closes) == pytest.approx(expected)


def test_financial_sic():
    assert is_financial_sic("6021") and is_financial_sic(6798)
    assert not is_financial_sic("3571") and not is_financial_sic(None)


def test_sec_ticker():
    assert sec_ticker("BRK.B") == "BRK-B"


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_daily_bars_follows_pagination():
    pages = [
        {"bars": {"SPY": [{"t": "2026-10-16T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]},
         "next_page_token": "abc"},
        {"bars": {"SPY": [{"t": "2026-10-19T04:00:00Z", "o": 2, "h": 3, "l": 1.5, "c": 2.5, "v": 20}]},
         "next_page_token": None},
    ]
    seen = []

    client = AlpacaClient("k", "s")

    def fake_get(url, params=None, timeout=None):
        seen.append(dict(params))
        return FakeResp(pages[len(seen) - 1])

    client.session.get = fake_get
    df = client.daily_bars(["SPY"], start=date(2026, 10, 1))
    assert list(df["close"]) == [1.5, 2.5]
    assert df["date"].iloc[-1] == date(2026, 10, 19)
    assert seen[1]["page_token"] == "abc"
    assert seen[0]["adjustment"] == "all"
