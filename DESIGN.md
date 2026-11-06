# Agent Trader: Design

Paper-trading experiment: a multi-agent Claude system that makes daily trading
decisions inside hard, code-enforced risk limits. Goal is to learn whether LLM
debate adds anything over a plain rules-based version of the same strategy.

## Account

- Broker: Alpaca, **paper** account first, sized to ~$600 to match the real portfolio.
- Cash account only. No margin, no shorting, no options.
- Fractional shares, so ~8-12 positions of ~$40-90 each across stocks, ETFs and a little crypto.
- Target: mostly invested (~80-90%) within about a week of starting (owner's call, 2026-10-20).
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

## Beyond the screen (added 2026-10-20)

- ETF sleeve, fixed list: VOO, VTI, QQQ, VXUS, VWO, VNQ (equity); BND, IEF, TLT, GLD (defensive).
- Crypto: BTC/USD and ETH/USD through Alpaca crypto, capped at 5% combined. No fundamentals to
  argue over, 24/7 trading and ~3x stock volatility, so it stays a small satellite.
- Shadow portfolio uses a fixed split for the same assets: 65% top 8 stocks (inverse-vol),
  30% ETF sleeve (equal weight), 5% crypto (inverse-vol).

## Hard limits (Python, agents cannot override)

- Max position size: 15% of portfolio.
- Max trades per day: 5 (raised from 3 on 2026-10-20).
- Crypto (BTC, ETH) at most 5% of equity combined.
- No same-day buy-and-sell round trips (avoids pattern-day-trader issues).
- Wash-sale block: no buying a ticker within 30 days before/after selling it at a loss.
- Inverse-volatility sizing.
- Regime filter: S&P 500 below its 200-day MA blocks new buys of stocks, equity ETFs and crypto; bond and gold ETFs stay buyable (tested, not assumed).
- Kill switch: portfolio down 15% from peak moves everything to cash and pauses trading.
- Idempotent orders via `client_order_id`.

## Learning loop (added 2026-10-20)

Nothing is retrained; the agents get memory and a track record.

- `pipeline learn` (post-close) grades every past call once 5 sessions have passed:
  buys vs. SPY, PM rejections and serious Skeptic objections (right if the name lagged),
  the market stance vs. SPY's next 5 days, high event-risk flags (right if the name moved
  >3% vs. SPY). Written to `state/scorecard.json`.
- The News Analyst's flags and notable headlines are kept for 60 days in
  `state/memory/news.jsonl`, so later runs can see whether a story followed through.
- A Reviewer agent (8th, post-close) keeps `state/lessons.json`: at most 20 active lessons,
  each with cited evidence and a confidence level, retired when later results contradict
  them. Changes to limits, the screen or the asset list go to `owner_suggestions` for a
  person to decide.
- Every agent reads `inputs/learning.json` (active lessons, its graded calls, news memory)
  before it works.

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
