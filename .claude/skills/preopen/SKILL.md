---
name: preopen
description: Run agent-trader's pre-open cycle - pull data, run the seven-agent debate, pass the decision through the risk layer, send paper orders to Alpaca, write the open report, commit and push. Use for "/preopen", "run the morning trade", or a scheduled 9:15 ET run.
---

# Pre-open cycle

Run from the repo root. `D` below is today's date in New York (`TZ=America/New_York date +%F`),
and `R` is `runs/D`. Stop and report if any step fails; never hand-edit a decision to get
past the risk layer.

**Launching an agent.** Use the agent's own type (e.g. `subagent_type: market-analyst`).
If the session reports that type as not found (agent files added after the session
started aren't loaded), use `general-purpose` with this prompt prefix instead:
"Your role is defined in .claude/agents/<name>.md. Read that file first and follow it
exactly (ignore its YAML frontmatter). Use only file reading and writing; do not run
shell commands, place orders, or read any input the role file doesn't list."

1. **Setup.** `pip install -q -r requirements.txt` if pandas is missing. Check that
   `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are set; if not, stop and say so.
   Run `python -m agent_trader.pipeline check-alpaca`. Fix-able config drift (margin or
   shorting) gets `check-alpaca --fix`; anything else, stop.

2. **Data.** `python -m agent_trader.pipeline prepare`. It re-runs the weekly screen if
   `state/menu.json` is a week old (about 10 minutes). If it prints that the market is
   closed today, write nothing further, commit any state changes, and stop.
   If it prints PAUSED, skip to step 6 with no decision file: the kill switch is on and
   only `pipeline resume`, run by a person, turns it off.

3. **Research (parallel).** In a single message, launch three Agent calls:
   `market-analyst`, `fundamentals-analyst`, `news-analyst`, each with the prompt
   "Run directory: R. Write your note." Wait for all three. Check that each wrote
   valid JSON to `R/agents/<name>.json`; re-run an agent once if its file is missing or
   invalid.

4. **Proposal.** Launch `proposer` with "Run directory: R."

5. **Challenge (parallel), then decision.** In one message launch `skeptic` and
   `implicator` with "Run directory: R." Then launch `portfolio-manager` with
   "Run directory: R. Write R/decision.json."
   Validate that `R/decision.json` is JSON with a `trades` list.

6. **Risk layer and orders.** `python -m agent_trader.pipeline execute`. It enforces the
   hard limits, sends approved orders (idempotent `client_order_id`, so a re-run won't
   double up), and writes `reports/D-open.md`. Don't retry rejected trades.


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

7. **Commit and push.** `git add runs/D* state reports logs && git commit -m "Pre-open run D"`,
   then push the current branch (`git push -u origin HEAD`, retrying network failures up to
   4 times with backoff).

8. **Tell the user.** Reply with the headline: stance, trades approved/rejected by the
   PM and by the risk layer, order statuses, equity. If a PushNotification tool is
   available, send the same in one line.
