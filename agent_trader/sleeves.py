"""The ETF and crypto sleeves: fixed lists (config.ETF_SLEEVE, config.CRYPTO) with the same
price-derived numbers the stock menu has, so the risk layer can size them the same way."""

import numpy as np
import pandas as pd

from . import config, prices


def _vol(closes: pd.Series, days: int) -> float | None:
    closes = closes.dropna().sort_index()
    if len(closes) < 61:
        return None
    window = min(len(closes) - 1, days)
    rets = np.log(closes.iloc[-(window + 1):]).diff().dropna()
    return float(rets.std(ddof=1) * np.sqrt(days))


def _ret(s: pd.Series, n: int) -> float | None:
    return None if len(s) <= n else round(float(s.iloc[-1] / s.iloc[-1 - n] - 1), 4)


def sleeve_rows(closes: dict[str, pd.Series]) -> list[dict]:
    rows = []
    for sym, (label, kind) in config.ETF_SLEEVE.items():
        rows.append(("etf", kind, sym, label))
    for sym, label in config.CRYPTO.items():
        rows.append(("crypto", "crypto", sym, label))
    out = []
    for cls, kind, sym, label in rows:
        s = closes.get(sym)
        if s is None or s.empty:
            continue
        days = config.CRYPTO_TRADING_DAYS if cls == "crypto" else prices.TRADING_DAYS
        sma = float(s.iloc[-200:].mean()) if len(s) >= 200 else None
        out.append({
            "symbol": sym, "name": label, "asset_class": cls, "kind": kind,
            "close": round(float(s.iloc[-1]), 4), "vol_252d": _vol(s, days),
            "ret_21d": _ret(s, 21), "ret_63d": _ret(s, 63),
            "pct_from_sma200": None if sma is None else round(float(s.iloc[-1]) / sma - 1, 4),
        })
    return [r for r in out if r["vol_252d"]]


def sleeve_symbols() -> list[str]:
    return [*config.ETF_SLEEVE, *config.CRYPTO]
