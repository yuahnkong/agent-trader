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
python -m agent_trader.pipeline check-alpaca --fix   # credentials, cash-only config, data feed
python -m agent_trader.pipeline screen               # build state/menu.json (~10 min, weekly)
```

## Daily cycle

In a Claude Code session in this repo, `/preopen` runs the morning cycle and `/postclose` the evening one. Scheduled tasks run the same skills at 9:15 and 16:15 ET on weekdays.

| Step | Command | Who |
|---|---|---|
| Data, screen refresh, agent packets | `pipeline prepare` | Python |
| Market, Fundamentals, News analysts | `.claude/agents/*-analyst.md` | Claude, in parallel |
| Proposer, then Skeptic + Implicator, then Portfolio Manager | `.claude/agents/` | Claude |
| Hard limits, orders, open report | `pipeline execute` | Python |
| Fills, P&L, shadow portfolio, SPY | `pipeline close` | Python |
| Reviews, close report | `pipeline close-report` | Claude, then Python |

Other commands: `pipeline status`, `pipeline execute --dry-run`, `pipeline resume` (clears a kill-switch pause).

## Where things land

- `runs/YYYY-MM-DD/`: every input packet, each agent's JSON output, the decision, what the risk layer did.
- `reports/YYYY-MM-DD-open.md`, `-close.md`: human-readable summaries.
- `logs/recommendations.jsonl`: one line per agent per day, for scoring agents later.
- `state/`: the menu, kill-switch status and peak equity, the shadow portfolio, the performance series.

## Known limitations

- Only SEC and Alpaca are reachable from the run environment, so VIX, Treasury yields and credit spreads are ETF proxies (VIXY, SHY/IEF/TLT, HYG/LQD).
- Earnings dates are estimates (last year's results 8-K + 364 days).
- Sectors come from SIC codes, which are coarser than GICS.
- The F-score uses year-end assets for ROA and treats a missing debt tag as no debt.
- The shadow portfolio rebalances at the close when a new weekly menu appears, ignores the 3-trades-a-day limit, and pays no spread.
