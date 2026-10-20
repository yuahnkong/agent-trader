---
name: postclose
description: Run agent-trader's post-close cycle - pull fills and P&L, update the shadow portfolio and SPY benchmark, have the analysts and Portfolio Manager review the day, write the close report, commit and push. Use for "/postclose", "how did today go", or a scheduled 4:15 ET run.
---

# Post-close cycle

Run from the repo root. `D` is today's date in New York (`TZ=America/New_York date +%F`),
`R` is `runs/D`.

1. **Setup.** Same as pre-open: requirements installed, Alpaca keys set.

2. **Data.** `python -m agent_trader.pipeline close`. It writes `R/close.json` (fills,
   positions, P&L, open theses), rebalances the shadow portfolio if the menu changed,
   records agents vs. shadow vs. SPY in `state/performance.json`, and checks the kill
   switch. If it reports the market was closed today and nothing changed, commit state
   and stop.

3. **Reviews (parallel).** In one message launch `market-analyst`, `fundamentals-analyst`,
   `news-analyst` and `portfolio-manager`, each with the prompt
   "Run directory: R. Post-close review mode: write R/review/<your name>.json."
   Skip the reviews if there are no positions and no fills today; say so in the report.

4. **Report.** `python -m agent_trader.pipeline close-report` writes `reports/D-close.md`.

5. **Commit and push.** `git add runs/D state reports logs && git commit -m "Post-close run D"`,
   then `git push -u origin HEAD` (retry network failures up to 4 times with backoff).

6. **Tell the user.** Agents vs. shadow vs. SPY since start, day P&L, any broken theses,
   any kill-switch action. If a PushNotification tool is available, send it in one line.
