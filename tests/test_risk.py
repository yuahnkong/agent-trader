from datetime import date

from agent_trader import config
from agent_trader.ledger import build_ledger
from agent_trader.risk import Context, Position, max_position_usd, review

TODAY = date(2026, 10, 21)


def ctx(**over):
    base = dict(
        today=TODAY,
        equity=600.0,
        buying_power=600.0,
        peak_equity=600.0,
        paused=False,
        regime_above_sma=True,
        positions={},
        menu_vol={"KO": 0.18, "JNJ": 0.18, "LOW": 0.36},
        median_vol=0.18,
        orders_today=[],
    )
    base.update(over)
    return Context(**base)


def buy(sym, usd):
    return {"symbol": sym, "side": "buy", "notional_usd": usd}


def sell(sym, fraction=1.0):
    return {"symbol": sym, "side": "sell", "fraction": fraction}


def test_buy_within_cap_passes_unchanged():
    [v] = review([buy("KO", 60)], ctx())
    assert v.approved and v.notional == 60 and not v.resized


def test_buy_resized_to_inverse_vol_cap():
    # median-vol name: 1/9 of $600 = $66.66; twice the vol halves it.
    assert round(max_position_usd(ctx(), "KO"), 2) == 66.67
    [v] = review([buy("LOW", 100)], ctx())
    assert v.approved and v.resized and v.notional == 33.33


def test_cap_never_exceeds_max_position_pct():
    c = ctx(menu_vol={"KO": 0.05}, median_vol=0.18)
    assert max_position_usd(c, "KO") == 600 * config.MAX_POSITION_PCT


def test_existing_holding_counts_against_cap():
    c = ctx(positions={"KO": Position(1, 60.0, 0.0)})
    [v] = review([buy("KO", 60)], c)
    assert v.approved and v.notional == 6.66


def test_off_menu_buy_rejected():
    [v] = review([buy("TSLA", 50)], ctx())
    assert not v.approved and "menu" in v.reasons[0]


def test_regime_filter_blocks_buys_not_sells():
    c = ctx(regime_above_sma=False, positions={"KO": Position(2, 120.0, 5.0)})
    b, s = sorted(review([buy("JNJ", 50), sell("KO")], c), key=lambda v: v.side)
    assert not b.approved and "200-day" in b.reasons[0]
    assert s.approved and s.qty == "2"


def test_unknown_regime_is_risk_off():
    [v] = review([buy("KO", 50)], ctx(regime_above_sma=None))
    assert not v.approved


def earlier_orders(n):
    return [{"symbol": f"X{i}", "side": "buy"} for i in range(n)]


def test_daily_trade_limit_counts_earlier_orders():
    c = ctx(orders_today=earlier_orders(config.MAX_TRADES_PER_DAY - 1))
    a, b = review([buy("KO", 20), buy("JNJ", 20)], c)
    assert a.approved and not b.approved and f"max {config.MAX_TRADES_PER_DAY}" in b.reasons[0]


def test_sells_judged_before_buys():
    c = ctx(
        orders_today=earlier_orders(config.MAX_TRADES_PER_DAY - 1),
        positions={"PEP": Position(1, 50.0, 1.0)},
    )
    verdicts = {v.symbol: v for v in review([buy("KO", 20), sell("PEP")], c)}
    assert verdicts["PEP"].approved and not verdicts["KO"].approved


def test_no_shorting():
    [v] = review([sell("KO")], ctx())
    assert not v.approved and "shorting" in v.reasons[0]


def test_same_day_round_trip_blocked_both_ways():
    c = ctx(
        positions={"KO": Position(1, 60.0, 0.0)},
        orders_today=[{"symbol": "KO", "side": "buy"}, {"symbol": "JNJ", "side": "sell"}],
    )
    s, b = review([sell("KO"), buy("JNJ", 20)], c)
    assert not s.approved and not b.approved


def test_wash_sale_blocks_rebuy_after_loss():
    c = ctx(last_loss_sale={"KO": date(2026, 10, 1)})
    [v] = review([buy("KO", 20)], c)
    assert not v.approved and "wash-sale" in v.reasons[0]
    [v] = review([buy("KO", 20)], ctx(last_loss_sale={"KO": date(2026, 9, 1)}))
    assert v.approved


def test_partial_loss_sale_after_recent_buy_blocked_full_sale_allowed():
    c = ctx(positions={"KO": Position(2, 100.0, -10.0)}, last_buy={"KO": date(2026, 10, 10)})
    [v] = review([sell("KO", 0.5)], c)
    assert not v.approved and "wash sale" in v.reasons[0]
    [v] = review([sell("KO", 1.0)], c)
    assert v.approved


def test_cash_limits_total_buys():
    a, b = review([buy("KO", 60), buy("JNJ", 60)], ctx(buying_power=80))
    assert a.notional == 60 and b.notional == 20 and b.resized


def test_kill_switch_and_pause_block_everything():
    c = ctx(equity=500, peak_equity=600, positions={"KO": Position(1, 60.0, 0.0)})
    assert all(not v.approved for v in review([buy("KO", 10), sell("KO")], c))
    assert all(not v.approved for v in review([buy("KO", 10)], ctx(paused=True)))


def test_duplicate_trade_in_decision_rejected():
    a, b = review([buy("KO", 10), buy("KO", 10)], ctx())
    assert a.approved and not b.approved


def test_ledger_fifo_and_loss_detection():
    fills = [
        {"symbol": "KO", "side": "buy", "qty": "1", "price": "60", "transaction_time": "2026-09-01T14:00:00Z"},
        {"symbol": "KO", "side": "buy", "qty": "1", "price": "70", "transaction_time": "2026-09-15T14:00:00Z"},
        {"symbol": "KO", "side": "sell", "qty": "1.5", "price": "65", "transaction_time": "2026-10-01T14:00:00Z"},
    ]
    led = build_ledger(fills)
    assert [round(r.pnl, 2) for r in led.realized] == [5.0, -2.5]
    assert led.last_loss_sale("KO") == date(2026, 10, 1)
    assert led.lots["KO"][0].qty == 0.5 and led.lots["KO"][0].price == 70
    assert led.last_buy("KO") == date(2026, 9, 15)


def sleeve_ctx(**over):
    base = dict(
        menu_vol={"KO": 0.18, "VOO": 0.15, "BND": 0.06, "BTC/USD": 0.45, "ETH/USD": 0.65},
        asset_class={"KO": "stock", "VOO": "etf", "BND": "etf", "BTC/USD": "crypto", "ETH/USD": "crypto"},
        defensive={"BND"},
    )
    base.update(over)
    return ctx(**base)


def test_crypto_capped_at_five_percent_combined():
    a, b = review([buy("BTC/USD", 25), buy("ETH/USD", 25)], sleeve_ctx())
    assert a.notional == 25 and b.notional == 5 and b.resized  # $30 = 5% of $600
    held = sleeve_ctx(positions={"BTC/USD": Position(0.0003, 30.0, 0.0)})
    [v] = review([buy("ETH/USD", 10)], held)
    assert not v.approved and "crypto room" in v.reasons[0]


def test_regime_off_allows_only_defensive_etfs():
    c = sleeve_ctx(regime_above_sma=False)
    verdicts = {v.symbol: v for v in review([buy("VOO", 20), buy("BND", 20), buy("BTC/USD", 5)], c)}
    assert verdicts["BND"].approved
    assert not verdicts["VOO"].approved and not verdicts["BTC/USD"].approved


def test_canonical_crypto_symbol():
    assert config.canonical_symbol("BTCUSD") == "BTC/USD" and config.canonical_symbol("KO") == "KO"
