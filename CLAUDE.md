# agent-trader

Paper-trading experiment: seven Claude subagents debate trades, a Python risk layer
enforces hard limits, orders go to an Alpaca **paper** account. DESIGN.md is the plan.

## Running it

- `/preopen` runs the morning cycle (data, debate, risk layer, orders, report, push).
- `/postclose` runs the evening cycle (P&L, shadow portfolio, reviews, report, push).
- `python -m agent_trader.pipeline status` shows equity, positions, open orders, menu
  and agents vs. shadow vs. SPY.
- `python -m agent_trader.pipeline execute --dry-run` shows what the risk layer would
  do with `runs/<today>/decision.json` without sending orders.
- `python -m agent_trader.pipeline screen` rebuilds the candidate menu (~10 minutes).
- `python -m agent_trader.pipeline resume` clears a kill-switch pause. Only run it when
  the user asks.

## Rules for working in this repo

- Never place orders except through `pipeline execute`, and never edit a decision file
  to get a trade past the risk layer. Limits live in `agent_trader/config.py`; change
  them only when the user asks.
- `state/`, `runs/`, `reports/` and `logs/` are the system's memory between scheduled
  runs (containers are thrown away). Commit them after every run.
- The trading API URL is hard-coded to paper. Don't add a setting for live trading.
- Run `python -m pytest` before committing code changes.
- Results only compare within a system version. Any change to what the agents see or may
  do, the hard limits, the screen or the shadow's rules adds an entry to `config.VERSIONS`,
  starting at the first run it applies to.
