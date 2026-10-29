"""Committed state that has to survive between runs: kill-switch status, peak equity,
the shadow portfolio, and the daily performance series. Everything else is rebuilt
from Alpaca on each run."""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import config

TRADING_PATH = config.STATE_DIR / "trading.json"
SHADOW_PATH = config.STATE_DIR / "shadow.json"
PERFORMANCE_PATH = config.STATE_DIR / "performance.json"


def _read(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str) + "\n")


@dataclass
class Trading:
    started: str | None = None  # first day the experiment ran
    start_equity: float | None = None
    spy_start_close: float | None = None
    peak_equity: float = 0.0
    paused: bool = False
    paused_reason: str | None = None
    paused_at: str | None = None

    @classmethod
    def load(cls) -> "Trading":
        return cls(**_read(TRADING_PATH, {}))

    def save(self) -> None:
        _write(TRADING_PATH, asdict(self))

    def start_if_needed(self, today: date, equity: float, spy_close: float | None) -> None:
        if self.started is None:
            self.started, self.start_equity, self.spy_start_close = today.isoformat(), equity, spy_close
        if self.spy_start_close is None and spy_close is not None:
            self.spy_start_close = spy_close
        self.peak_equity = max(self.peak_equity, equity)


@dataclass
class Shadow:
    """Rules-only twin: same menu and limits, no agents. Trades at the close, on paper
    inside the repo; it never touches Alpaca."""

    started: str | None = None
    cash: float = 0.0
    shares: dict[str, float] = field(default_factory=dict)
    last_menu: str | None = None  # menu as_of the shadow last rebalanced to
    trades: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Shadow":
        return cls(**_read(SHADOW_PATH, {}))

    def save(self) -> None:
        _write(SHADOW_PATH, asdict(self))

    def value(self, closes: dict[str, float]) -> float:
        return self.cash + sum(q * closes[s] for s, q in self.shares.items() if s in closes)


def load_performance() -> list[dict]:
    return _read(PERFORMANCE_PATH, [])


def record_performance(row: dict) -> list[dict]:
    rows = [r for r in load_performance() if r["date"] != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    save_performance(rows)
    return rows


def save_performance(rows: list[dict]) -> None:
    _write(PERFORMANCE_PATH, rows)
