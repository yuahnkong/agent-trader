"""Paths, endpoints and credentials. Everything environment-dependent lives here."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# Paper only. There is deliberately no setting that points this at the live API.
ALPACA_TRADING_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_URL = "https://data.alpaca.markets"
# "sip" is consolidated tape; free accounts may use it for anything older than 15 min,
# which covers pre-open pulls of prior-day bars. Set ALPACA_DATA_FEED=iex to fall back.
ALPACA_DATA_FEED = os.environ.get("ALPACA_DATA_FEED", "sip")

# SEC requires a descriptive User-Agent with contact info, and caps clients at 10 req/s.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "agent-trader research yuahnkong@gmail.com")
SEC_MAX_REQUESTS_PER_SEC = 8

# The S&P 500 index itself isn't on Alpaca; SPY tracks it closely enough for a 200-day MA test.
MARKET_PROXY = "SPY"


class MissingCredentials(RuntimeError):
    pass


def alpaca_credentials() -> tuple[str, str]:
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise MissingCredentials(
            "APCA_API_KEY_ID and APCA_API_SECRET_KEY must be set (paper account keys)."
        )
    return key, secret
