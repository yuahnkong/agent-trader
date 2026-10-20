"""Paths, endpoints and credentials. Everything environment-dependent lives here."""

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
# Everything below is committed: the containers that run the schedule are thrown away
# after each run, so the repo is the only memory between days.
STATE_DIR = ROOT / "state"
RUNS_DIR = ROOT / "runs"
REPORTS_DIR = ROOT / "reports"
LOGS_DIR = ROOT / "logs"

MARKET_TZ = ZoneInfo("America/New_York")


def market_today() -> date:
    """Today's date in New York. The container clock is UTC, which rolls over at 8pm ET."""
    return datetime.now(MARKET_TZ).date()

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

# Hard limits (DESIGN.md). Agents read these; only code changes them.
MAX_POSITION_PCT = 0.15
MAX_TRADES_PER_DAY = 3
WASH_SALE_DAYS = 30
KILL_SWITCH_DRAWDOWN = 0.15
TARGET_POSITIONS = 9  # inverse-vol sizing is centred on 1/9 of equity per name
MIN_ORDER_USD = 1.00  # Alpaca's floor for notional (fractional) orders

# Screen (DESIGN.md "Candidate menu").
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20_000_000  # median daily $ volume over the last 60 sessions
GP_TOP_PCT = 0.20
MIN_FSCORE = 7
MAX_FUNDAMENTALS_AGE_DAYS = 550  # latest 10-K must cover a fiscal year ending within ~18 months
MENU_MAX_AGE_DAYS = 7

# Shadow portfolio: rules-only, same menu, no agents.
SHADOW_POSITIONS = 8


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
