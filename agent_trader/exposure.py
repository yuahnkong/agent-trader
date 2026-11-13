"""Portfolio exposure by sector and sleeve, computed in Python so agents don't add up
weights by hand (lesson L3 kept catching mistakes there).

Stock sectors come from SIC codes via the screen, which is coarse: it files Domino's
under Consumer Staples and MSCI under Industrials. The screen ranks within those SIC
sectors and must stay as it is, but for exposure the obvious misfits among names the
account has held are overridden with their usual (GICS-style) sector. Both labels are
reported so agents can see when they differ.
"""

from . import config

SECTOR_OVERRIDES = {
    "DPZ": "Consumer Discretionary",  # restaurants; SIC 5812 lands in Staples
    "MSCI": "Financials",  # index and data provider; SIC 7389
    "FAST": "Industrials",  # industrial distributor; SIC 5200
    "MSM": "Industrials",  # industrial distributor
    "CARG": "Communication",  # online marketplace
    "VRSN": "Technology",
}

ETF_BUCKETS = {
    "VOO": "US broad equity ETF", "VTI": "US broad equity ETF", "QQQ": "US large-cap growth ETF",
    "VXUS": "International equity ETF", "VWO": "International equity ETF",
    "VNQ": "Real Estate", "BND": "Bonds", "IEF": "Bonds", "TLT": "Bonds", "GLD": "Gold",
}


def bucket(symbol: str, menu_sector: str | None) -> str:
    if symbol in config.CRYPTO:
        return "Crypto"
    if symbol in ETF_BUCKETS:
        return ETF_BUCKETS[symbol]
    return SECTOR_OVERRIDES.get(symbol) or menu_sector or "Unknown"


def exposure(positions: dict[str, float], equity: float, menu_sectors: dict[str, str]) -> dict:
    """positions: symbol -> market value. Weights are fractions of equity."""
    if equity <= 0:
        return {}
    by: dict[str, float] = {}
    rows = []
    for sym, mv in sorted(positions.items(), key=lambda kv: -kv[1]):
        b = bucket(sym, menu_sectors.get(sym))
        by[b] = by.get(b, 0.0) + mv
        rows.append({"symbol": sym, "bucket": b, "screen_label": menu_sectors.get(sym),
                     "weight": round(mv / equity, 4)})
    invested = sum(positions.values())
    us_etf = sum(mv for s, mv in positions.items() if ETF_BUCKETS.get(s, "").startswith("US "))
    return {
        "invested_pct": round(invested / equity, 4),
        "cash_pct": round(1 - invested / equity, 4),
        "by_bucket": {k: round(v / equity, 4) for k, v in sorted(by.items(), key=lambda kv: -kv[1])},
        "us_index_etfs_pct": round(us_etf / equity, 4),
        "largest": rows[:3],
        "positions": rows,
        "note": "bucket = sector used for exposure (overrides fix known SIC misfits); screen_label = the "
                "SIC sector the screen ranks within. VOO, VTI and QQQ overlap heavily with each other and "
                "with large held stocks.",
    }
