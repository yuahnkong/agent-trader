# agent-trader

A paper-trading experiment: seven Claude agents debate each trade, a Python risk layer enforces hard limits, and orders go to an Alpaca paper account.

See [DESIGN.md](DESIGN.md) for the full plan.

## Setup

```
pip install -r requirements-dev.txt
python -m pytest
```

Set `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` (paper keys) in the environment, then:

```
python -m agent_trader.pipeline check-alpaca   # credentials, cash-only config, data feed
python -m agent_trader.pipeline daily --symbols AAPL KO   # pre-open pull -> data/snapshots/
```

`check-alpaca` warns if the paper account isn't set up the way DESIGN.md requires
(equity near $600, `max_margin_multiplier` = 1, shorting disabled).
