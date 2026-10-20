# Agent Trader: Design

Paper-trading experiment: a multi-agent Claude system that makes daily trading
decisions inside hard, code-enforced risk limits. Goal is to learn whether LLM
debate adds anything over a plain rules-based version of the same strategy.

## Account

- Broker: Alpaca, **paper** account first, sized to ~$600 to match the real portfolio.
- Cash account only. No margin, no shorting, no options.
- Fractional shares, so ~8-10 positions of ~$60-75 each.
- Taxable account: favor holding winners past one year; avoid churn.

## Daily schedule (weekdays, scheduled tasks)

**Pre-open (~9:15 ET)**
1. Python pulls data: prior-day prices, S&P 500 vs 200-day MA, new SEC filings,
   headlines for holdings and candidates.
2. Python runs the screen to build the candidate menu.
3. Agents debate (see below) and output a JSON list of approved trades.
4. Python risk layer checks every trade; rejects anything that breaks a limit.
5. Orders go to Alpaca paper with a unique `client_order_id` per order.
6. Open report written.

**Post-close (~4:15 ET)**
1. Python pulls fills, positions, P&L.
2. Agents review the day and flag any broken theses.
3. Close report written, comparing agents vs. shadow portfolio vs. SPY.

## Agents (7)

Each agent is a Claude Code subagent defined in `.claude/agents/`. Each one gets
different inputs, so they bring different information rather than restating
each other.

Stage 1, research (run in parallel, without seeing each other's output):
1. **Market Analyst**: S&P 500 vs 200-day MA, VIX, Treasury yields, sector
   performance. Outputs a market stance (risk-on / neutral / risk-off) with
   reasons. Never proposes trades.
2. **Fundamentals Analyst**: the screened menu plus SEC filings. Ranks
   candidates and flags accounting red flags (restatements, auditor changes,
   accruals far above cash flow).
3. **News & Events Analyst**: headlines, 8-K filings, upcoming earnings dates
   for holdings and candidates. Flags event risk (e.g. earnings within 5 days).

Stage 2, proposal:
4. **Proposer**: reads all three research notes and proposes concrete trades
   (ticker, side, dollar amount, thesis, what would prove the thesis wrong).

Stage 3, challenge (run in parallel):
5. **Skeptic**: must make the strongest case against each proposal. Cannot
   simply say "looks fine."
6. **Implicator**: checks portfolio-level consequences: concentration, overlap
   with existing holdings, taxes (short- vs long-term), wash sales, and the
   portfolio's loss in a 10% market drop.

Stage 4, decision:
7. **Portfolio Manager**: the only agent that decides. Reads everything,
   then approves, resizes, or rejects each proposal, with written reasoning.
   Outputs the final JSON trade list.

Post-close: the three analysts update their notes, and the Portfolio Manager
writes a review of the day's decisions.

Agents choose only from the screened menu. They never calculate ratios and never
place orders directly. Python's hard limits apply after the Portfolio Manager.

Each agent's recommendation is logged separately so we can later measure which
agents' calls helped, and drop or rework the ones that don't.

## Candidate menu (from Gemini plan, corrected)

- Universe: liquid US common stocks, excluding financials (gross profitability
  is meaningless without COGS).
- Gross profitability = (Revenue - COGS) / Total Assets, top 20% within sector.
- Piotroski F-Score >= 7 (treated as a hypothesis; Piotroski built it for value stocks).
- Annualized 252-day volatility in the calmer half of the eligible universe.
- Data: free SEC EDGAR XBRL (point-in-time by filing date) + Alpaca market data.

## Hard limits (Python, agents cannot override)

- Max position size: 15% of portfolio.
- Max trades per day: 3.
- No same-day buy-and-sell round trips (avoids pattern-day-trader issues).
- Wash-sale block: no buying a ticker within 30 days before/after selling it at a loss.
- Inverse-volatility sizing.
- Regime filter: S&P 500 below its 200-day MA blocks new buys (tested, not assumed).
- Kill switch: portfolio down 15% from peak moves everything to cash and pauses trading.
- Idempotent orders via `client_order_id`.

## Evaluation

- **Shadow portfolio**: same menu, rules-only rebalance, no agents.
- Every report compares agents vs. shadow vs. SPY.
- Every debate and decision logged to the repo for later review.

## Reports

- Markdown files committed to the repo (`reports/YYYY-MM-DD-open.md`, `-close.md`).
- Phone notification with headline numbers.

## Setup status

- [x] GitHub repo created and connected
- [x] Alpaca paper account sized to $600, API keys generated, cash-only config applied (2026-10-20)
- [x] Environment network access: data.sec.gov, www.sec.gov,
      paper-api.alpaca.markets, data.alpaca.markets (verified 2026-10-20)
- [x] Environment variables: APCA_API_KEY_ID, APCA_API_SECRET_KEY
- [x] Pipeline, screen, risk layer, agents, reports, shadow portfolio (2026-10-20)
