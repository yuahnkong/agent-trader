"""Price-derived measures: the 200-day regime filter and 252-day volatility."""

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass(frozen=True)
class Regime:
    as_of: date
    close: float
    sma200: float

    @property
    def above(self) -> bool:
        return self.close > self.sma200

    @property
    def pct_from_sma(self) -> float:
        return self.close / self.sma200 - 1


def regime(closes: pd.Series) -> Regime | None:
    """Latest close vs. its 200-day simple moving average. `closes` is indexed by date.
    Returns None with fewer than 200 closes rather than guessing."""
    closes = closes.dropna().sort_index()
    if len(closes) < 200:
        return None
    return Regime(
        as_of=closes.index[-1],
        close=float(closes.iloc[-1]),
        sma200=float(closes.iloc[-200:].mean()),
    )


def annualized_vol(closes: pd.Series, window: int = TRADING_DAYS) -> float | None:
    """Annualized std. dev. of the last `window` daily log returns; None if history is short."""
    closes = closes.dropna().sort_index()
    if len(closes) < window + 1:
        return None
    rets = np.log(closes.iloc[-(window + 1) :]).diff().dropna()
    return float(rets.std(ddof=1) * np.sqrt(TRADING_DAYS))


def closes_by_symbol(bars: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        sym: g.set_index("date")["close"].sort_index()
        for sym, g in bars.groupby("symbol", sort=False)
    }
