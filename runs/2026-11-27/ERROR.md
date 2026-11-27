# Post-close 2026-11-27: not run

Step 1 (setup) failed: `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` are not set in the
scheduled post-close session's environment (`agent_trader/config.py:alpaca_credentials`
would raise `MissingCredentials`). `pandas` was also missing and `pip install -r requirements.txt`
did not make it importable.

Nothing was pulled: no `close.json`, no shadow/SPY update, no scorecard, no reviews, no
close report. Today's pre-open did place orders (e.g. DPZ sell, order f3223578…), so fills
are pending reconciliation.

Fix: add the paper keys to the post-close routine's environment, then re-run
`/postclose` for 2026-11-27 from an interactive session (the close step reads Alpaca's
history, so it can be caught up after the fact).
