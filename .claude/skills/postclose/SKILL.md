---
name: postclose
description: Run agent-trader's post-close cycle - pull fills and P&L, update the shadow portfolio and SPY benchmark, have the analysts and Portfolio Manager review the day, write the close report, commit and push. Use for "/postclose", "how did today go", or a scheduled 4:15 ET run.
---

# Post-close cycle

Run from the repo root. `D` is today's date in New York (`TZ=America/New_York date +%F`),
`R` is `runs/D`.

**Launching an agent.** Use the agent's own type (e.g. `subagent_type: market-analyst`).
If the session reports that type as not found (agent files added after the session
started aren't loaded), use `general-purpose` with this prompt prefix instead:
"Your role is defined in .claude/agents/<name>.md. Read that file first and follow it
exactly (ignore its YAML frontmatter). Use only file reading and writing; do not run
shell commands, place orders, or read any input the role file doesn't list."

1. **Setup.** Same as pre-open: requirements installed, Alpaca keys set.

2. **Data.** `python -m agent_trader.pipeline close`. It writes `R/close.json` (fills,
   positions, P&L, open theses), rebalances the shadow portfolio if the menu changed,
   records agents vs. shadow vs. SPY in `state/performance.json`, and checks the kill
   switch. If it reports the market was closed today and nothing changed, commit state
   and stop.

2b. **Score.** `python -m agent_trader.pipeline learn` grades every past call against
   what prices did (`state/scorecard.json`) and saves today's flagged news to
   `state/memory/news.jsonl`.

3. **Reviews (parallel).** In one message launch `market-analyst`, `fundamentals-analyst`,
   `news-analyst` and `portfolio-manager`, each with the prompt
   "Run directory: R. Post-close review mode: write R/review/<your name>.json."
   Skip the reviews if there are no positions and no fills today; say so in the report.

3b. **Reflect.** Launch `reviewer` with "Run directory: R. Today is D." after the reviews
   finish (run it even when the reviews were skipped). It updates `state/lessons.json`,
   which every agent reads before the next run, and writes `R/review/reviewer.json`.

4. **Report.** `python -m agent_trader.pipeline close-report` writes `reports/D-close.md`.


**Refresh the dashboard** (after the report, before committing). The Agent Trader Desk
dashboard is https://claude.ai/artifact/7Xjbz2oNcyLmMLznWe7q6R. Run
`python -m agent_trader.pipeline dashboard-data`, which writes eight files to
`data/dashboard/`: account, performance, positions, trades, agents, menu, lessons, scorecard. For each one,
upload it to that URL with the Artifact tool (`asset: true`, `file_path` the JSON file,
one call per file), then with ArtifactData `get` collection `datasets`, doc `<name>` and
`update` it (pinned with `if_version`) with
`{"source": {"kind": "file", "url": <the upload's /_blob/... url>, "name": "<name>.json"}, "updated": {"at": <now ISO>, "by": "Claude (agent-trader pipeline)"}}`.
Then delete each replaced asset (Artifact `action: "delete"`, `path` = the old blob id).
If the Artifact or ArtifactData tools aren't available in this session, skip this step and
say so in the summary.

5. **Commit and push.** `git add runs/D* state reports logs && git commit -m "Post-close run D"`,
   then `git push -u origin HEAD` (retry network failures up to 4 times with backoff).

6. **Tell the user.** Agents vs. shadow vs. SPY since start, day P&L, any broken theses,
   any kill-switch action. If a PushNotification tool is available, send it in one line.
