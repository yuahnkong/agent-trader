---
name: refresh-dashboard
description: Upload fresh data to agent-trader's Agent Trader Desk dashboard (account, positions, trades, debate, menu, lessons, scorecard). Used at the end of /preopen and /postclose, and on its own by the hourly refresh task.
---

# Refresh the dashboard

The Agent Trader Desk
dashboard is https://claude.ai/artifact/7Xjbz2oNcyLmMLznWe7q6R. Run
`python -m agent_trader.pipeline dashboard-data`, which writes eight files to
`data/dashboard/`: account, performance, positions, trades, agents, menu, lessons, scorecard. For each one,
upload it to that URL with the Artifact tool (`asset: true`, `file_path` the JSON file,
one call per file), then with ArtifactData `get` collection `datasets`, doc `<name>` and
`update` it (pinned with `if_version`) with
`{"source": {"kind": "file", "url": <the upload's /_blob/... url>, "name": "<name>.json"}, "updated": {"at": <now ISO>, "by": "Claude (agent-trader pipeline)"}}`.
Then delete each replaced asset (Artifact `action: "delete"`, `path` = the old blob id).
The Artifact and ArtifactData tools are often deferred: load them first with ToolSearch,
query `select:Artifact,ArtifactData`. Do this step whenever they load; it is part of the
run, not optional. Skip it only if that search returns neither tool, and then say in the
summary exactly what the search returned.

This only reads the account and the repo: it never trades, and it doesn't change anything
that needs committing.
