from datetime import date

from agent_trader import config
from agent_trader.fundamentals import AnnualSnapshot
from agent_trader.packets import estimate_next_earnings
from agent_trader.scoring import gross_profitability, piotroski, sector_for_sic
from agent_trader.shadow import rebalance, target_weights
from agent_trader.state import Shadow


def snap(current, prior):
    return AnnualSnapshot(1, "T", date(2026, 3, 1), date(2025, 12, 31), date(2024, 12, 31),
                          current=current, prior=prior)


GOOD = dict(net_income=12, total_assets=100, cfo=15, long_term_debt=10, current_assets=50,
            current_liabilities=20, shares_basic=100, revenue=80, gross_profit=40)
WORSE = dict(net_income=8, total_assets=100, cfo=9, long_term_debt=20, current_assets=40,
             current_liabilities=20, shares_basic=105, revenue=70, gross_profit=30)


def test_piotroski_perfect_score_and_missing_data_scores_zero():
    f = piotroski(snap(GOOD, WORSE))
    assert f.score == 9 and f.computable == 9
    f = piotroski(snap({**GOOD, "cfo": None}, WORSE))
    assert f.score == 7 and f.computable == 7  # both cash-flow tests drop out


def test_debt_free_both_years_passes_leverage():
    f = piotroski(snap({**GOOD, "long_term_debt": None}, {**WORSE, "long_term_debt": None}))
    assert f.tests["leverage_not_up"] is True


def test_gross_profitability():
    assert gross_profitability(snap(GOOD, WORSE)) == 0.4


def test_sector_mapping_narrow_ranges_win():
    assert sector_for_sic(2834) == "Health Care"  # pharma, inside chemicals 28xx
    assert sector_for_sic(2080) == "Consumer Staples"
    assert sector_for_sic(7372) == "Technology"  # software, inside business services
    assert sector_for_sic(7389) == "Industrials"
    assert sector_for_sic(5331) == "Consumer Staples"  # Walmart, not discretionary retail
    assert sector_for_sic(4955) == "Industrials"  # waste services, not utilities
    assert sector_for_sic(None) is None


def test_earnings_estimate_prefers_item_202():
    forms = [
        {"form": "8-K", "filed": "2025-10-21", "items": "2.02,9.01"},
        {"form": "10-Q", "filed": "2025-10-23", "items": ""},
    ]
    assert estimate_next_earnings(forms, date(2026, 10, 1)) == "2026-10-20"


MENU = {"as_of": "2026-10-19", "menu": [
    {"symbol": "A", "vol_252d": 0.10}, {"symbol": "B", "vol_252d": 0.20}, {"symbol": "C", "vol_252d": 0.40},
]}


def test_target_weights_inverse_vol_and_capped():
    w = target_weights(MENU["menu"])
    assert w["A"] == config.MAX_POSITION_PCT
    assert w["A"] >= w["B"] >= w["C"]


def test_shadow_rebalance_once_per_menu_and_regime_blocks_buys():
    s = Shadow(started="2026-10-19", cash=600.0)
    closes = {"A": 10.0, "B": 20.0, "C": 40.0}
    assert rebalance(s, MENU, closes, regime_above=False, today=date(2026, 10, 19)) == []
    assert s.cash == 600.0

    s = Shadow(started="2026-10-19", cash=600.0)
    trades = rebalance(s, MENU, closes, regime_above=True, today=date(2026, 10, 19))
    assert {t["symbol"] for t in trades} == {"A", "B", "C"}
    assert abs(s.value(closes) - 600.0) < 1e-6
    assert rebalance(s, MENU, closes, regime_above=True, today=date(2026, 10, 20)) == []

    new_menu = {"as_of": "2026-10-26", "menu": [{"symbol": "B", "vol_252d": 0.20}]}
    trades = rebalance(s, new_menu, closes, regime_above=False, today=date(2026, 10, 26))
    assert {t["symbol"] for t in trades} == {"A", "C"} and set(s.shares) == {"B"}


SLEEVES = [
    {"symbol": "VOO", "asset_class": "etf", "kind": "equity", "vol_252d": 0.15},
    {"symbol": "BND", "asset_class": "etf", "kind": "defensive", "vol_252d": 0.05},
    {"symbol": "BTC/USD", "asset_class": "crypto", "kind": "crypto", "vol_252d": 0.45},
    {"symbol": "ETH/USD", "asset_class": "crypto", "kind": "crypto", "vol_252d": 0.65},
]


def test_shadow_split_with_sleeves():
    w = target_weights(MENU["menu"], SLEEVES)
    assert w["VOO"] == w["BND"] == config.MAX_POSITION_PCT  # 30% / 2 ETFs, capped at 15%
    assert abs(w["BTC/USD"] + w["ETH/USD"] - config.SHADOW_SPLIT["crypto"]) < 1e-9
    assert w["BTC/USD"] > w["ETH/USD"]


def test_shadow_regime_off_buys_only_defensive():
    s = Shadow(started="2026-10-19", cash=600.0)
    closes = {"A": 10.0, "B": 20.0, "C": 40.0, "VOO": 500.0, "BND": 70.0, "BTC/USD": 80000.0, "ETH/USD": 2400.0}
    trades = rebalance(s, MENU, closes, regime_above=False, today=date(2026, 10, 19), sleeves=SLEEVES)
    assert {t["symbol"] for t in trades} == {"BND"}


def test_forward_return_waits_for_sessions_after_the_call():
    import pandas as pd
    from agent_trader.learning import _forward
    s = pd.Series([100.0, 101.0, 102.0, 99.0], index=[date(2026, 10, d) for d in (19, 20, 21, 22)])
    r, n = _forward(s, date(2026, 10, 20))
    assert n == 2 and abs(r - (99 / 101 - 1)) < 1e-12
    r, n = _forward(s, date(2026, 10, 20), 1)
    assert n == 1 and abs(r - (102 / 101 - 1)) < 1e-12
    assert _forward(s, date(2026, 10, 22)) == (None, 0)  # nothing after the call yet
